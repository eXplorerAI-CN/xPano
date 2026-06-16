# xPano Multi-Track GUI

面向 360 全景视频、普通照片和航拍照片的 Metashape 自动化重建工具。

本项目基于 xPano 的核心思想继续扩展：对齐阶段直接使用 `.osv` / `.insv` 原始双鱼眼帧，不先拼接 ERP，也不先切 cubemap；在 Metashape 中先用 `Station` 约束完成稀疏对齐，再释放为 `Folder` 优化，最后导出 COLMAP 结构给 3D Gaussian Splatting、NeRF 或其他重建流程使用。

把 xPano/Metashape 操作流程封装成一个可复现的一键 GUI 和 CLI。

## 功能状态

已验证：

- 选择 `.osv` / `.insv` 全景视频。
- 按秒/帧抽取左右双鱼眼帧，默认 `1.0` 秒/帧。
- 自动建立每帧左右鱼眼 Camera Station。
- 自动调用 Metashape 完成匹配、对齐、优化和工程保存。
- 全景传感器使用 `Fisheye`，像元尺寸 `0.0024 mm`，焦距 `2.5 mm`。
- 固定 `B1`、`B2`、`K4`，并在对齐后从 `Station` 释放回 `Folder`。
- 导出 COLMAP：`images/` 和 `sparse/0/{cameras.bin, images.bin, points3D.bin}`。
- GUI 进度显示、抽帧预览和日志输出。

实验性支持：

- 普通照片轨道：作为 `Frame` 相机导入，同一 COLMAP 模型导出。
- 航拍照片轨道：作为 `Frame` 相机导入，同一 COLMAP 模型导出。
- 多轨道混合：全景、普通照片、航拍照片进入同一个 Metashape chunk 共同对齐。

混合轨道的导入结构、相机类型和导出结构已经有测试覆盖，但真实同场景“全景 + 手机/航拍”对齐质量仍需要更多数据验收。

## 为什么这样做

传统 360 重建常见流程是：

1. 原始双鱼眼视频先拼接成 ERP 全景图。
2. ERP 再切成多张透视图。
3. 用这些透视图做 SfM/3DGS。

这个流程容易引入非物理形变：拼接软件为了视觉无缝会做光流拉伸，ERP 顶底也有严重极区拉伸。把这些图再交给摄影测量软件做 bundle adjustment，等于让优化器拟合已经被非刚性处理过的图像，容易导致点云漂移、轨迹弯曲、接缝附近重影。

本项目采用相反策略：

1. 对齐阶段只使用原始左右鱼眼。
2. 同一时刻左右鱼眼先设为 Metashape `Station`，帮助初始化。
3. 初始对齐完成后释放为 `Folder`，让优化器恢复真实双镜头小基线。
4. 对齐完成后才做 cubemap/undistort 导出。

这样可以减少对齐图像数量，避免 ERP/拼接形变进入空三，并让导出的透视图继承已经优化好的相机姿态。

## 依赖

Windows 环境：

- Python 3.10+。
- ffmpeg，并确保 `ffmpeg.exe` 在 `PATH` 中。
- Agisoft Metashape，并确保 `metashape.exe` 在 `PATH` 中，或设置环境变量 `XPANO_METASHAPE` 指向完整路径。

Python 依赖：

- GUI/抽帧侧：见 `requirements.txt`。
- Metashape Python 侧：见 `metashape_requirements.txt`。

一键安装：

```powershell
INSTALL_DEPS.bat
```

安装脚本会：

- 检查 `ffmpeg.exe`。
- 安装普通 Python 依赖。
- 查找 `metashape.exe`。
- 使用 Metashape 自带 Python 安装导出所需依赖。

## 快速使用

启动 GUI：

```powershell
RUN_GUI.bat
```

调试模式启动：

```powershell
RUN_GUI_DEBUG.bat
```

GUI 流程：

1. 添加素材轨，至少添加一个全景视频轨。
2. 选择输出文件夹。
3. 确认 Metashape 路径。
4. 输入抽帧间隔，单位为秒/帧，推荐先用 `1.0`。
5. 帧数限制可留空；测试时可填 `50`。
6. 点击开始，等待 COLMAP 输出完成。

