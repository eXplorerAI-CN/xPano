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
    halfht = math.floor(f * halfht); halfwr = math.floor(f * halfwr)
    halfhb = math.floor(f * halfhb); halfwl = math.floor(f * halfwl)
    halfw = min(halfwl, halfwr); halfh = min(halfht, halfhb)
    halfwl = halfwr = halfw; halfht = halfhb = halfh
    max_dim = max(w, h)
    calib = Metashape.Calibration()
    calib.f = f; calib.width = min(math.floor(max_dim * 1.2), math.floor(halfwl + halfwr))
    calib.height = min(math.floor(max_dim * 1.2), math.floor(halfht + halfhb))
    calib.cx = halfwl - (halfwl + halfwr) / 2; calib.cy = halfht - (halfht + halfhb) / 2
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
    except Exception: pass
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
    Xb, Yb, Zb = Ri[0,0]*X + Ri[0,1]*Y + Ri[0,2]*Z, Ri[1,0]*X + Ri[1,1]*Y + Ri[1,2]*Z, Ri[2,0]*X + Ri[2,1]*Y + Ri[2,2]*Z
    r_xy = np.sqrt(Xb**2 + Yb**2); theta = np.arctan2(r_xy, Zb)
    if 'Equisolid' in sensor_info_str: r_base = 2.0 * np.sin(theta / 2.0)
    elif 'Stereographic' in sensor_info_str: r_base = 2.0 * np.tan(theta / 2.0)
    elif 'Orthographic' in sensor_info_str: r_base = np.sin(theta)
    else: r_base = theta
    k = [getattr(calib, kn, 0) or 0 for kn in ['k1','k2','k3','k4']]
    p1, p2, b1, b2 = [getattr(calib, kn, 0) or 0 for kn in ['p1','p2','b1','b2']]
    r2 = r_base**2; r_dist = r_base * (1 + k[0]*r2 + k[1]*r2**2 + k[2]*r2**3 + k[3]*r2**4)
    mask = r_xy > 1e-10; xn, yn = np.zeros_like(theta), np.zeros_like(theta)
    xn[mask], yn[mask] = Xb[mask] / r_xy[mask], Yb[mask] / r_xy[mask]
    xd, yd = xn * r_dist, yn * r_dist
    if p1 != 0 or p2 != 0:
        r_dist2 = r_dist**2; xd, yd = xd + p1*(r_dist2+2*xd**2)+2*p2*xd*yd, yd + p2*(r_dist2+2*yd**2)+2*p1*xd*yd
    mx = (calib.width/2.0 + calib.cx - 0.5) + xd * calib.f + xd * b1 + yd * b2
    my = (calib.height/2.0 + calib.cy - 0.5) + yd * calib.f
    return mx.astype(np.float32), my.astype(np.float32)

