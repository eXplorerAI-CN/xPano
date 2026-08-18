# xPano

xPano 是一套 Windows 桌面工作流，用于将全景视频、普通视频和照片处理为可用于 COLMAP 重建、点云查看、致密化和高斯训练的数据。

## 下载地址

- [官方安装包](https://pubres.explorerglobal.cn/Beluga/xpano-release/xPano-setup.exe)
- [网盘备用镜像](https://pan.baidu.com/s/1jfdjtZRJlDJnhq0BP9MfSw?pwd=1234)

```text
导入素材 -> 准备素材 -> 对齐与重建 -> 查看成果 -> 致密化或高斯训练
```

## 核心功能

- 导入 DJI `.osv`、Insta360 `.insv`、普通视频、照片文件夹和航拍照片。
- 视频抽帧，并渐进加载大规模照片文件夹预览。
- 全景素材直接使用原始双鱼眼图像进行重建，不依赖 ERP 或手工切分的 cubemap。
- 全景及混合素材工程采用“全景优先”的 Metashape 对齐流程。
- 导出标准 COLMAP 数据集：`images/` 与 `sparse/0/`。
- 在 Metashape 中手动修正 PSX 后，可从 xPano 重新导出。
- 查看重建点云，并将致密化结果保存为独立点云版本。
- 启动 LichtFeld Studio 高斯训练，同时在 xPano 中查看进度和日志。

## 快速开始

1. 下载并解压 xPano 发布包。
2. 启动 `xPano.exe`。
3. 在**素材**工作区导入视频或照片。
4. 设置抽帧频率、时间范围和可选 LUT。
5. 完成素材准备后，开始对齐与重建。
6. 检查点云质量，确认无误后再继续致密化或高斯训练。

首次导入会在素材旁自动创建 `xPano` 工程目录。请确保素材所在本地磁盘有充足空间。

## 支持的素材

| 类型 | 推荐输入 |
| --- | --- |
| 全景视频 | `.osv`、`.insv` |
| 普通视频 | `.mp4`、`.mov`、`.avi`、`.mkv` |
| 普通照片 | JPG/JPEG、PNG、TIF/TIFF、BMP |
| 航拍照片 | JPG/JPEG、PNG、TIF/TIFF、BMP |

请保持配对的 Insta360 文件位于同一目录。素材准备期间，请勿移动、改名或删除源文件。

## 对齐与重建

**Metashape** 适用于全景工程和混合素材工程。xPano 会先建立全景素材的稳定骨架，再逐步加入普通视频帧和照片。

Metashape 工程保存在：

```text
work/xpano.psx
```

如需手动修正，可在 Metashape 中打开 PSX，保存后回到 xPano 使用**从 PSX 重新导出**。若工程中存在多个 Component，请选择主 Component 后再导出。

**COLMAP** 已内置，当前仅用于全景素材重建。混合素材的 COLMAP 流程尚未通过完整回归验证，因此会被限制使用。

## LUT 支持

- 视频和照片均可使用自定义 `.cube` 风格 LUT。
- DJI `.osv` 的 D-Log M 素材可使用内置 Rec.709 还原 LUT。
- Insta360 还原 LUT 需要手动选择。
- 同时启用时，先执行还原 LUT，再执行风格 LUT。

## 工程结构

```text
MyProject/
  xpano_project.json
  work/
    xpano_manifest.json
    xpano.psx
    jobs/
  images/
  sparse/0/
  xpano_run_summary.json
```

xPano 工程是完整目录。复制、移动或备份时，请保持目录结构完整。

## 环境要求

- Windows 10/11 x64。
- 发布包包含 xPano 所需运行时、FFmpeg 和 COLMAP。
- 使用 Metashape 重建时，需要自行安装并授权 Agisoft Metashape Professional。
- 致密化与高斯训练需要兼容 GPU 和足够显存；软件内会显示运行环境状态。

## 从源码启动

```bat
RUN_XPANO_UI.bat
```

## 许可证与致谢

xPano 采用 MIT 协议发布。

感谢 Agisoft Metashape、COLMAP、FFmpeg、LichtFeld Studio、RoMa 及所有开源社区贡献者。特别感谢[邵青](https://github.com/RobLinkA)、[Beluga](https://github.com/BelugaStd)、[霞霞](https://github.com/EdwardJackson123)及所有项目贡献者。
