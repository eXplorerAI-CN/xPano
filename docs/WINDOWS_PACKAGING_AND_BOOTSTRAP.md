# xPano Windows 安装器与运行时自举设计

## 1. 支持边界

- 正式目标仅为 Windows 10/11 x64、当前用户安装。
- 安装后无需系统 Python、FFmpeg、COLMAP、Git、CUDA Toolkit 或管理员权限。
- Metashape 本体及许可证不随包发布；选择 Metashape 后端时只探测用户已有安装。
- Torch、TorchVision、Open3D、PyCOLMAP、RoMa 模型与其他致密化大依赖不随安装包发布。
- 本轮验证不得下载上述大依赖；下载分支使用本地假源、小文件和模拟 pip 验证。

## 2. 单一发布链

正式发布只允许：

```text
scripts/build_installer.ps1
  -> 校验源码/锁文件/本地供应物
  -> 创建全新 runtime staging
  -> 写入逐文件 SHA-256 release manifest
  -> pnpm --frozen-lockfile 构建当前前端
  -> cargo/Tauri 构建当前 Rust EXE
  -> Tauri NSIS 打包 staging resources
  -> 安装器静态审计与 SHA-256
```

约束：

- 不提供正式的 `SkipTauriBuild`；正式安装器永远从当前源码构建。
- 只使用 `pnpm-lock.yaml`。`package-lock.json` 不参与发布解析。
- staging 每次先创建到新的临时目录，通过校验后原子替换；不复用旧 release 文件夹。
- Tauri NSIS 是唯一安装器。旧 Inno/portable 脚本只能作为明确标记的开发辅助，不得产出正式文件名。
- 安装器版本由 Tauri/Cargo 单一版本源派生；版本不一致直接失败。

依据：Tauri 官方支持 NSIS、`resources`、`installerHooks` 和 `currentUser` 安装模式。

- https://v2.tauri.app/distribute/windows-installer/
- https://v2.tauri.app/reference/config/#nsisconfig

## 3. 随包内容

允许进入 staging 的内容采用显式 allowlist：

| 类别 | 内容 | 原因 |
|---|---|---|
| 应用 | 当前构建的 `xPano.exe` 与 Tauri 必需 DLL | 主程序 |
| 基础 Python | `binaries/python`，含已验证的 NumPy/OpenCV/Pillow/piexif | 抽帧、导出和基础管线 |
| FFmpeg | `ffmpeg.exe`、`ffprobe.exe` | 素材探测、缩略图和抽帧 |
| COLMAP | 已验证的 Windows x64 运行目录及 app-local DLL | 免费对齐后端 |
| 脚本 | 运行时实际引用的 Python 脚本及包 | 管线逻辑 |
| 致密化源码 | 固定版本的 LichtFeld 插件源码，不含 `.git`/缓存/模型 | 按需运行入口 |
| 自举 | runtime manifest、bootstrap 脚本、`pip.pyz`、许可证 | 安全下载与安装 |
| WebView2 | 已校验的 Evergreen Offline Installer | 无网络机器首次安装 |

禁止进入 staging：

- `.venv-densify/`
- `tools/torch-cache/`
- `tools/offline-wheels/densify/`
- 测试、截图、工程输出、`.git`、`__pycache__`、`.pyc/.pyo`、PDB、下载临时目录
- 构建脚本、开发文档和未在运行时 allowlist 中的任意仓库文件

构建脚本必须解析 Windows 符号链接并复制真实文件；0-byte WinGet link 不能通过资源校验。

## 4. 安装与 WebView2

- NSIS `installMode = currentUser`，默认安装到用户目录，不触发 UAC。
- 禁止降级安装；升级保持用户工程和运行时缓存。
- 使用 NSIS hook 在 WebView2 缺失时静默执行随包 Offline Installer；成功后删除安装目录中的临时安装器副本。
- 卸载只删除应用安装目录和应用设置；默认保留用户工程。
- 致密化缓存单独询问是否删除，不能由普通卸载静默删除数 GiB 用户已下载数据。

## 5. 运行时目录

应用资源只读；所有可变环境放在：

```text
%LOCALAPPDATA%\com.xpano.app\
  runtimes\densify\<runtime-id>\
    site-packages\
    runtime.json
    complete.marker
  cache\artifacts\sha256\<hash>
  downloads\<hash>.partial
  state\active-densify.json
  logs\bootstrap-*.log
```

`runtime-id` 至少包含 manifest 版本、Windows x64、CPython ABI、Torch 版本和 CPU/CUDA profile。升级创建新目录，验证成功后原子更新 active 指针；旧版本在新版本成功前保持可用。

## 6. 解释器策略

- 基础应用只使用随包 Python 3.12，不读取系统 site-packages。
- 致密化也使用同一随包解释器，但额外注入当前 runtime 的 `site-packages`、`torch/lib` 和 Open3D DLL 路径。
- 不创建系统 venv，不修改 PATH、注册表或用户 Python。
- 内置 Python 不承担在线 pip；随包提供固定版本 `pip.pyz`，只对 staging runtime 执行离线 `--target` 安装。

Python 官方说明 embeddable distribution 几乎与用户系统隔离，适合应用内嵌：

- https://docs.python.org/3/using/windows.html#the-embeddable-package

## 7. 依赖 manifest

manifest 是版本化 JSON，正式文件必须随发布签名/校验，核心字段：

