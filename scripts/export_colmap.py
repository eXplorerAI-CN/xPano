# -*- coding: utf-8 -*-
import Metashape
import json
import os
import struct
import math
import concurrent.futures
import sys
import time
from pathlib import Path

try:
    import numpy as np
except Exception as exc:
    raise RuntimeError(
        "Metashape Python could not load NumPy. xPano's Metashape export requires "
        "Metashape's bundled NumPy runtime."
    ) from exc

try:
    from scripts.component_selection import (
        activated_component,
        inspect_components,
        resolve_component_key,
    )
except ImportError:
    from component_selection import (
        activated_component,
        inspect_components,
        resolve_component_key,
    )

try:
    from scripts.export_image_cache import (
        build_image_signature,
        empty_image_cache,
        load_image_cache,
        output_record,
        refresh_output_records,
        reuse_cached_outputs,
        source_record,
        write_image_cache,
    )
except ImportError:
    from export_image_cache import (
        build_image_signature,
        empty_image_cache,
        load_image_cache,
        output_record,
        refresh_output_records,
        reuse_cached_outputs,
        source_record,
        write_image_cache,
    )

try:
    from scripts.export_remap import RemapEngine, benchmark_remap_backends, remap_bilinear, select_remap_backend
except ImportError:
    from export_remap import RemapEngine, benchmark_remap_backends, remap_bilinear, select_remap_backend


IMAGE_CONTRACT_VERSION = "xpano-images-v2-fisheye-projection"
MIN_FISHEYE_REMAP_COVERAGE = 0.99
CALIBRATION_FIELDS = ("width", "height", "f", "cx", "cy", "k1", "k2", "k3", "k4", "p1", "p2", "b1", "b2")

# ==========================================
# 0. 基础工具与 COLMAP 二进制打包
# ==========================================
f32 = lambda x: bytes(struct.pack("f", x))
d64 = lambda x: bytes(struct.pack("d", x))
u8  = lambda x: x.to_bytes(1, "little", signed=(x < 0))
u32 = lambda x: x.to_bytes(4, "little", signed=(x < 0))
u64 = lambda x: x.to_bytes(8, "little", signed=(x < 0))
bstr = lambda x: bytes((x + "\0"), "utf-8")

