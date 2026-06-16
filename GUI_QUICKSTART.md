# GUI Quickstart

这是给最终用户看的最短启动说明。完整工作流、CLI 和发布注意事项见 `README.md`。

## 1. 安装依赖

先确认：

- Python 可通过 `python` 命令启动。
- `ffmpeg.exe` 已加入 `PATH`。
- `metashape.exe` 已加入 `PATH`，或设置 `XPANO_METASHAPE` 为完整 exe 路径。

然后运行：

```powershell
INSTALL_DEPS.bat
```

## 2. 启动软件

普通启动：

```powershell
RUN_GUI.bat
```

如果窗口闪退或需要看日志：

```powershell
RUN_GUI_DEBUG.bat
```

调试日志会写入：

```text
xpano_gui_error.log
```

## 3. 基本操作

1. 在“素材轨”中添加全景视频，选择 `.osv` / `.insv` 文件。
2. 选择输出文件夹。
3. 检查 Metashape 路径是否正确。
4. 输入抽帧间隔，单位是秒/帧，推荐 `1.0`。
5. 帧数限制可留空；快速测试可填 `50`。
6. 点击开始。

## 4. 输出结果

输出文件夹内会生成：

```text
work\xpano.psx
work\xpano_manifest.json
xpano_alignment_summary.txt
xpano_run_summary.json
images\*.jpg
sparse\0\cameras.bin
sparse\0\images.bin
sparse\0\points3D.bin
```

`sparse\0` 和 `images` 可以作为 COLMAP 数据输入到 3DGS/NeRF 流程。
