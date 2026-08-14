# xPano COLMAP 后端：当前管线与参数

> 本文按当前源码整理，描述的是 xPano 的**直接 COLMAP 后端**，不是 Metashape 对齐后导出的标准 COLMAP 数据集。
>
> 结论先行：当前直接 COLMAP 后端只用于全景双鱼眼视频。普通视频、标准照片、航拍照片或任何混合素材在 GUI 中都会被拦截，推荐使用 Metashape 后端。

## 1. 总体流程

```text
全景视频
  -> FFmpeg 按采样率抽取左右鱼眼 JPEG
  -> 将左右鱼眼复制到 COLMAP 原生工作区
  -> feature_extractor（SIFT）
  -> sequential_matcher 或 exhaustive_matcher
  -> mapper（稀疏重建）
  -> 将鱼眼原生模型转换为五面透视训练数据
  -> 发布 images/ + sparse/0/
  -> 可选：LichtFeld 致密化或高斯训练
```

相关入口：

- 任务总控：`scripts/pipeline_core.py:run_multi_track_pipeline`
- COLMAP 命令计划：`scripts/colmap_backend.py:build_colmap_plan`
- 原生结果发布与五面转换：`scripts/colmap_backend.py:publish_colmap_output`
- 命令行入口：`scripts/run_xpano_tracks_job.py`

## 2. 输入与抽帧

### 2.1 可用输入

COLMAP 计划只读取 manifest 中 `track_type == "panorama_video"` 的帧。每个采样时刻必须有一对存在的文件：

```text
left.jpg
right.jpg
```

`.osv` 从一个双流文件中取 `0:0` 和 `0:1`；成对 `.insv` 分别取两个文件的 `0:0`。抽帧滤镜为：

```text
fps=<用户设置>[, restoration LUT][, style LUT][, format=yuvj420p]
```

JPEG 质量为 `-q:v 2`。解码优先尝试 CUDA、D3D11VA，再回退软件解码；这只加速抽帧，不改变 COLMAP 的几何计算。

### 2.2 原生 COLMAP 工作区

每对鱼眼图像复制到：

```text
<项目>/colmap/
  database.db
  colmap_images/
    left/000001.jpg
    right/000001.jpg
  sparse/
  xpano_manifest.json
  colmap_images.json
```

COLMAP 直接匹配原始鱼眼，不先转 ERP 或 cubemap。每次创建计划前，会在已经确认所有输入帧存在后清理旧的 `colmap_images/`、`sparse/` 和 `database.db`。

## 3. 运行的 COLMAP 命令

### 3.1 特征提取

```text
colmap feature_extractor
  --database_path <项目>/colmap/database.db
  --image_path <项目>/colmap/colmap_images
  --ImageReader.camera_model OPENCV_FISHEYE
  --ImageReader.camera_params 1041.6666666667,1041.6666666667,1920,1920,0,0,0,0
  --ImageReader.single_camera 0
  --ImageReader.single_camera_per_folder 1
  --FeatureExtraction.max_image_size <见参数表>
  --FeatureExtraction.num_threads 4
  --FeatureExtraction.use_gpu <0 或 1>
  --SiftExtraction.max_num_features <见参数表>
```

相机参数顺序为 `fx, fy, cx, cy, k1, k2, k3, k4`。它是当前代码里的固定初值，不会按每段素材的实际像素尺寸、相机型号或 EXIF 动态求解。`single_camera_per_folder=1` 表示左眼全部帧共享一组内参、右眼全部帧共享另一组内参；两眼不共享内参。

### 3.2 匹配

默认使用时序匹配：

```text
colmap sequential_matcher
  --database_path <...>/database.db
  --FeatureMatching.num_threads 4
  --FeatureMatching.use_gpu <0 或 1>
  --FeatureMatching.guided_matching <0 或 1>
  --SequentialMatching.overlap <见参数表>
  --SequentialMatching.expand_rig_images 1
```

可切换为 `exhaustive_matcher`；该模式不传递顺序 overlap 与 `expand_rig_images`。直接 COLMAP 后端没有额外写入 COLMAP rig 配置或 GPS/IMU 先验。

### 3.3 稀疏重建

```text
colmap mapper
  --database_path <...>/database.db
  --image_path <...>/colmap_images
  --output_path <...>/sparse
```

除实验预设中的少数覆盖项外，Mapper 的 BA、初始化、最小匹配数、最大模型数等均使用安装包中 COLMAP 本身的默认值。若 Mapper 产生多个模型，xPano 自动选取 `(已注册图像数, 点数, 相机数)` 最大的一个；没有像 Metashape 一样的人工 Component 选择器。

## 4. 参数表

### 4.1 配置层的预设

