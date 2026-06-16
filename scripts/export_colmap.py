# -*- coding: utf-8 -*-
import Metashape
import os
import cv2
import numpy as np
import struct
import math
import concurrent.futures
import sys

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
                if image.cn == 3: return img_arr[:, :, ::-1].copy()
                elif image.cn == 4: return cv2.cvtColor(img_arr, cv2.COLOR_RGBA2BGR)
                elif image.cn == 1: return img_arr.copy()
    except Exception as e:
        pass
    return None

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
    
    r2 = r_base**2
    r_dist = r_base * (1 + k[0]*r2 + k[1]*r2**2 + k[2]*r2**3 + k[3]*r2**4)
    mask = r_xy > 1e-10
    xn, yn = np.zeros_like(theta), np.zeros_like(theta)
    xn[mask], yn[mask] = Xb[mask] / r_xy[mask], Yb[mask] / r_xy[mask]
    xd, yd = xn * r_dist, yn * r_dist
    
    if p1 != 0 or p2 != 0:
        r_dist2 = r_dist**2
        tang_x = p1 * (r_dist2 + 2 * xd**2) + 2 * p2 * xd * yd
        tang_y = p2 * (r_dist2 + 2 * yd**2) + 2 * p1 * xd * yd
        xd += tang_x; yd += tang_y
    
    mx = (calib.width/2.0 + calib.cx - 0.5) + xd * calib.f + xd * b1 + yd * b2
    my = (calib.height/2.0 + calib.cy - 0.5) + yd * calib.f
    return mx.astype(np.float32), my.astype(np.float32)