def threaded_remap_and_save(img_src, mx, my, file_path):
    try:
        out_img = cv2.remap(img_src, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
        is_success, buffer = cv2.imencode(".jpg", out_img, [int(cv2.IMWRITE_JPEG_QUALITY), 100])
        if is_success:
            with open(file_path, "wb") as f: f.write(buffer)
    except Exception: pass

# ==========================================
# 3. 缝合调度与二进制写入
# ==========================================
def run_mixed_export():
    doc = Metashape.app.document; chunk = doc.chunk
    if not chunk: return
    out_dir = Metashape.app.getExistingDirectory("选择混合导出文件夹")
    if not out_dir: return

    sparse_dir = os.path.join(out_dir, "sparse", "0"); images_dir = os.path.join(out_dir, "images")
    for d in [sparse_dir, images_dir]: os.makedirs(d, exist_ok=True)

    T_shift = get_coord_transform(chunk, True)
    colmap_cams, colmap_imgs, points3d_list, sensor_map = {}, [], {}, {}
    cam_id_acc = 1

    print(">>> [1/4] 开始扫描相机模型...", flush=True)
    for sensor in chunk.sensors:
        s_info = str(sensor.type) + (" " + str(sensor.calibration.type) if sensor.calibration else "")
        if any(k in s_info for k in ['Fisheye', 'Spherical', 'Equisolid', 'Equidistant', 'Orthographic', 'Stereographic']):
            calib = sensor.calibration; opt_W = int(round(calib.f * 2.0))
            if opt_W % 2 != 0: opt_W += 1
            sensor_map[sensor.key] = {'type': 'Cubemap', 'opt_W': opt_W, 'faces': {}, 'info_str': s_info}
            for face in ['front', 'left', 'right', 'top', 'bottom']:
                f_cfg = get_face_configs(opt_W)[face]
                colmap_cams[cam_id_acc] = (1, int(f_cfg[0]), int(f_cfg[1]), float(opt_W)/2.0, float(opt_W)/2.0, float(f_cfg[2]), float(f_cfg[3]))
                sensor_map[sensor.key]['faces'][face] = cam_id_acc; cam_id_acc += 1
        else:
            calib, T1 = compute_undistorted_calib(sensor)
            if calib.width == 0: continue
            sensor_map[sensor.key] = {'type': 'Frame', 'cid': cam_id_acc, 'calib1': calib, 'T1': T1}
            colmap_cams[cam_id_acc] = (1, calib.width, calib.height, calib.f, calib.f, calib.cx + calib.width*0.5, calib.cy + calib.height*0.5)
            cam_id_acc += 1

    print(">>> [2/4] 提取 3D 轨迹点...", flush=True)
    if chunk.tie_points:
        for pt in chunk.tie_points.points:
            if not pt.valid or abs(pt.coord[3]) < 1e-10: continue
            v_w = T_shift.mulp(Metashape.Vector([pt.coord[j]/pt.coord[3] for j in range(3)]))
            rgb = chunk.tie_points.tracks[pt.track_id].color or (255, 255, 255)
            points3d_list[pt.track_id] = {'xyz': (v_w.x, v_w.y, v_w.z), 'rgb': (int(rgb[0]), int(rgb[1]), int(rgb[2])), 'error': 0.0, 'refs': []}

    print(">>> [3/4] 开始处理照片...", flush=True)
    grid_cache = {}; executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    R_faces = {'front': np.eye(3), 'left': np.array([[0,0,1],[0,1,0],[-1,0,0]]), 'right': np.array([[0,0,-1],[0,1,0],[1,0,0]]), 'top': np.array([[1,0,0],[0,0,1],[0,-1,0]]), 'bottom': np.array([[1,0,0],[0,0,-1],[0,1,0]])}

    valid_cameras = [c for c in chunk.cameras if c.transform and c.sensor and c.sensor.calibration and c.enabled]
    for idx, camera in enumerate(valid_cameras):
        print(f"    处理中 [{idx+1}/{len(valid_cameras)}] : {camera.label}", flush=True)
        strategy = sensor_map.get(camera.sensor.key)
        if not strategy: continue
        base_name = f"{camera.key:05d}_{os.path.basename(camera.photo.path)}"

        if strategy['type'] == 'Frame':
            cal0, cal1, T1, cid = camera.sensor.calibration, strategy['calib1'], strategy['T1'], strategy['cid']
            trans = T_shift * camera.transform * T1; R = trans.rotation().inv(); T = -1 * (R * trans.translation()); Q = matrix_to_quat(R)
            img_name = f"frame_{base_name}"
            camera.image().warp(cal0, Metashape.Matrix.Diag([1,1,1,1]), cal1, T1).save(os.path.join(images_dir, img_name))
            pts2d = []
            if chunk.tie_points:
                T1_inv = T1.inv()
                for proj in chunk.tie_points.projections[camera]:
                    if proj.track_id in points3d_list:
                        p2 = cal1.project(T1_inv.mulp(cal0.unproject(proj.coord)))
                        if p2 and 0 <= p2.x < cal1.width and 0 <= p2.y < cal1.height: pts2d.append((p2.x, p2.y, proj.track_id))
            colmap_imgs.append({'Q': Q, 'T': T, 'cid': cid, 'name': img_name, 'pts2d': pts2d})

        elif strategy['type'] == 'Cubemap':
            opt_W = strategy['opt_W']; T_c2w = T_shift * camera.transform; R_c2w = np.array([[T_c2w[i,j] for j in range(3)] for i in range(3)])
            R_c2w /= np.linalg.norm(R_c2w, axis=0); R_w2c = R_c2w.T; T_w2c = -R_w2c @ np.array([T_c2w[i,3] for i in range(3)])
            img_src = get_image_safe(camera); cam_tasks = []
            for face in ['front', 'left', 'right', 'top', 'bottom']:
                cid = strategy['faces'][face]; img_name = f"cube_{face}_{base_name}"
                if not img_name.lower().endswith(('.jpg', '.jpeg')): img_name += ".jpg"
                rf, tf = R_faces[face] @ R_w2c, R_faces[face] @ T_w2c
                qw, qx, qy, qz = matrix_to_quat(Metashape.Matrix(rf.tolist()))
                colmap_imgs.append({'Q': Metashape.Vector([qw,qx,qy,qz]), 'T': Metashape.Vector([tf[0],tf[1],tf[2]]), 'cid': cid, 'name': img_name, 'pts2d': []})
                if img_src is not None:
                    ck = (camera.sensor.key, opt_W, face)
                    if ck not in grid_cache: grid_cache[ck] = build_remap_grid(face, opt_W, camera.sensor.calibration, R_faces[face], strategy['info_str'])
                    cam_tasks.append(executor.submit(threaded_remap_and_save, img_src.copy(), grid_cache[ck][0], grid_cache[ck][1], os.path.join(images_dir, img_name)))
            if cam_tasks: concurrent.futures.wait(cam_tasks)

    executor.shutdown()

    print(">>> [4/4] 写入 COLMAP 二进制文件 (正在应用 Front 优先排序)...", flush=True)
    # --- 核心修改：排序逻辑 ---
    colmap_imgs.sort(key=lambda x: 0 if "cube_front" in x['name'] else 1)
    
    # 分配最终 ID 并同步 3D 点引用
    for i, im in enumerate(colmap_imgs):
        im['id'] = i + 1
        for p2d in im['pts2d']:
            points3d_list[p2d[2]]['refs'].append((im['id'], im['pts2d'].index(p2d)))

    # 写入文件
    with open(os.path.join(sparse_dir, "cameras.bin"), "wb") as f:
        f.write(u64(len(colmap_cams)))
        for cid in sorted(colmap_cams.keys()):
            c = colmap_cams[cid]; f.write(u32(cid)); f.write(u32(c[0])); f.write(u64(c[1])); f.write(u64(c[2]))
            for p in c[3:]: f.write(d64(p))

    with open(os.path.join(sparse_dir, "images.bin"), "wb") as f:
        f.write(u64(len(colmap_imgs)))
        for im in colmap_imgs:
            f.write(u32(im['id'])); f.write(d64(im['Q'].w)); f.write(d64(im['Q'].x)); f.write(d64(im['Q'].y)); f.write(d64(im['Q'].z))
            f.write(d64(im['T'].x)); f.write(d64(im['T'].y)); f.write(d64(im['T'].z)); f.write(u32(im['cid']))
            f.write(bstr(im['name'])); f.write(u64(len(im['pts2d'])))
            for p2 in im['pts2d']: f.write(d64(p2[0])); f.write(d64(p2[1])); f.write(u64(p2[2]))

    with open(os.path.join(sparse_dir, "points3D.bin"), "wb") as f:
        f.write(u64(len(points3d_list)))
        for tid, p in points3d_list.items():
            f.write(u64(tid)); f.write(d64(p['xyz'][0])); f.write(d64(p['xyz'][1])); f.write(d64(p['xyz'][2]))
            f.write(u8(p['rgb'][0])); f.write(u8(p['rgb'][1])); f.write(u8(p['rgb'][2])); f.write(d64(p['error'])); f.write(u64(len(p['refs'])))
            for r in p['refs']: f.write(u32(r[0])); f.write(u32(r[1]))

    Metashape.app.messageBox("混合导出完成！")

if __name__ == "__main__":
    run_mixed_export()