输出目录结构：

```text
output/
  work/
    frames/
    xpano_manifest.json
    xpano.psx
  images/
    *.jpg
  sparse/
    0/
      cameras.bin
      images.bin
      points3D.bin
  xpano_alignment_summary.txt
  xpano_run_summary.json
```

## CLI 使用

单个全景视频：

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "D:\path\to\output" `
  --pano "D:\path\to\camera.osv" `
  --seconds-per-frame 1 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

限制前 50 帧做回归测试：

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "D:\path\to\output_50" `
  --pano "D:\path\to\camera.osv" `
  --seconds-per-frame 1 `
  --max-frames 50 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

混合普通照片轨：

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "D:\path\to\mixed_output" `
  --pano "D:\path\to\camera.osv" `
  --standard-track phone "D:\path\to\phone_photos" `
  --seconds-per-frame 1 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

混合航拍照片轨：

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "D:\path\to\drone_output" `
  --pano "D:\path\to\camera.osv" `
  --aerial-track mavic "D:\path\to\drone_photos" `
  --seconds-per-frame 1 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

## 已锁定的 Metashape 流程

全景轨道必须遵守以下流程：

1. 每个采样时刻生成一个文件夹，内部包含左右两张原始鱼眼图。
2. 每个文件夹作为一个 Metashape CameraGroup。
3. 匹配和对齐前，全景 CameraGroup 设为 `Station`。
4. 全景 sensor 设为 `Metashape.Sensor.Type.Fisheye`。
5. 像元尺寸设为 `0.0024`，焦距设为 `2.5`。
6. 初始 `b1`、`b2`、`k4` 设为 `0`。
7. 固定参数必须是大写的 `["B1", "B2", "K4"]`。
8. `matchPhotos` 使用 `tiepoint_limit=0`，并关闭 `filter_stationary_points`。
9. `alignCameras(adaptive_fitting=True)`。
10. 对齐后把全景 CameraGroup 改回 `Folder`。
11. `optimizeCameras(fit_b1=False, fit_b2=False, fit_k4=False)`。
12. 保存 `.psx` 后再执行地面校正和 COLMAP/cubemap 导出。

详细验收记录见 `docs/VERIFIED_WORKFLOW.md`。

## 项目结构

```text
app.py                         GUI 和共享任务编排入口
scripts/
  xpano_extract.py             OSV/INSV 抽帧
  xpano_tracks.py              多素材轨 manifest 构建与校验
  metashape_pipeline.py        Metashape 自动化对齐流程
  export_colmap.py             COLMAP/cubemap/Frame 图像导出
  run_xpano_tracks_job.py      CLI 入口
  verify_xpano_output.py       输出结构校验
  diagnose_metashape_project.py Metashape 工程诊断
docs/
  VERIFIED_WORKFLOW.md         已验收的全景工作流
  MULTI_TRACK_BACKEND.md       多轨道后端设计和测试记录
tests/                         轻量单元测试
```

## 测试

```powershell
python -m py_compile app.py scripts\metashape_pipeline.py scripts\export_colmap.py scripts\xpano_tracks.py
python -m unittest tests.test_xpano_tracks tests.test_verify_xpano_output tests.test_run_xpano_tracks_job tests.test_app_pipeline
```

如果本机安装了 Metashape，可以进一步使用：

```powershell
& "C:\Path\To\Metashape\metashape.exe" -r scripts\diagnose_metashape_project.py `
  --project "D:\path\to\output\work\xpano.psx" `
  --expect-fixed-fisheye
```

## 发布前注意

- 不要提交本机输出目录、`.psx` 工程、抽帧图像、COLMAP 输出和错误日志。
- 不要把 Metashape 本体打进仓库；用户需要自行安装并遵守 Agisoft 授权。
- `download/` 是历史下载/迁移目录，不属于发布主体。
- 混合轨道功能目前建议标为 experimental，直到有更多真实同场景数据验收。

## License

本项目保留原 xPano 的 MIT License。发布 fork 时请保留 `LICENSE`，并在 release note 中说明本 fork 追加了 GUI、多素材轨、Metashape CLI 自动化和 COLMAP 混合导出能力。