```json
{
  "schemaVersion": 1,
  "runtimeVersion": "densify-1",
  "platform": "windows-x86_64",
  "pythonAbi": "cp312",
  "profiles": {
    "cuda": {
      "probe": "nvidia",
      "packages": ["torch", "torchvision", "open3d", "pycolmap"]
    },
    "cpu": {
      "probe": "cpu",
      "packages": ["torch", "torchvision", "open3d", "pycolmap"]
    }
  },
  "artifacts": [
    {
      "id": "example-wheel",
      "filename": "example.whl",
      "size": 123,
      "sha256": "...",
      "urls": ["mirror-1", "official-origin"]
    }
  ]
}
```

规则：

- 所有包和传递依赖精确锁版本、文件名、大小、SHA-256。
- 只接受 `win_amd64`、`cp312/abi3` 或 `py3-none-any` wheel；拒绝 sdist 和本机编译。
- 每个 artifact 按 manifest URL 顺序尝试：受控镜像优先，官方源兜底。
- 下载后先校验大小和 SHA-256，再进入共享缓存。
- pip 最终仅从已校验本地 wheelhouse 安装，并启用 `--require-hashes --only-binary :all:`。

依据：pip 官方安全安装文档明确建议 hash-checking mode 与禁用源码分发：

- https://pip.pypa.io/en/stable/topics/secure-installs/

Torch 的 CPU/CUDA wheel 使用 PyTorch 官方 index 作为权威来源：

- https://pytorch.org/get-started/locally/

## 8. GPU/profile 选择

1. 检查 `nvidia-smi` 是否存在并能返回 GPU/driver。
2. 检查 Windows 显卡信息只用于诊断，不据此宣称 CUDA 可用。
3. 用户启用 CUDA 且 NVIDIA 探针通过时选择锁定 CUDA profile；否则选择 CPU profile并明确提示降级。
4. 安装完成后运行隔离探针：导入全部模块、输出版本、执行 `torch.cuda.is_available()`、创建一个小 tensor。
5. CUDA profile 探针失败时保留日志并失败，不静默再下载另一套大包；UI 提供明确的“改用 CPU 配置”操作。

不要求系统安装 CUDA Toolkit；兼容性由锁定的 PyTorch wheel 和 NVIDIA 驱动探针决定。

## 9. 下载、缓存与恢复事务

状态机：

```text
idle -> planning -> downloading -> verifying -> installing -> probing -> ready
                    |             |             |           |
                    +---------- failed / cancelled ----------+
```

- 下载使用 `.partial`，支持 HTTP Range；服务器不支持 Range 时从零重试并记录原因。
- 取消只终止当前网络/安装子进程；已校验共享缓存保留，partial 可在下次恢复。
- 安装写入 `<runtime-id>.staging-<uuid>`；只有全部探针通过才写 `complete.marker` 并切换 active 指针。
- 失败绝不覆盖当前 active runtime，错误必须包含阶段、artifact、镜像、HTTP/进程错误码和日志路径。
- 第二次启动先校验 active marker/manifest/hash，不重复下载。
- 磁盘预算在下载前按“未缓存 artifact + staging 解压 + 安全余量”计算，不足则 fail fast。
- 同一机器只允许一个 bootstrap lock；第二个请求返回已有任务快照而不是并发写目录。

## 10. 探针与错误语义

基础随包探针：

- `xPano.exe` 当前版本/hash
- Python 版本与 `cv2/numpy/PIL/piexif` 导入
- FFmpeg/ffprobe 可执行、版本和硬解能力查询
- COLMAP 可执行、版本、插件/DLL 启动
- LichtFeld 插件 `--help` 轻量路径
- WebView2 存在

致密化探针：

- manifest/profile/runtime-id 一致
- 所有 wheel 安装记录存在
- `torch/torchvision/open3d/pycolmap/PIL/scipy/tqdm/einops/rich` 导入
- runner `--help`
- CPU 小 tensor；CUDA profile 额外验证 CUDA tensor

错误码至少区分：`MANIFEST_INVALID`、`UNSUPPORTED_PLATFORM`、`DISK_FULL`、`NETWORK_OFFLINE`、`HTTP_ERROR`、`HASH_MISMATCH`、`INSTALL_FAILED`、`PROBE_FAILED`、`CANCELLED`、`BUSY`。

## 11. 环境模拟验收

禁止真实下载大包。测试通过注入临时目录、假 manifest、localhost/文件镜像、假 pip 和小 artifact 覆盖：

- 无任何 runtime -> 自动计划/下载/安装/切 active
- 已缓存 -> 零网络复用
- NVIDIA/无 NVIDIA -> CUDA/CPU profile
- 镜像 1 失败 -> 镜像 2 成功
- 断网、Range 恢复、hash 错、磁盘不足、取消、进程失败
- 中文/空格路径、非管理员目录、并发请求
- 旧 active + 新安装失败 -> 旧 active 不变
- 升级成功 -> 新 active，旧 runtime 可清理
- 卸载应用 -> 工程不删；runtime/cache 按用户选择

## 12. 发布门

正式安装器只有在以下证据全部存在时生成：

- Python、Rust、Node、PowerShell 测试全绿
- staging allowlist 与逐文件 hash manifest 通过
- 禁止内容扫描为零
- 安装器安装到带中文/空格的 current-user 路径成功
- 基础资源探针在隔离 PATH 下成功
- 假源环境矩阵全绿且网络日志证明未访问大包源
- 安装版再次完成真实素材抽帧、对齐、点云预览和既有致密化 runtime 验收
- 安装器 SHA-256 与版本/依赖清单已输出
