# COLMAP 与 LichtFeld 致密化

本文档描述当前 GUI 实际使用的 COLMAP 和 LichtFeld densification plugin 路径。旧的外部 LichtFeld Studio 命令规划不是当前 GUI 的推荐工作流。

## 1. 当前工作流

1. xPano 使用内置或用户指定的 COLMAP 完成特征提取、匹配和稀疏重建。
2. 正式输出保持标准 COLMAP 结构：`images/` 和 `sparse/0/`。
3. 用户可以把 xPano 工程或普通 COLMAP 文件夹载入内置点云查看器。
4. 查看器中的致密化面板调用项目内的 LichtFeld densification plugin 运行时。
5. 致密结果先作为候选预览；用户确认后注册为永久点云版本，不与标准点云合并，也不会自动切换训练输入。

## 2. COLMAP 解析顺序

xPano 按以下顺序查找 COLMAP：

1. GUI/CLI 显式指定路径；
2. `XPANO_COLMAP` 环境变量；
3. 项目或发布包内的 `tools/colmap`；
4. 系统 `PATH` 和常见安装目录。

发布包应包含：

```text
tools/colmap/bin/colmap.exe
```

因此正常发布包不要求用户另行安装 COLMAP。

## 3. 标准输出结构

```text
output/
  images/
  sparse/
    0/
      cameras.bin
      images.bin
      points3D.bin
  colmap/
    database.db
    colmap_images/
    sparse/
  work/
    xpano_manifest.json
  xpano_manifest.json
  xpano_run_summary.json
```

`colmap/` 是原生工作缓存。下游训练器应读取 `images/` 和 `sparse/0/`。

## 4. COLMAP CLI

```powershell
python scripts\run_xpano_tracks_job.py `
  --backend colmap `
  --output "D:\path\to\output" `
  --pano "D:\path\to\camera.osv" `
  --frames-per-second 1
```

密度预设：

- `stable`：内存压力较低的默认方案；
- `high-density`：更多特征和更宽的顺序匹配；
- `experimental-high-density`：进一步放宽过滤，只建议小规模测试。

验证输出：

```powershell
python scripts\verify_xpano_output.py `
  --backend colmap `
  --output "D:\path\to\output" `
  --expect-single-sparse
```

## 5. 致密化依赖

当前插件入口应位于：

```text
tools/lichtfeld-densification-plugin/densify.py
```

运行时优先顺序：

1. 发布包中记录的可移植致密化运行时；
2. `XPANO_LFS_DENSIFY_PYTHON`；
3. 项目本地 `.venv-densify`；
4. 经环境配置器验证的兼容 Python。

源码环境可使用：

```powershell
INSTALL_LFS_DENSIFY.bat
```

环境检查由 `scripts/configure_environment.ps1` 统一负责。发布包包含 COLMAP、插件和必要的小型资源；torch、CUDA 与 Open3D 不随安装器发布，首次使用致密化时由环境引导按需安装或复用已验证的本机环境。

## 6. GUI 致密化参数

当前查看器提供：

- RoMa 模式：`turbo`、`fast`、`base`、`high`、`precise`；
- 参考图比例/数量；
- 每参考图邻居数；
- 每参考图匹配点数；
- 最低置信度；
- 图像筛选；
- CUDA/CPU；
- 步数，默认 50。

注意：当前 `scripts/run_lfs_densify_viewer.py` 只记录和保存“步数”，并明确将其视为后端预留参数；它尚未传递给实际插件算法。界面显示 50 不代表插件内部只迭代 50 次。后续实现前不能把该参数描述为已经控制训练迭代。

## 7. 点云版本与结果应用

致密化运行期间，状态和日志保存在输出工程的致密化工作区。完成后：

1. 查看器载入候选致密点云；
2. 用户检查点云位置与密度；
3. “保存为版本”把候选规范化到 canonical 坐标并写入 `work/geometry/variants/densified_<id>/points3D.bin`；
4. 保存后候选文件仍保留，标准版本也始终保留；
5. 用户可先预览任意就绪版本，再单独点击“设为训练点云”；
6. 切换训练版本时，系统按当前 `worldFromCanonical` 重新物化 `colmap/sparse/0/points3D.bin`，因此标准版与各致密版可以来回切换；
7. “关闭预览（保留候选）”只关闭界面，不删除候选数据。

`standard` 版本、受保护版本和当前训练版本不可删除。非活动致密版本可删除；损坏、缺失或属于旧重建输入的 stale 版本不可预览或激活。切换过程使用 rollback 文件和 transaction marker，工程 revision 只有在活动点文件写入成功后才提交。

## 8. 已知边界

- 大场景 CUDA 致密化可能消耗大量显存和内存，应先用较少帧验证。
- 完全无网且用户没有兼容 torch/Open3D 环境时，致密化不可用；素材准备、对齐、稀疏点云预览仍可使用。
- 致密化“步数”目前是预留参数，尚未影响插件计算。
- 外部 LichtFeld Studio CLI 兼容代码仍存在于源码中，但不属于当前 GUI 主路径，也不作为发布操作说明。