def threaded_remap_and_save(img_src, mx, my, file_path):
    try:
        out_img = cv2.remap(img_src, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
        is_success, buffer = cv2.imencode(".jpg", out_img, [int(cv2.IMWRITE_JPEG_QUALITY), 100])
        if is_success:
            with open(file_path, "wb") as f: f.write(buffer)
    except: pass

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

# ==========================================
# 3. 缝合调度与二进制写入
# ==========================================
def run_mixed_export(out_dir=None):
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

    T_shift = get_coord_transform(chunk, True)
    colmap_cams = {}
    colmap_imgs = []
    points3d_list = {}
    sensor_map = {}
    
    cam_id_acc = 1
    img_id_acc = 1

    valid_cameras = [c for c in chunk.cameras if c.transform and c.sensor and c.sensor.calibration and c.enabled]
    used_sensors = []
    used_sensor_keys = set()
    for camera in valid_cameras:
        if camera.sensor.key not in used_sensor_keys:
            used_sensors.append(camera.sensor)
            used_sensor_keys.add(camera.sensor.key)

    print(">>> [1/4] 开始扫描相机模型...", flush=True)
    for sensor in used_sensors:
        sensor_info_str = str(sensor.type)
        if sensor.calibration:
            sensor_info_str += " " + str(sensor.calibration.type)
            
        if any(k in sensor_info_str for k in ['Fisheye', 'Spherical', 'Equisolid', 'Equidistant', 'Orthographic', 'Stereographic']):
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
            calib, T1 = compute_undistorted_calib(sensor)
            if calib.width == 0: continue
            sensor_map[sensor.key] = {
                'type': 'Frame', 'cid': cam_id_acc, 'calib1': calib, 'T1': T1
            }
            colmap_cams[cam_id_acc] = (
                1, calib.width, calib.height, calib.f, calib.f, 
                calib.cx + calib.width * 0.5, calib.cy + calib.height * 0.5
            )
            cam_id_acc += 1

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

    print(">>> [3/4] 开始处理照片 (严格防 OOM 控制并发)...", flush=True)
    grid_cache = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5) # 严格限制并发仅供 5 个面使用
    
    R_faces = {
        'front': np.eye(3),
        'left':  np.array([[0,0,1],[0,1,0],[-1,0,0]]),
        'right': np.array([[0,0,-1],[0,1,0],[1,0,0]]),
        'top':   np.array([[1,0,0],[0,0,1],[0,-1,0]]),
        'bottom':np.array([[1,0,0],[0,0,-1],[0,1,0]])
    }

    total_cams = len(valid_cameras)
    
    for idx, camera in enumerate(valid_cameras):
        # 强制刷新进度条到控制台
        print(f"    处理中 [{idx+1}/{total_cams}] : {camera.label}", flush=True)
        
        if camera.sensor.key not in sensor_map: continue
        strategy = sensor_map[camera.sensor.key]
        img_name_base = f"{camera.key:05d}_{os.path.basename(camera.photo.path)}"

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
            ext = os.path.splitext(img_name)[1].lower()
            img_ms = camera.image().warp(calib0, Metashape.Matrix.Diag([1, 1, 1, 1]), calib1, T1)
            if ext in [".jpg", ".jpeg"]:
                comp = Metashape.ImageCompression()
                comp.jpeg_quality = 100
                img_ms.save(os.path.join(images_dir, img_name), comp)
            else:
                img_ms.save(os.path.join(images_dir, img_name))

            pts2d = []
            T1_inv = T1.inv()
            for proj in camera_projections(chunk, camera):
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

        elif strategy['type'] == 'Cubemap':
            opt_W = strategy['opt_W']
            T_c2w = T_shift * camera.transform
            R_c2w = np.array([[T_c2w[i,j] for j in range(3)] for i in range(3)])
            R_c2w = R_c2w / np.linalg.norm(R_c2w, axis=0)
            C_w = np.array([T_c2w[0,3], T_c2w[1,3], T_c2w[2,3]])
            R_w2c = R_c2w.T
            T_w2c = -R_w2c @ C_w

            img_src = get_image_safe(camera)

            # 只为当前的这张图片创建临时并发池，处理完立刻清空内存
            cam_tasks = []
            for face in ['front', 'left', 'right', 'top', 'bottom']:
                cid = strategy['faces'][face]
                img_name = f"cube_{face}_{img_name_base}"
                if not img_name.lower().endswith(('.jpg', '.jpeg')): img_name += ".jpg"
                
                rf, tf = R_faces[face] @ R_w2c, R_faces[face] @ T_w2c
                qw, qx, qy, qz = matrix_to_quat(Metashape.Matrix(rf.tolist()))
                fw, fh, vcx, vcy = get_face_configs(opt_W)[face]
                img_id = img_id_acc
                pts2d = []

                fx = fy = opt_W / 2.0
                for proj in camera_projections(chunk, camera):
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

                if img_src is not None:
                    cache_key = (camera.sensor.key, opt_W, face)
                    if cache_key not in grid_cache:
                        grid_cache[cache_key] = build_remap_grid(face, opt_W, camera.sensor.calibration, R_faces[face], strategy['info_str'])
                    mx, my = grid_cache[cache_key]
                    # 提交这一个面的渲染任务
                    cam_tasks.append(executor.submit(threaded_remap_and_save, img_src.copy(), mx, my, os.path.join(images_dir, img_name)))
            
            if cam_tasks:
                concurrent.futures.wait(cam_tasks)

    executor.shutdown()

    points3d_list = {track_id: point for track_id, point in points3d_list.items() if point['refs']}

    print(">>> [4/4] 写入 COLMAP 二进制文件...", flush=True)
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

    print(">>> 运行完毕！", flush=True)
    try:
        Metashape.app.messageBox("混合导出完成！请检查输出文件夹。")
    except Exception:
        print("混合导出完成！请检查输出文件夹。", flush=True)

if __name__ == "__main__":
    print("====================================", flush=True)
    print("开始执行 3DGS 混合导出脚本...", flush=True)
    export_arg = None
    if "--export-dir" in sys.argv:
        idx = sys.argv.index("--export-dir")
        if idx + 1 < len(sys.argv):
            export_arg = sys.argv[idx + 1]
    run_mixed_export(export_arg)
