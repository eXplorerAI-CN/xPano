import Metashape
import random
import math

# ================= 配置区 =================
# 如果为 True: 导出适配 Postshot/SuperSplat 的 Y-Up 坐标系
# 如果为 False: 保持 Metashape 默认的 Z-Up 坐标系
EXPORT_FOR_3DGS = True 

# RANSAC 严苛阈值系数 (0.001 代表场景对角线的千分之一)
STRICT_THRESHOLD_COEFF = 0.001
# ==========================================

def dot(v1, v2):
    return v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]

def cross(v1, v2):
    return Metashape.Vector([
        v1[1]*v2[2] - v1[2]*v2[1],
        v1[2]*v2[0] - v1[0]*v2[2],
        v1[0]*v2[1] - v1[1]*v2[0]
    ])

def normalize(v):
    n = math.sqrt(dot(v, v))
    return v / n if n > 0 else v

def get_ransac_plane(points, iterations=5000, threshold=0.001, must_be_perpendicular_to=None):
    best_inliers_count = 0
    best_normal = None
    best_p = None
    
    # 样本池过滤
    sample_pool = points if len(points) < 10000 else random.sample(points, 10000)

    for i in range(iterations):
        p1, p2, p3 = random.sample(sample_pool, 3)
        n = normalize(cross(p2 - p1, p3 - p1))
        
        if must_be_perpendicular_to:
            if abs(dot(n, must_be_perpendicular_to)) > 0.05: continue
        
        # 统计内点 (步进采样加速)
        inliers_count = sum(1 for p in sample_pool[::2] if abs(dot(p - p1, n)) < threshold)
        
        if inliers_count > best_inliers_count:
            best_inliers_count = inliers_count
            best_normal = n
            best_p = p1
            
    return best_normal, best_p

def main():
    doc = Metashape.app.document
    chunk = doc.chunk
    if not chunk or not chunk.tie_points:
        print("错误: 当前 Chunk 没有连接点，请先运行 Align Photos。")
        return

    print(f"--- 启动转换程序 (EXPORT_FOR_3DGS = {EXPORT_FOR_3DGS}) ---")

    # 1. 提取点云
    all_points = [p.coord[:3] for p in chunk.tie_points.points if p.valid]
    selected_points = [p.coord[:3] for p in chunk.tie_points.points if p.valid and p.selected]
    
    # 动态阈值计算
    tmp_min = Metashape.Vector([min(p[0] for p in all_points[::20]), min(p[1] for p in all_points[::20]), min(p[2] for p in all_points[::20])])
    tmp_max = Metashape.Vector([max(p[0] for p in all_points[::20]), max(p[1] for p in all_points[::20]), max(p[2] for p in all_points[::20])])
    diag = (tmp_max - tmp_min).norm()
    threshold = diag * STRICT_THRESHOLD_COEFF
    
    # 2. 拟合地平面法线 (Ground Normal)
    if len(selected_points) >= 3:
        print(f"模式: 【手动辅助】 基于 {len(selected_points)} 个选中点，阈值 {threshold:.6f}")
        ground_n, p_ref = get_ransac_plane(selected_points, 5000, threshold)
    else:
        print(f"模式: 【全自动】 扫描全局点云，阈值 {threshold:.6f}")
        ground_n, p_ref = get_ransac_plane(all_points, 5000, threshold)

    if not ground_n:
        print("拟合失败，点云可能共线或太少。")
        return

    # 3. 确保法线朝向相机 (向上)
    cam_centers = [c.center for c in chunk.cameras if c.transform]
    if cam_centers:
        avg_cam = sum(cam_centers, Metashape.Vector([0,0,0])) / len(cam_centers)
        if dot(avg_cam - p_ref, ground_n) < 0:
            ground_n = -ground_n

    # 4. 寻找辅助正交轴 (寻找墙面或使用默认对齐)
    # 尝试寻找一个垂直于地面的平面作为正面 (X轴)
    wall_n, _ = get_ransac_plane(all_points, 2000, threshold * 5, must_be_perpendicular_to=ground_n)
    
    if wall_n:
        x_orig = wall_n
    else:
        # 如果没有墙面，利用默认世界坐标系投影
        temp_v = Metashape.Vector([0, 1, 0])
        x_orig = normalize(cross(temp_v, ground_n))
        if x_orig.norm() < 0.1:
            x_orig = normalize(cross(Metashape.Vector([1, 0, 0]), ground_n))

    y_orig = normalize(cross(ground_n, x_orig))
    x_orig = normalize(cross(y_orig, ground_n))

    # 5. 定义目标坐标系轴向 (关键变换点)
    if EXPORT_FOR_3DGS:
        # 目标: Y轴向上 (WebGL/Postshot)
        # New_X = x_orig, New_Y = ground_n, New_Z = -y_orig
        X_final, Y_final, Z_final = x_orig, -ground_n, y_orig
        print("已应用: Y-Up (3DGS/SuperSplat) 轴向转换")
    else:
        # 目标: Z轴向上 (Metashape默认)
        X_final, Y_final, Z_final = x_orig, y_orig, ground_n
        print("已应用: Z-Up (Metashape标准) 轴向保持")

    # 6. 计算 5%-95% 的核心中心
    proj_X = sorted([dot(p, X_final) for p in all_points])
    proj_Y = sorted([dot(p, Y_final) for p in all_points])
    proj_Z = sorted([dot(p, Z_final) for p in all_points])
    
    num = len(all_points)
    i_min, i_max = int(num * 0.05), int(num * 0.95)
    
    c_x = (proj_X[i_min] + proj_X[i_max]) / 2
    c_y = (proj_Y[i_min] + proj_Y[i_max]) / 2
    c_z = (proj_Z[i_min] + proj_Z[i_max]) / 2
    
    # 内部坐标系中的中心点位置
    new_center_internal = X_final * c_x + Y_final * c_y + Z_final * c_z

    # 7. 构造并应用变换矩阵
    # 旋转部分
    R = Metashape.Matrix([X_final, Y_final, Z_final]) 
    
    # Metashape 的 chunk.transform.matrix 是从 Internal 映射到 Local 的 4x4 矩阵
    # 我们构造一个以 new_center_internal 为原点，R 为基向量的逆变换
    T = Metashape.Matrix.Translation(-new_center_internal)
    R_4x4 = Metashape.Matrix.Diag([1,1,1,1])
    for i in range(3):
        for j in range(3):
            R_4x4[i, j] = R[i, j]
    
    # 应用变换
    chunk.transform.matrix = R_4x4 * T

    # 8. 同步更新 Region (包围盒)
    chunk.region.center = new_center_internal
    chunk.region.rot = R.t() # Region 旋转是局部基矢的转置
    chunk.region.size = Metashape.Vector([
        proj_X[i_max] - proj_X[i_min],
        proj_Y[i_max] - proj_Y[i_min],
        proj_Z[i_max] - proj_Z[i_min]
    ]) * 1.2 # 留出 20% 余量

    print(f"--- 转换成功！阈值已优化，核心区域已居中 ---")
    Metashape.app.update()

if __name__ == "__main__":
    main()