def matrix_to_quat(m):
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if (tr > 0):
        s = 2 * math.sqrt(tr + 1)
        return Metashape.Vector([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s])
    if (m[0, 0] > m[1, 1]) and (m[0, 0] > m[2, 2]):
        s = 2 * math.sqrt(1 + m[0, 0] - m[1, 1] - m[2, 2])
        return Metashape.Vector([0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s])
    if (m[1, 1] > m[2, 2]):
        s = 2 * math.sqrt(1 + m[1, 1] - m[0, 0] - m[2, 2])
        return Metashape.Vector([(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s])
    else:
        s = 2 * math.sqrt(1 + m[2, 2] - m[0, 0] - m[1, 1])
        return Metashape.Vector([(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s])

def get_coord_transform(chunk, use_localframe=True):
    if not use_localframe: return Metashape.Matrix.Diag([1, 1, 1, 1])
    if not chunk.region: return chunk.transform.matrix
    fr_to_gc  = chunk.transform.matrix
    gc_to_loc = chunk.crs.localframe(fr_to_gc.mulp(chunk.region.center))
    fr_to_loc = gc_to_loc * fr_to_gc
    return (Metashape.Matrix.Translation(-fr_to_loc.mulp(chunk.region.center)) * fr_to_loc)

# ==========================================
# 1. 原版 Frame 去畸变系统
# ==========================================
def calib_valid(calib, point):
    reproj = calib.project(calib.unproject(point))
    if not reproj: return False
    return (reproj - point).norm() < 1.0

def rotate_vector(vec, axis, angle):
    axis = axis.normalized()
    collinear = axis * (vec * axis)
    orthogonal0 = vec - collinear
    orthogonal1 = Metashape.Vector.cross(axis, orthogonal0)
    return collinear + orthogonal0 * math.cos(angle) + orthogonal1 * math.sin(angle)

def axis_magnitude_rotation(axis):
    angle = axis.norm()
    axis = axis.normalized()
    x = Metashape.Vector((1, 0, 0))
    y = Metashape.Vector((0, 1, 0))
    z = Metashape.Vector((0, 0, 1))
    return Metashape.Matrix((rotate_vector(x, axis, -angle), rotate_vector(y, axis, -angle), rotate_vector(z, axis, -angle)))

def compute_size(top, right, bottom, left, T1):
    T1_inv = T1.inv()
    tl = T1_inv.mulp(Metashape.Vector([left, top, 1]))
    tr = T1_inv.mulp(Metashape.Vector([right, top, 1]))
    bl = T1_inv.mulp(Metashape.Vector([left, bottom, 1]))
    br = T1_inv.mulp(Metashape.Vector([right, bottom, 1]))

    halfwl = min(-tl.x / tl.z, -bl.x / bl.z)
    halfwr = min(tr.x / tr.z, br.x / br.z)
    halfht = min(-tr.y / tr.z, -tl.y / tl.z)
    halfhb = min(br.y / br.z, bl.y / bl.z)
    return (halfht, halfwr, halfhb, halfwl)

def get_valid_calib_region(calib):
    w, h = calib.width, calib.height
    left = right = math.floor(calib.cx + w / 2)
    top = bottom = math.floor(calib.cy + h / 2)
    left_set = right_set = top_set = bottom_set = False
    max_dim = max(w, h)
    max_tan = math.hypot(w, h) / calib.f
    step_x = 1 / min(1.2, (h / w)) if w <= h else 1
    step_y = 1 / min(1.2, (w / h)) if w > h else 1

    for r in range(max_dim):
        if left_set and top_set and right_set and bottom_set: break
        next_top = top if top_set else math.floor(calib.cy + h / 2 - r * step_y)
        next_bottom = bottom if bottom_set else math.floor(calib.cy + h / 2 + r * step_y)
        next_left = left if left_set else math.floor(calib.cx + w / 2 - r * step_x)
        next_right = right if right_set else math.floor(calib.cx + w / 2 + r * step_x)

        next_top, next_left = max(next_top, 0), max(next_left, 0)
        next_right, next_bottom = min(next_right, w - 1), min(next_bottom, h - 1)

        for v in range(2):
            for u in range(2):
                if (u == 0 and left_set) or (v == 0 and top_set) or (u == 1 and right_set) or (v == 1 and bottom_set): continue
                corner = Metashape.Vector([next_right if u else next_left, next_bottom if v else next_top])
                corner.x += 0.5; corner.y += 0.5
                step = Metashape.Vector([step_x if u else -step_x, step_y if v else -step_y])
                prev_corner = Metashape.Vector(corner) - step
                pt = calib.unproject(corner)
                pt = Metashape.Vector([pt.x / pt.z, pt.y / pt.z])
                prev_pt = calib.unproject(prev_corner)
                prev_pt = Metashape.Vector([prev_pt.x / prev_pt.z, prev_pt.y / prev_pt.z])
                dif = pt - prev_pt

                if (pt.norm() > max_tan or dif * step <= 0 or not calib_valid(calib, corner)):
                    if u: right_set = True
                    else: left_set = True
                    if v: bottom_set = True
                    else: top_set = True

        if not left_set: left = next_left
        if not top_set: top = next_top
        if not right_set: right = next_right
        if not bottom_set: bottom = next_bottom

    right += 1; bottom += 1
    new_w, new_h = right - left, bottom - top
    border = math.ceil(0.01 * min(new_w, new_h))
    
    if left_set: left += border
    if right_set: right -= border
    if top_set: top += border
    if bottom_set: bottom -= border
    return (top, right, bottom, left)

def compute_undistorted_calib(sensor):
    calib_initial = sensor.calibration
    w, h, f = calib_initial.width, calib_initial.height, calib_initial.f
    (reg_top, reg_right, reg_bottom, reg_left) = get_valid_calib_region(calib_initial)

    left, right, top, bottom = -float("inf"), float("inf"), -float("inf"), float("inf")
    for i in range(reg_top, reg_bottom):
        im_pt = Metashape.Vector([reg_left + 0.5, i + 0.5])
        if calib_valid(calib_initial, im_pt):
            pt = calib_initial.unproject(im_pt); left = max(left, pt.x / pt.z)
        im_pt = Metashape.Vector([reg_right - 0.5, i + 0.5])
        if calib_valid(calib_initial, im_pt):
            pt = calib_initial.unproject(im_pt); right = min(right, pt.x / pt.z)

    for i in range(reg_left, reg_right):
        im_pt = Metashape.Vector([i + 0.5, reg_top + 0.5])
        if calib_valid(calib_initial, im_pt):
            pt = calib_initial.unproject(im_pt); top = max(top, pt.y / pt.z)
        im_pt = Metashape.Vector([i + 0.5, reg_bottom - 0.5])
        if calib_valid(calib_initial, im_pt):
            pt = calib_initial.unproject(im_pt); bottom = min(bottom, pt.y / pt.z)

    T1 = Metashape.Matrix.Diag([1, 1, 1, 1])
    left_ang, right_ang = math.atan(left), math.atan(right)
    top_ang, bottom_ang = math.atan(top), math.atan(bottom)
    rotation_vec = Metashape.Vector([math.tan((left_ang + right_ang) / 2), math.tan((top_ang + bottom_ang) / 2), 1]).normalized()
    rotation_vec = Metashape.Vector.cross(Metashape.Vector((0, 0, 1)), rotation_vec)
    T1 = Metashape.Matrix.Rotation(axis_magnitude_rotation(rotation_vec))

    (halfht, halfwr, halfhb, halfwl) = compute_size(top, right, bottom, left, T1)
    halfht = math.floor(f * halfht)
    halfwr = math.floor(f * halfwr)
    halfhb = math.floor(f * halfhb)
    halfwl = math.floor(f * halfwl)
    halfw = min(halfwl, halfwr)
    halfh = min(halfht, halfhb)
    halfwl = halfwr = halfw
    halfht = halfhb = halfh
    max_dim = max(w, h)

    calib = Metashape.Calibration()
    calib.f = f
    calib.width = min(math.floor(max_dim * 1.2), math.floor(halfwl + halfwr))
    calib.height = min(math.floor(max_dim * 1.2), math.floor(halfht + halfhb))
    calib.cx = halfwl - (halfwl + halfwr) / 2
    calib.cy = halfht - (halfht + halfhb) / 2
    return (calib, T1)

# ==========================================
# 2. 鱼眼/全景 Cubemap 系统
# ==========================================
def get_image_safe(camera):
    try:
        image = camera.photo.image()
        if image is not None:
            try: buf = image.tobytes()
            except AttributeError: buf = image.tostring()
            dt_str = str(image.data_type).upper()
            if 'U16' in dt_str: np_type = np.uint16
            elif 'F32' in dt_str: np_type = np.float32
            else: np_type = np.uint8  
            expected_len = image.width * image.height * image.cn * np.dtype(np_type).itemsize
            if len(buf) == expected_len:
                img_arr = np.frombuffer(buf, dtype=np_type).reshape(image.height, image.width, image.cn)
                if np_type == np.uint16: img_arr = (img_arr / 256.0).astype(np.uint8)
                elif np_type == np.float32: img_arr = np.clip(img_arr * 255.0, 0, 255).astype(np.uint8)
                if image.cn == 3: return img_arr.copy()
                elif image.cn == 4: return img_arr[:, :, :3].copy()
                elif image.cn == 1: return img_arr.copy()
    except Exception as e:
        pass
    return None


def calibration_signature_payload(calibration):
    payload = {"type": str(getattr(calibration, "type", ""))}
    for name in CALIBRATION_FIELDS:
        value = getattr(calibration, name, 0)
        payload[name] = float(value or 0)
    payload["width"] = int(getattr(calibration, "width", 0) or 0)
    payload["height"] = int(getattr(calibration, "height", 0) or 0)
    return payload


def matrix_signature_payload(matrix):
    if matrix is None:
        return None
    values = []
    for row in range(4):
        for column in range(4):
            try:
                values.append(float(matrix[row, column]))
            except Exception:
                return str(matrix)
    return values


def strategy_signature_payload(strategy):
    if strategy.get("type") == "Cubemap":
        return {
            "type": "Cubemap",
            "optW": int(strategy["opt_W"]),
            "sensorInfo": str(strategy.get("info_str", "")),
            "faces": get_face_configs(int(strategy["opt_W"])),
            "jpegQuality": 100,
        }
    return {
        "type": "Frame",
        "mode": str(strategy.get("mode", "")),
        "calibration": calibration_signature_payload(strategy["calib1"]),
        "transform": matrix_signature_payload(strategy.get("T1")),
        "jpegQuality": 100,
    }


def camera_image_signature(camera, strategy, source_cache, cached_sources=None):
    source_path = str(camera.photo.path)
    source = source_cache.get(source_path)
    if source is None:
        source_key = str(Path(source_path).resolve())
        source = source_record(source_path, cached=(cached_sources or {}).get(source_key))
        source_cache[source_path] = source
    sensor = {
        "key": int(getattr(camera.sensor, "key", 0) or 0),
        "type": str(getattr(camera.sensor, "type", "")),
        "calibration": calibration_signature_payload(camera.sensor.calibration),
    }
    return build_image_signature(
        source,
        sensor,
        strategy_signature_payload(strategy),
        IMAGE_CONTRACT_VERSION,
    )

def get_face_configs(W):
    W_half = int(W / 2)
    return {
        'front':  (W,      W,      W_half, W_half),
        'right':  (W_half, W,      W_half, W_half),
        'left':   (W_half, W,      0,      W_half),
        'top':    (W,      W_half, W_half, 0),
        'bottom': (W,      W_half, W_half, W_half)
    }

def build_remap_grid(face, W, calib, R_face, sensor_info_str):
    fw, fh, cx, cy = get_face_configs(W)[face]
    u, v = np.meshgrid(np.arange(fw, dtype=np.float32), np.arange(fh, dtype=np.float32))
    f_p = W / 2.0 
    X, Y, Z = (u + 0.5 - cx) / f_p, (v + 0.5 - cy) / f_p, np.ones_like(u)
    
    Ri = R_face.T
    Xb = Ri[0,0]*X + Ri[0,1]*Y + Ri[0,2]*Z
    Yb = Ri[1,0]*X + Ri[1,1]*Y + Ri[1,2]*Z
    Zb = Ri[2,0]*X + Ri[2,1]*Y + Ri[2,2]*Z
    
    r_xy = np.sqrt(Xb**2 + Yb**2)
    theta = np.arctan2(r_xy, Zb)
    
    if 'Equisolid' in sensor_info_str: r_base = 2.0 * np.sin(theta / 2.0)
    elif 'Stereographic' in sensor_info_str: r_base = 2.0 * np.tan(theta / 2.0)
    elif 'Orthographic' in sensor_info_str: r_base = np.sin(theta)
    else: r_base = theta
    
    k = [getattr(calib, kn, 0) or 0 for kn in ['k1','k2','k3','k4']]
    p1, p2 = getattr(calib, 'p1', 0) or 0, getattr(calib, 'p2', 0) or 0
    b1, b2 = getattr(calib, 'b1', 0) or 0, getattr(calib, 'b2', 0) or 0
    
    mask = r_xy > 1e-10
    xn, yn = np.zeros_like(theta), np.zeros_like(theta)
    xn[mask], yn[mask] = Xb[mask] / r_xy[mask], Yb[mask] / r_xy[mask]
    x, y = xn * r_base, yn * r_base
    r2 = x**2 + y**2
    radial = 1 + k[0]*r2 + k[1]*r2**2 + k[2]*r2**3 + k[3]*r2**4
    xd = x * radial + p1 * (r2 + 2 * x**2) + 2 * p2 * x * y
    yd = y * radial + p2 * (r2 + 2 * y**2) + 2 * p1 * x * y
    
    mx = (calib.width/2.0 + calib.cx - 0.5) + xd * calib.f + xd * b1 + yd * b2
    my = (calib.height/2.0 + calib.cy - 0.5) + yd * calib.f
    return mx.astype(np.float32), my.astype(np.float32)


def remap_valid_fraction(mx, my, width, height):
    if width < 2 or height < 2 or mx.size == 0 or my.shape != mx.shape:
        return 0.0
    valid = (mx >= 0) & (mx < width - 1) & (my >= 0) & (my < height - 1)
    return float(np.mean(valid))


def validate_fisheye_remap_grid(sensor, face, mx, my):
    coverage = remap_valid_fraction(mx, my, sensor.calibration.width, sensor.calibration.height)
    if coverage < MIN_FISHEYE_REMAP_COVERAGE:
        raise RuntimeError(
            "Fisheye calibration cannot cover the requested cubemap face: "
            f"sensor={sensor.label!r} face={face} coverage={coverage:.1%}. "
            "This project likely contains a resolution-incompatible legacy calibration; "
            "re-align the source material with the current xPano version."
        )
    return coverage

def save_image_array(image_array, file_path):
    if image_array.ndim == 2:
        mode = "L"
    elif image_array.shape[2] == 4:
        mode = "RGBA"
    else:
        mode = "RGB"
        image_array = image_array[:, :, :3]
    image = Metashape.Image.fromstring(image_array.tobytes(), image_array.shape[1], image_array.shape[0], mode)
    comp = Metashape.ImageCompression()
    comp.jpeg_quality = 100
    image.save(file_path, comp)


def save_metashape_image(image, file_path):
    comp = Metashape.ImageCompression()
    comp.jpeg_quality = 100
    image.save(file_path, comp)

def threaded_remap_and_save(img_src, mx, my, file_path, remap_engine=None):
    out_img = (remap_engine or RemapEngine(None, "numpy")).remap(img_src, mx, my)
    save_image_array(out_img, file_path)


def sensor_is_fisheye_like(sensor):
    if sensor.type == Metashape.Sensor.Type.Fisheye:
        return True
    if sensor.type != Metashape.Sensor.Type.Frame:
        text = str(sensor.type)
        if sensor.calibration:
            text += " " + str(sensor.calibration.type)
        return any(k in text for k in ['Fisheye', 'Spherical', 'Equisolid', 'Equidistant', 'Orthographic', 'Stereographic'])
    return False


def make_original_frame_calib(sensor):
    calib = sensor.calibration
    width = int(getattr(calib, "width", 0) or getattr(sensor, "width", 0) or 0)
    height = int(getattr(calib, "height", 0) or getattr(sensor, "height", 0) or 0)
    f = float(getattr(calib, "f", 0) or max(width, height))
    cx = float(getattr(calib, "cx", 0) or 0)
    cy = float(getattr(calib, "cy", 0) or 0)
    fallback = Metashape.Calibration()
    fallback.width = width
    fallback.height = height
    fallback.f = f
    fallback.cx = cx
    fallback.cy = cy
    return fallback


def frame_export_strategy(sensor):
    try:
        calib, T1 = compute_undistorted_calib(sensor)
        if int(getattr(calib, "width", 0) or 0) > 0 and int(getattr(calib, "height", 0) or 0) > 0:
            return {"mode": "undistort", "calib": calib, "T1": T1}
    except Exception as exc:
        print(f"WARN: Frame undistort calibration failed for sensor {sensor.label}: {exc}; using original image", flush=True)
    return {
        "mode": "original",
        "calib": make_original_frame_calib(sensor),
        "T1": Metashape.Matrix.Diag([1, 1, 1, 1]),
    }


def save_frame_camera_image(camera, calib0, calib1, T1, strategy, path):
    if strategy.get("mode") == "undistort":
        try:
            img_ms = camera.image().warp(calib0, Metashape.Matrix.Diag([1, 1, 1, 1]), calib1, T1)
            save_metashape_image(img_ms, path)
            return
        except Exception as exc:
            print(f"WARN: Frame warp failed for {camera.label}: {exc}; using original image", flush=True)

    image = get_image_safe(camera)
    if image is None:
        try:
            save_metashape_image(camera.photo.image(), path)
            return
        except Exception as exc:
            raise RuntimeError(f"Frame image export failed for {camera.label}: {exc}") from exc
    save_image_array(image, path)

def project_track_to_pinhole(point_xyz, R, T, fx, fy, cx, cy, width, height):
    X = np.array(point_xyz, dtype=np.float64)
    pc = R @ X + T
    if pc[2] <= 1e-8:
        return None
    u = fx * (pc[0] / pc[2]) + cx
    v = fy * (pc[1] / pc[2]) + cy
    if 0 <= u < width and 0 <= v < height:
        return (float(u), float(v))
    return None

def camera_projections(chunk, camera):
    if not chunk.tie_points:
        return []
    try:
        return chunk.tie_points.projections[camera]
    except KeyError:
        return []


def emit_export_event(stage, message, percent, current=None, total=None):
    payload = {
        "phase": "export",
        "stage": stage,
        "percent": percent,
        "phasePercent": max(0, min(100, round((percent - 95) / 5 * 100))),
        "message": message,
    }
    if current is not None:
        payload["current"] = int(current)
    if total is not None:
        payload["total"] = int(total)
    print("PIPELINE_EVENT:" + json.dumps(payload, ensure_ascii=True), flush=True)

# ==========================================
# 3. 缝合调度与二进制写入
# ==========================================
def _run_active_component_export(
    out_dir=None,
    show_dialog=True,
    reuse_images_dir=None,
    image_cache_path=None,
    image_cache_output=None,
    selected_component_key=None,
):
    export_started = time.perf_counter()
    doc = Metashape.app.document
    chunk = doc.chunk
    if not chunk: 
        print("错误：没有有效 Chunk！")
        return

    if out_dir is None:
        out_dir = Metashape.app.getExistingDirectory("选择混合导出文件夹")
    if not out_dir: return

    sparse_dir = os.path.join(out_dir, "sparse", "0")
    images_dir = os.path.join(out_dir, "images")
    for d in [sparse_dir, images_dir]: os.makedirs(d, exist_ok=True)

    reuse_images_dir = Path(reuse_images_dir) if reuse_images_dir else None
    image_cache_path = Path(image_cache_path) if image_cache_path else None
    image_cache_output = Path(image_cache_output) if image_cache_output else Path(out_dir) / "work" / "export_image_cache.json"
    image_cache = empty_image_cache()
    if image_cache_path:
        try:
            image_cache = load_image_cache(image_cache_path)
        except Exception as exc:
            print(f"WARN: Existing image cache is unavailable; regenerating images: {exc}", flush=True)
    next_cache_cameras = {}
    source_cache = {}
    cached_sources = {}
    for entry in image_cache.get("cameras", {}).values():
        cached_source = entry.get("source") if isinstance(entry, dict) else None
        if isinstance(cached_source, dict) and cached_source.get("path"):
            cached_sources[cached_source["path"]] = cached_source
    cache_hits = 0
    cache_misses = 0

    T_shift = get_coord_transform(chunk, True)
    colmap_cams = {}
    colmap_imgs = []
    points3d_list = {}
    sensor_map = {}
    
    cam_id_acc = 1
    img_id_acc = 1

    valid_cameras = [
        c for c in chunk.cameras
        if c.transform and c.sensor and c.sensor.calibration and c.enabled
    ]
    if not valid_cameras:
        raise RuntimeError("No aligned cameras are available in the selected Metashape component")
    print(
        f">>> Exporting Metashape Component {selected_component_key} "
        f"({len(valid_cameras)} aligned cameras).",
        flush=True,
    )
    used_sensors = []
    used_sensor_keys = set()
    for camera in valid_cameras:
        if camera.sensor.key not in used_sensor_keys:
            used_sensors.append(camera.sensor)
            used_sensor_keys.add(camera.sensor.key)

    sensor_scan_started = time.perf_counter()
    print(">>> [1/4] 开始扫描相机模型...", flush=True)
    for sensor in used_sensors:
        sensor_info_str = str(sensor.type)
        if sensor.calibration:
            sensor_info_str += " " + str(sensor.calibration.type)
            
        if sensor_is_fisheye_like(sensor):
            calib = sensor.calibration
            opt_W = int(round(calib.f * 2.0))
            if opt_W % 2 != 0: opt_W += 1
            sensor_map[sensor.key] = {
                'type': 'Cubemap', 'opt_W': opt_W, 'faces': {}, 'info_str': sensor_info_str
            }
            for face in ['front', 'left', 'right', 'top', 'bottom']:
                f_cfg = get_face_configs(opt_W)[face]
                colmap_cams[cam_id_acc] = (
                    1, int(f_cfg[0]), int(f_cfg[1]), float(opt_W)/2.0, float(opt_W)/2.0, float(f_cfg[2]), float(f_cfg[3])
                )
                sensor_map[sensor.key]['faces'][face] = cam_id_acc
                cam_id_acc += 1
        else:
            strategy = frame_export_strategy(sensor)
            calib = strategy['calib']
            T1 = strategy['T1']
            if calib.width == 0 or calib.height == 0:
                raise RuntimeError(f"Invalid Frame calibration size for sensor {sensor.label}: {calib.width}x{calib.height}")
            sensor_map[sensor.key] = {
                'type': 'Frame', 'cid': cam_id_acc, 'calib1': calib, 'T1': T1, 'mode': strategy['mode']
            }
            colmap_cams[cam_id_acc] = (
                1, calib.width, calib.height, calib.f, calib.f, 
                calib.cx + calib.width * 0.5, calib.cy + calib.height * 0.5
            )
            cam_id_acc += 1

    sensor_scan_seconds = time.perf_counter() - sensor_scan_started
    point_scan_started = time.perf_counter()
    print(">>> [2/4] 提取 3D 轨迹点...", flush=True)
    if chunk.tie_points:
        for i, pt in enumerate(chunk.tie_points.points):
            if not pt.valid or abs(pt.coord[3]) < 1e-10: continue
            track_id = pt.track_id
            v_w = T_shift.mulp(Metashape.Vector([pt.coord[j]/pt.coord[3] for j in range(3)]))
            rgb = chunk.tie_points.tracks[track_id].color or (255, 255, 255)
            points3d_list[track_id] = {
                'xyz': (v_w.x, v_w.y, v_w.z), 'rgb': (int(rgb[0]), int(rgb[1]), int(rgb[2])),
                'error': 0.0, 'refs': []
            }

    point_scan_seconds = time.perf_counter() - point_scan_started
    image_export_started = time.perf_counter()
    print(">>> [3/4] 开始处理照片 (严格防 OOM 控制并发)...", flush=True)
    grid_cache = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5) # 严格限制并发仅供 5 个面使用
    remap_engine = RemapEngine(
        warning_cb=lambda message: print(f"WARN: {message}", flush=True),
    )
    print(f">>> Image remap backend: {remap_engine.backend}", flush=True)
    
    R_faces = {
        'front': np.eye(3),
        'left':  np.array([[0,0,1],[0,1,0],[-1,0,0]]),
        'right': np.array([[0,0,-1],[0,1,0],[1,0,0]]),
        'top':   np.array([[1,0,0],[0,0,1],[0,-1,0]]),
        'bottom':np.array([[1,0,0],[0,0,-1],[0,1,0]])
    }

    total_cams = len(valid_cameras)
    emit_export_event("export.images", "正在导出训练图像", 97, 0, total_cams)
    
    for idx, camera in enumerate(valid_cameras):
        emit_export_event(
            "export.images",
            f"正在导出训练图像 {idx + 1}/{total_cams}",
            97 + int(2 * (idx + 1) / max(1, total_cams)),
            idx + 1,
            total_cams,
        )
        # 强制刷新进度条到控制台
        print(f"    处理中 [{idx+1}/{total_cams}] : {camera.label}", flush=True)
        
        if camera.sensor.key not in sensor_map: continue
        strategy = sensor_map[camera.sensor.key]
        img_name_base = f"{camera.key:05d}_{os.path.basename(camera.photo.path)}"
        projections = list(camera_projections(chunk, camera))
        try:
            image_signature = camera_image_signature(camera, strategy, source_cache, cached_sources)
        except Exception as exc:
            image_signature = None
            print(f"WARN: Image cache signature failed for {camera.label}: {exc}", flush=True)

        if strategy['type'] == 'Frame':
            calib0 = camera.sensor.calibration
            calib1 = strategy['calib1']
            T1 = strategy['T1']
            cid = strategy['cid']

            transform = T_shift * camera.transform * T1
            R = transform.rotation().inv()
            T = -1 * (R * transform.translation())
            Q = matrix_to_quat(R)
            
            img_name = f"frame_{img_name_base}"
            out_path = os.path.join(images_dir, img_name)
            reused = bool(
                image_signature
                and reuse_images_dir
                and reuse_cached_outputs(
                    image_cache,
                    str(camera.key),
                    image_signature,
                    reuse_images_dir,
                    images_dir,
                )
            )
            if reused:
                cache_hits += 1
            else:
                cache_misses += 1
                save_frame_camera_image(camera, calib0, calib1, T1, strategy, out_path)
            if not os.path.exists(out_path):
                raise RuntimeError(f"Frame image export did not create {out_path}")

            pts2d = []
            T1_inv = T1.inv()
            for proj in projections:
                track_id = proj.track_id
                if track_id in points3d_list:
                    pt2d = calib1.project(T1_inv.mulp(calib0.unproject(proj.coord)))
                    if pt2d and 0 <= pt2d.x < calib1.width and 0 <= pt2d.y < calib1.height:
                        pts2d.append((pt2d.x, pt2d.y, track_id))
                        points3d_list[track_id]['refs'].append((img_id_acc, len(pts2d) - 1))

            colmap_imgs.append({
                'id': img_id_acc, 'Q': Q, 'T': T, 'cid': cid, 'name': img_name, 'pts2d': pts2d
            })
            img_id_acc += 1
            if image_signature:
                cached_entry = image_cache.get("cameras", {}).get(str(camera.key), {})
                next_cache_cameras[str(camera.key)] = {
                    "signature": image_signature,
                    "source": source_cache[str(camera.photo.path)],
                    "outputs": (
                        refresh_output_records(cached_entry.get("outputs", []), images_dir)
                        if reused
                        else [output_record(out_path, images_dir)]
                    ),
                    "backend": "cache" if reused else (
                        "metashape-warp" if strategy.get("mode") == "undistort" else "metashape-image"
                    ),
                }

        elif strategy['type'] == 'Cubemap':
            opt_W = strategy['opt_W']
            T_c2w = T_shift * camera.transform
            R_c2w = np.array([[T_c2w[i,j] for j in range(3)] for i in range(3)])
            R_c2w = R_c2w / np.linalg.norm(R_c2w, axis=0)
            C_w = np.array([T_c2w[0,3], T_c2w[1,3], T_c2w[2,3]])
            R_w2c = R_c2w.T
            T_w2c = -R_w2c @ C_w

            expected_names = []
            for face in ['front', 'left', 'right', 'top', 'bottom']:
                expected_name = f"cube_{face}_{img_name_base}"
                if not expected_name.lower().endswith(('.jpg', '.jpeg')):
                    expected_name += ".jpg"
                expected_names.append(expected_name)
            reused = bool(
                image_signature
                and reuse_images_dir
                and reuse_cached_outputs(
                    image_cache,
                    str(camera.key),
                    image_signature,
                    reuse_images_dir,
                    images_dir,
                )
            )
            if reused:
                cache_hits += 1
                img_src = None
            else:
                cache_misses += 1
                img_src = get_image_safe(camera)
                if img_src is None:
                    raise RuntimeError(f"Cubemap source image could not be loaded for {camera.label}")

                for face in ['front', 'left', 'right', 'top', 'bottom']:
                    cache_key = (camera.sensor.key, opt_W, face)
                    if cache_key not in grid_cache:
                        grid = build_remap_grid(
                            face,
                            opt_W,
                            camera.sensor.calibration,
                            R_faces[face],
                            strategy['info_str'],
                        )
                        validate_fisheye_remap_grid(camera.sensor, face, *grid)
                        grid_cache[cache_key] = grid

            # 只为当前的这张图片创建临时并发池，处理完立刻清空内存
            cam_tasks = []
            output_paths = []
            for face, img_name in zip(['front', 'left', 'right', 'top', 'bottom'], expected_names):
                cid = strategy['faces'][face]
                
                rf, tf = R_faces[face] @ R_w2c, R_faces[face] @ T_w2c
                qw, qx, qy, qz = matrix_to_quat(Metashape.Matrix(rf.tolist()))
                fw, fh, vcx, vcy = get_face_configs(opt_W)[face]
                img_id = img_id_acc
                pts2d = []

                fx = fy = opt_W / 2.0
                for proj in projections:
                    track_id = proj.track_id
                    point = points3d_list.get(track_id)
                    if point is None:
                        continue
                    uv = project_track_to_pinhole(point['xyz'], rf, tf, fx, fy, vcx, vcy, fw, fh)
                    if uv is None:
                        continue
                    pts2d.append((uv[0], uv[1], track_id))
                    point['refs'].append((img_id, len(pts2d) - 1))
                
                colmap_imgs.append({
                    'id': img_id_acc, 'Q': Metashape.Vector([qw, qx, qy, qz]), 'T': Metashape.Vector([tf[0], tf[1], tf[2]]), 
                    'cid': cid, 'name': img_name, 'pts2d': pts2d
                })
                img_id_acc += 1

                out_path = os.path.join(images_dir, img_name)
                output_paths.append(out_path)
                if not reused:
                    cache_key = (camera.sensor.key, opt_W, face)
                    mx, my = grid_cache[cache_key]
                    # 提交这一个面的渲染任务
                    cam_tasks.append((executor.submit(threaded_remap_and_save, img_src, mx, my, out_path, remap_engine), out_path))
            
            if cam_tasks:
                futures = [task for task, _path in cam_tasks]
                concurrent.futures.wait(futures)
                for task, out_path in cam_tasks:
                    task.result()
                    if not os.path.exists(out_path):
                        raise RuntimeError(f"Cubemap image export did not create {out_path}")
            for out_path in output_paths:
                if not os.path.exists(out_path):
                    raise RuntimeError(f"Cubemap image export did not create {out_path}")
            if image_signature:
                cached_entry = image_cache.get("cameras", {}).get(str(camera.key), {})
                next_cache_cameras[str(camera.key)] = {
                    "signature": image_signature,
                    "source": source_cache[str(camera.photo.path)],
                    "outputs": (
                        refresh_output_records(cached_entry.get("outputs", []), images_dir)
                        if reused
                        else [output_record(path, images_dir) for path in output_paths]
                    ),
                    "backend": "cache" if reused else remap_engine.backend,
                }

    executor.shutdown()
    image_export_seconds = time.perf_counter() - image_export_started

    points3d_list = {track_id: point for track_id, point in points3d_list.items() if point['refs']}

    colmap_write_started = time.perf_counter()
    print(">>> [4/4] 写入 COLMAP 二进制文件...", flush=True)
    emit_export_event("export.colmap", "正在写出 COLMAP 模型", 99)
    with open(os.path.join(sparse_dir, "cameras.bin"), "wb") as fout:
        fout.write(u64(len(colmap_cams)))
        for cid in sorted(colmap_cams.keys()):
            c = colmap_cams[cid]
            fout.write(u32(cid)); fout.write(u32(c[0])); fout.write(u64(c[1])); fout.write(u64(c[2]))
            for param in c[3:]: fout.write(d64(param))

    with open(os.path.join(sparse_dir, "images.bin"), "wb") as fout:
        fout.write(u64(len(colmap_imgs)))
        for img in colmap_imgs:
            fout.write(u32(img['id']))
            fout.write(d64(img['Q'].w)); fout.write(d64(img['Q'].x)); fout.write(d64(img['Q'].y)); fout.write(d64(img['Q'].z))
            fout.write(d64(img['T'].x)); fout.write(d64(img['T'].y)); fout.write(d64(img['T'].z))
            fout.write(u32(img['cid'])); fout.write(bstr(img['name'])); fout.write(u64(len(img['pts2d'])))
            for pt in img['pts2d']:
                fout.write(d64(pt[0])); fout.write(d64(pt[1])); fout.write(u64(pt[2]))

    with open(os.path.join(sparse_dir, "points3D.bin"), "wb") as fout:
        fout.write(u64(len(points3d_list)))
        for track_id, p in points3d_list.items():
            fout.write(u64(track_id))
            fout.write(d64(p['xyz'][0])); fout.write(d64(p['xyz'][1])); fout.write(d64(p['xyz'][2]))
            fout.write(u8(p['rgb'][0])); fout.write(u8(p['rgb'][1])); fout.write(u8(p['rgb'][2]))
            fout.write(d64(p['error'])); fout.write(u64(len(p['refs'])))
            for ref in p['refs']:
                fout.write(u32(ref[0])); fout.write(u32(ref[1]))

    colmap_write_seconds = time.perf_counter() - colmap_write_started
    cache_write_started = time.perf_counter()
    write_image_cache(image_cache_output, next_cache_cameras)
    cache_write_seconds = time.perf_counter() - cache_write_started
    print(
        f">>> Image cache summary: reused={cache_hits}, regenerated={cache_misses}, path={image_cache_output}",
        flush=True,
    )
    print(
        "XPANO_EXPORT_METRICS:" + json.dumps({
            "backend": remap_engine.backend,
            "cacheHits": cache_hits,
            "cacheMisses": cache_misses,
            "sensorScanSeconds": round(sensor_scan_seconds, 3),
            "pointScanSeconds": round(point_scan_seconds, 3),
            "imageExportSeconds": round(image_export_seconds, 3),
            "colmapWriteSeconds": round(colmap_write_seconds, 3),
            "cacheWriteSeconds": round(cache_write_seconds, 3),
            "totalSeconds": round(time.perf_counter() - export_started, 3),
        }, ensure_ascii=True),
        flush=True,
    )

    if not show_dialog:
        return
    print(">>> 运行完毕！", flush=True)
    try:
        Metashape.app.messageBox("混合导出完成！请检查输出文件夹。")
    except Exception:
        print("混合导出完成！请检查输出文件夹。", flush=True)


def run_mixed_export(
    out_dir=None,
    show_dialog=True,
    reuse_images_dir=None,
    image_cache_path=None,
    image_cache_output=None,
    selected_component_key=None,
):
    chunk = Metashape.app.document.chunk
    if not chunk:
        print("错误：没有有效 Chunk！")
        return
    inspection = inspect_components(chunk)
    selected = resolve_component_key(
        inspection,
        selected_component_key,
        strict=selected_component_key is not None,
    )
    with activated_component(chunk, selected):
        return _run_active_component_export(
            out_dir,
            show_dialog=show_dialog,
            reuse_images_dir=reuse_images_dir,
            image_cache_path=image_cache_path,
            image_cache_output=image_cache_output,
            selected_component_key=selected,
        )

if __name__ == "__main__":
    print("====================================", flush=True)
    print("开始执行 3DGS 混合导出脚本...", flush=True)
    export_arg = None
    if "--export-dir" in sys.argv:
        idx = sys.argv.index("--export-dir")
        if idx + 1 < len(sys.argv):
            export_arg = sys.argv[idx + 1]
    def argument_value(name):
        if name not in sys.argv:
            return None
        index = sys.argv.index(name)
        return sys.argv[index + 1] if index + 1 < len(sys.argv) else None

    run_mixed_export(
        export_arg,
        reuse_images_dir=argument_value("--reuse-images-dir"),
        image_cache_path=argument_value("--image-cache-path"),
        image_cache_output=argument_value("--image-cache-output"),
    )