| 参数 | stable | high-density | experimental-high-density |
|---|---:|---:|---:|
| `FeatureExtraction.max_image_size` | 1600 | 1600 | 2000 |
| `SiftExtraction.max_num_features` | 4096 | 8192 | 12000 |
| `SequentialMatching.overlap` | 6 | 10 | 12 |
| `FeatureMatching.guided_matching` | 关闭 | 开启 | 开启 |
| `SiftExtraction.peak_threshold` | COLMAP 默认 | COLMAP 默认 | 0.004 |
| `Mapper.filter_max_reproj_error` | COLMAP 默认 | COLMAP 默认 | 6.0 |
| `Mapper.tri_min_angle` | COLMAP 默认 | COLMAP 默认 | 1.0 |
| `Mapper.tri_ignore_two_view_tracks` | COLMAP 默认 | COLMAP 默认 | `false` |

固定配置：`num_threads=4`、相机模型 `OPENCV_FISHEYE`、上节中的固定初始内参、`single_camera=false`、`single_camera_per_folder=true`。

### 4.2 GUI 实际默认值

| 项目 | 当前 GUI 默认值 | 是否可在 COLMAP 面板直接修改 |
|---|---:|---|
| GPU 特征提取与匹配 | 开启 | 是 |
| Matcher | sequential | 是，可改 exhaustive |
| 最大图像尺寸 | 1600 | 是，最小 256、步长 128 |
| 每图最大 SIFT 特征数 | 4096 | 是，最小 512、步长 512 |
| 线程数 | 4 | 否 |
| 鱼眼模型与初始内参 | 固定值 | 否 |
| 顺序 overlap / guided matching | 由预设决定 | 当前面板没有直接控件 |

CLI 的 `--colmap-use-gpu` 是 `store_true`，因此直接运行 CLI 而不加这个开关时默认 CPU；GUI 默认会传 GPU 开启。若 COLMAP 构建或运行日志表明没有 CUDA，程序会将特征提取和匹配自动重试为 CPU。

### 4.3 预设的当前边界

`colmapDensityPreset` 已存在于项目配置和 CLI，但当前 GUI 没有预设选择控件；正常 GUI 路径为 `stable`。

此外，GUI 总会传递图像尺寸和最大特征数，默认即 `1600/4096`。后端随后把这两个显式值覆盖到预设上。因此即使通过已有项目状态或其他调用选择 `high-density`/`experimental-high-density`，其 `8192`/`12000` 特征数和实验预设的 `2000` 图像尺寸也会被 GUI 默认值覆盖；通常只剩 overlap、guided matching 及实验 Mapper/SIFT 覆盖生效。

## 5. 发布为训练数据

Mapper 的原生鱼眼结果不会直接作为训练输入。xPano 读取选中的原生模型，将每个鱼眼图像转换成：

```text
front, left, right, top, bottom
```

五个透视 JPEG，并同步重投影稀疏点和相机外参。发布目录为：

```text
<项目>/
  images/
    cube_front_*.jpg
    cube_left_*.jpg
    cube_right_*.jpg
    cube_top_*.jpg
    cube_bottom_*.jpg
  sparse/0/
    cameras.bin
    images.bin
    points3D.bin
```

这一步不重新匹配、不改变原始稀疏重建的相机求解，只是把结果物化为训练器可读的透视相机集。一对鱼眼帧会发布为十张透视图；所以 `images/` 的数量约为采样时刻数量的十倍。对齐率必须按原生 Mapper 模型的注册鱼眼图像计算，不能按十倍的发布图像计算。

## 6. 可选后处理

- **LichtFeld Studio 高斯训练**：读取发布后的 `images/` 和 `sparse/0/`。
- **LichtFeld densification**：同样读取发布目录，默认 RoMa `fast`、参考图比例 `0.75`、每参考图邻居数 `3`、每参考图匹配数 `10000`、最低置信度 `0.20`、重投影阈值 `1.5`、Sampson 阈值 `5.0`、最小视差 `0.5`、不限制最大点数。致密化成功后会把候选稠密点合并到 `points3D.bin`。

## 7. 当前实现边界

1. 直接 COLMAP 后端会拒绝任何非全景轨道；不要用它处理“全景 + 平面照片/普通视频”的混合项目。
2. 多段全景视频的帧会按 manifest 顺序合并进同一时序序列，当前没有按轨道建立匹配隔离或跨轨道约束。
3. 固定鱼眼初始内参是该后端最大的相机模型假设；素材的真实尺寸、焦距或畸变明显不同，可能降低对齐质量。
4. GUI 的“向上轴”配置未在直接 COLMAP 分支调用 `postprocess_colmap_axis.py`，因此当前不改变直接 COLMAP 结果坐标轴。
5. 直接 COLMAP 分支会发出 `output.validate` 进度事件，但当前没有实际调用 `verify_output(...)` 做最终结构校验；`run_colmap_plan` 本身只确认数据库和原生稀疏模型存在。
6. 发布 cubemap 时会重建点观测轨迹，属于导出阶段的几何投影；它不是新的 SfM 验证，也不应被当作额外对齐步骤。

这些边界说明了为什么该后端目前应定位为“全景单轨/连续序列的快速直接重建路径”，而不是取代 Metashape 的通用对齐路径。
