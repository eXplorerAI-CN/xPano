# xPano 架构加固前三阶段执行规范

> 状态：待实施
> 范围：总路线 Phase 0、Phase 1、Phase 2
> 最后核对：2026-07-10
> 目标读者：维护者，以及需要按步骤执行任务的自动化编码模型

## 1. 背景与阶段关系

当前已经确认两个相互独立的问题：

1. `scripts/configure_environment.ps1` 使用 `$ErrorActionPreference = "Stop"`，
   Python 导入失败写入 stderr 时可能被 PowerShell 转换为终止性的
   `NativeCommandError`。这会让本应进入自动安装分支的“缺少依赖”直接导致任务退出。
2. `xpano-ui/src-tauri/src/lib.rs` 使用 `Command.output()` 执行环境预检。
   PowerShell 完成前，stdout/stderr 被完整缓冲，GUI 无法获得中间日志，也无法取消预检进程。

因此前三阶段必须严格按下面顺序执行：

1. Phase 0：固化可验证基线。
2. Phase 1：修复环境配置器的确定性和错误语义。
3. Phase 2：让环境预检异步、流式、可取消、可观察。

禁止跳过 Phase 0，也禁止同时实现 Phase 1 和 Phase 2。否则环境判断逻辑与进程生命周期同时变化，回归时无法判断责任层。

## 2. 全阶段执行规则

1. 每个阶段必须形成一个独立提交，验收通过后才能开始下一阶段。
2. 不得执行 `git reset --hard`、`git clean -fd` 或覆盖用户现有修改。
3. 当前工作区不是干净状态。禁止在未分类前使用 `git add -A`。
4. 源码、第三方运行时、数据集、构建缓存必须分别处理。
5. 所有新增文本文件使用 UTF-8；Python 和 PowerShell 子进程显式使用 UTF-8。
6. 测试不得实际访问网络。需要覆盖下载分支时使用假命令或临时 fixture。
7. 任一验收门失败时停止后续阶段，先修复当前阶段。
8. 现有 release 不会因为源码修复而自动更新。Phase 2 完成前不得宣称发布包已修复。

---

# Phase 0：固化可验证基线

## 3. 阶段目标

在不改变业务行为的前提下，建立一套可重复执行的检查，明确修改前：

- 哪些 Python 行为已通过测试；
- 哪些 Rust/Tauri 行为已通过测试；
- 前端是否能通过 lint 和 production build；
- 已接受的 Metashape/COLMAP 输出具有什么结构和数量指标；
- 工作区中的文件哪些属于源码，哪些属于本机运行时或生成物。

## 4. 文件范围

创建：

- `pytest.ini`
- `requirements-dev.txt`
- `scripts/verify_dev.ps1`
- `docs/BASELINE.md`

按分类结果决定是否修改：

- `.gitignore`

本阶段禁止修改：

- Metashape 对齐参数；
- COLMAP 参数；
- Python 流水线业务逻辑；
- Tauri 命令行为；
- React 页面行为；
- 发布包内容。

## 5. 详细执行步骤

### 5.1 建立工作分支

先运行：

```powershell
git status --short --branch
git branch --show-current
```

如果尚未处于本任务分支，创建：

```powershell
git switch -c codex/runtime-hardening
```

创建分支不会清理现有修改。若分支已经存在，不得强制覆盖。

### 5.2 分类现有文件

运行：

```powershell
git diff --name-only
git ls-files --others --exclude-standard
git status --short
```

逐项归类：

| 类型 | 示例 | 处理方式 |
|---|---|---|
| 项目源码/测试/文档 | `scripts/*.py`、`tests/*.py`、`xpano-ui/src/**` | 阅读差异后显式暂存 |
| 构建缓存/运行缓存 | `target*`、`__pycache__`、日志 | 加入忽略规则；确认所有权后才可删除 |
| 第三方二进制/本机环境 | `tools/metashape-python`、`tools/lfs-densify-runtime` | 本机保留；普通 Git 不提交二进制载荷 |
| 数据集/重建输出 | OSV、PSX、PLY、COLMAP 输出 | 不提交；只记录指标 |

注意：第三方目录可能被发布脚本使用。“不提交 Git”不等于“从本机删除”。

### 5.3 限定 pytest 收集范围

创建 `pytest.ini`：

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -ra
```

目的：根目录运行 pytest 时不得收集 `binaries/`、`download/`、插件源码和嵌入式 Python 自带测试。

创建 `requirements-dev.txt`：

```text
pytest==9.0.2
```

pytest 只安装到开发 `.venv`，不得安装到 Metashape Python，也不得写入发布包内置 Python。

### 5.4 编写统一验证脚本

`scripts/verify_dev.ps1` 必须：

1. 使用 `$ErrorActionPreference = "Stop"` 管理验证脚本自身错误。
2. 接受可选 `-Bootstrap` 参数。
3. 默认不联网、不安装依赖。
4. `-Bootstrap` 时才安装 `requirements.txt` 和 `requirements-dev.txt`。
5. 依次运行 Python 测试、Rust 测试、前端 lint、前端 build。
6. 任一步退出码非零时立即失败。
7. 最后输出四项结果摘要。

固定命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cargo test --manifest-path .\xpano-ui\src-tauri\Cargo.toml
pnpm.cmd --dir .\xpano-ui run lint
pnpm.cmd --dir .\xpano-ui run build
```

如果 `.venv` 不存在，脚本应给出明确创建命令，不得静默改用 Metashape Python。

### 5.5 记录基线

`docs/BASELINE.md` 至少记录：

- 基线提交哈希；
- Windows、Python、Node、pnpm、Rust 版本；
- Python 测试数量，当前已知参考值为 `114 passed`；
- Rust 测试数量，当前已知参考值为 `4 passed`；
- 前端 lint/build 结果；
- 已知缺陷：环境探测异常、预检日志缓冲、取消无法覆盖预检、旧 EXE 复制风险；
- 测试命令和执行日期。

不要把 `D:\...`、`F:\...` 等个人数据路径写入公开基线文档。需要保存本地路径时写入被忽略的 `.codex_tmp/baseline-local.json`。

### 5.6 记录已接受输出指标

从已经成功的 Metashape 和 COLMAP 输出中各选一个，不重新运行昂贵任务。记录：

- 输入素材类型和抽帧间隔；
- 相机总数、已注册相机数；
- `cameras.bin` 中模型分布；
- `images.bin` 图像数量；
- `points3D.bin` 点数量；
- `xpano_manifest.json`、`xpano_run_summary.json`、PSX 是否存在；
- `images/` 与 `sparse/0/` 是否满足 LichtFeld Studio 输入结构。

### 5.7 提交边界

验收通过后，显式暂存本阶段文件，提交信息固定为：

```text
chore: establish reproducible verification baseline
```

不得把数据集、环境目录、构建产物顺带放入该提交。

## 6. Phase 0 测试与验收门

必须全部满足：

- Python 测试全部通过，参考结果 `114 passed`；
- Rust 测试全部通过，参考结果 `4 passed`；
- `pnpm run lint` 通过；
- `pnpm run build` 通过；
- 根目录 pytest 不再扫描项目外测试；
- 验证后 `git status` 不出现新的构建缓存；
- 所有原有用户修改都已分类，没有被还原或丢弃；
- 已记录 Phase 0 提交哈希。

## 7. Phase 0 非目标与回滚

非目标：修环境脚本、改 GUI、重打 release、调整对齐算法。

回滚使用：

```powershell
git revert <phase-0-commit>
```

不得使用破坏工作区的硬重置。

---

# Phase 1：修复环境配置器确定性错误

## 8. 阶段目标

让环境检查能够稳定区分：

- 解释器不存在；
- 模块未安装；
- Python ABI/架构不支持；
- NumPy、OpenCV 或其他原生 DLL 导入失败；
- 离线 wheel 不完整；
- pip 安装失败；
- 安装完成后二次导入仍失败。

缺少 `cv2` 必须成为正常检查结果，不得再导致 PowerShell 自身以 `NativeCommandError` 终止。

## 9. 文件范围

创建：

- `scripts/probe_python_environment.py`
- `tests/test_probe_python_environment.py`
- `tests/test_configure_environment.ps1`
- `docs/ENVIRONMENT_PROTOCOL.md`

修改：

- `scripts/configure_environment.ps1`
- `scripts/verify_dev.ps1`

本阶段不修改 Rust、React、安装器或 release 内容。

## 10. Python 探针规范

### 10.1 命令行接口

```powershell
python scripts\probe_python_environment.py `
  --module numpy `
  --module cv2 `
  --extra-python-path <site-packages> `
  --check-cuda
```

参数要求：

- `--module` 可重复；至少一个；
- `--extra-python-path` 可重复；
- `--check-cuda` 可选；
- stdout 只输出一行 UTF-8 JSON；
- 诊断内容写入 JSON，不依赖 stderr 传递业务结果。

### 10.2 JSON 结构

```json
{
  "schemaVersion": 1,
  "ok": false,
  "errorCode": "IMPORT_VERIFY_FAILED",
  "interpreter": {
    "executable": "C:\\Path\\python.exe",
    "version": "3.9.13",
    "implementation": "CPython",
    "architecture": "64bit",
    "abiTag": "cp39"
  },
  "modules": [
    {
      "name": "cv2",
      "ok": false,
      "version": null,
      "file": null,
      "errorType": "ModuleNotFoundError",
      "error": "No module named 'cv2'",
      "traceback": "..."
    }
  ],
  "cuda": null
}
```

### 10.3 探针行为

1. 使用 `importlib.import_module()` 逐个导入模块。
2. 禁止使用 `import cv2, numpy, ...`，否则无法判断具体失败模块。
3. 捕获每个模块的异常类型、消息和完整 traceback。
4. `--extra-python-path` 仅插入当前进程的 `sys.path`。
5. 不修改父进程 `PYTHONPATH`。
6. 模块版本优先取 `module.__version__`，不可用时允许为 `null`。
7. `--check-cuda` 仅在用户请求致密化 CUDA 时执行。
8. CUDA 不可用是结构化检查结果，不得让探针崩溃。

退出码固定：

- `0`：全部请求检查通过；
- `2`：至少一个模块导入失败；
- `3`：参数或调用无效；
- `4`：探针自身发生未预期异常。

## 11. PowerShell 重构规范

### 11.1 可测试入口

把主流程封装为：

```powershell
function Invoke-XPanoEnvironment {
    param(...)
}
```

文件被 dot-source 时只定义函数；使用 `-File` 执行时才调用主函数。测试脚本不得通过字符串复制生产函数。

### 11.2 替换导入检查

删除当前通过 `python -c` 加 `*> $null` 判断导入的实现，新增 `Invoke-PythonProbe`：

1. stdout、stderr 分别重定向到临时文件；
2. 调用前保存 `$ErrorActionPreference`；
3. 调用期间把预期的原生 stderr 按非终止错误处理；
4. 在 `finally` 恢复全局设置并删除临时文件；
5. 解析 stdout 中唯一 JSON；
6. JSON 缺失或无效时返回 `PROBE_INVALID_OUTPUT`；
7. stderr 作为诊断保留，不能静默丢弃。

### 11.3 UTF-8 边界

脚本入口保存并设置：

- `[Console]::OutputEncoding`；
- `$OutputEncoding`；
- `PYTHONUTF8=1`；
- `PYTHONIOENCODING=utf-8`。

所有设置在 `finally` 中恢复，保证 dot-source 测试不会污染调用者。

### 11.4 结构化环境事件

增加 `-EmitEvents` 开关。Tauri 模式传入该开关，BAT/终端默认仍显示人类可读文本。

事件格式：

```text
XPANO_ENV_EVENT:{"schemaVersion":1,"kind":"status","stage":"app.imports","status":"checking","message":"正在检查应用 Python","code":null,"current":1,"total":5,"indeterminate":false}
```

固定字段：

- `schemaVersion`；
- `kind`：`status`、`progress`、`warning`、`error`、`result`；
- `stage`；
- `status`：`checking`、`ready`、`installing`、`failed`、`skipped`；
- `message`；
- `code`；
- `current`；
- `total`；
- `indeterminate`。

### 11.5 稳定错误码

至少实现：

- `APP_PYTHON_MISSING`
- `APP_IMPORT_FAILED`
- `FFMPEG_NOT_FOUND`
- `FFPROBE_NOT_FOUND`
- `METASHAPE_NOT_FOUND`
- `METASHAPE_PYTHON_NOT_FOUND`
- `PYTHON_ABI_UNSUPPORTED`
- `COLMAP_NOT_FOUND`
- `DENSIFY_ENV_INCOMPLETE`
- `OFFLINE_WHEELS_INCOMPLETE`
- `PIP_INSTALL_FAILED`
- `IMPORT_VERIFY_FAILED`
- `PROBE_INVALID_OUTPUT`
- `PERMISSION_DENIED`
- `UNKNOWN_BACKEND`

离线 wheel 不完整但允许联网时先发送 warning，再进入在线源；所有允许的来源都失败后才发送 `PIP_INSTALL_FAILED`。

### 11.6 `-CheckOnly` 必须只读

`-CheckOnly` 下禁止：

- 运行 pip；
- 创建 `.venv`；
- 创建目标目录；
- 写入 `active_path.txt`；
- 写入 `active_python.txt`；
- 更新已有文件时间；
- 访问网络。

即使环境已经准备好，也不能重写 active 文件。

### 11.7 幂等要求

正常模式连续运行两次时：

- 第二次不得重新安装；
- 不得重复创建 venv；
- active 内容未变化时不得重写文件；
- 第二次所有已满足检查直接返回 `ready`。

## 12. Phase 1 测试矩阵

1. 导入 `json` 成功并返回退出码 0。
2. 故意不存在的模块返回 `IMPORT_VERIFY_FAILED`，不出现 `NativeCommandError`。
3. 临时模块主动抛出 `ImportError("fixture DLL failure")`，完整保留 traceback。
4. 带空格和中文的额外模块路径可以正常导入。
5. 多个模块只有一个失败时，其他模块仍有独立结果。
6. `-CheckOnly` 前后比较文件名、哈希、时间戳，结果完全一致。
7. 正常模式连续运行两次，第二次不执行安装。
8. 安装测试使用假 pip/临时解释器，禁止真实联网。

把 PowerShell 检查加入 `scripts/verify_dev.ps1`，并继续执行 Phase 0 的所有验证。

## 13. Phase 1 验收门

- 已报告的 `python.exe : Traceback ... NativeCommandError` 无法再复现；
- 缺少 `cv2` 时进入安装决策，而不是 PowerShell 终止；
- DLL 导入失败能够显示真实 traceback；
- `-CheckOnly` 被自动测试证明为只读；
- 所有 Phase 0 测试继续通过；
- 提交信息为 `fix: make environment probing deterministic`。

## 14. Phase 1 非目标与回滚

非目标：流式 UI、取消进程、重新设计安装器、重打 release。

回滚只回滚 Phase 1 提交，不得回滚 Phase 0 基线。

---

# Phase 2：异步、流式、可取消的环境预检

## 15. 阶段目标

- 环境日志产生后立即进入 UI；
- PowerShell 运行期间 Tauri 命令保持异步；
- 环境配置和正式流水线由同一个停止按钮管理；
- 第二次启动不会静默杀掉第一个任务；
- 所有成功、失败、取消和竞态路径最终恢复 `Idle`。

## 16. 文件范围

创建：

- `xpano-ui/src-tauri/src/environment.rs`

修改：

- `xpano-ui/src-tauri/src/lib.rs`
- `xpano-ui/src-tauri/src/pipeline.rs`
- `xpano-ui/src-tauri/src/process_job.rs`，仅在现有接口确实不足时修改
- `xpano-ui/src/lib/types.ts`
- `xpano-ui/src/hooks/usePipeline.ts`
- Rust 对应测试

环境协议解析和环境子进程逻辑必须放在 `environment.rs`，禁止继续堆入 `lib.rs`。

## 17. 后端状态机

使用唯一任务状态：

```rust
enum RunPhase {
    Idle,
    Preflight,
    Running,
    Cancelling,
}

struct PipelineState {
    run_id: u64,
    phase: RunPhase,
    pid: Option<u32>,
    cancelled: Option<Arc<AtomicBool>>,
    job: Option<ProcessJob>,
}
```

允许的状态转换：

```text
Idle -> Preflight -> Running -> Idle
Idle -> Preflight -> Cancelling -> Idle
Idle -> Preflight -> Idle
Running -> Cancelling -> Idle
```

禁止：

- `Preflight -> Preflight`；
- `Running -> Preflight`；
- 启动新任务时自动取消旧任务。

第二次启动必须返回稳定错误 `ALREADY_RUNNING`，并保持原任务不变。

## 18. `start_pipeline` 精确流程

1. 改为 `async fn start_pipeline(...)`。
2. 短暂锁定 `PipelineState`。
3. 仅当状态为 `Idle` 时递增 `run_id` 并进入 `Preflight`。
4. 立即释放锁；禁止持有锁跨越 `.await`。
5. 使用 `tauri::async_runtime::spawn_blocking` 管理阻塞式子进程等待。
6. PowerShell 参数全部通过 `Command.arg()` 逐项传递。
7. 传入 `-EmitEvents`。
8. stdout/stderr 设置为 `Stdio::piped()`。
9. 子进程启动后立即把 PID 加入现有 Windows Job Object。
10. 环境成功后重新检查 `run_id` 与当前状态。
11. 只有相同 `run_id` 且仍为 `Preflight` 才能启动 Python 流水线。
12. 环境失败、取消或 Python spawn 失败时都调用 `finish_run(run_id)`。

## 19. 流式读取规则

`environment.rs` 必须：

1. 分别在线程中持续读取 stdout 和 stderr，避免管道死锁。
2. 使用按字节换行读取并通过 `String::from_utf8_lossy()` 解码。
3. 识别 `XPANO_ENV_EVENT:` 前缀并反序列化。
4. 结构化事件映射为 `pipeline:progress`。
5. 无前缀内容作为普通日志发送。
6. stderr 只保留最后 200 行，避免安装日志无限占用内存。
7. 子进程退出后等待两个读取线程结束，保证尾部日志不丢失。

若连续 10 秒没有输出：

- 发送“环境配置仍在运行”的心跳；
- 心跳最多每 10 秒一次；
- 不根据耗时伪造百分比；
- 下载总量未知时使用 `indeterminate: true`。

## 20. 取消与进程树规则

`cancel_pipeline` 同时处理 `Preflight` 和 `Running`：

1. 状态改为 `Cancelling`；
2. 设置取消原子标记；
3. 优先调用 `ProcessJob::terminate()`；
4. Windows 使用 `taskkill /F /T /PID` 作为可见降级路径；
5. 等待进程树退出；
6. 清理 PID、Job Object 和取消标记；
7. 恢复 `Idle`。

所有 watcher 必须比较 `run_id`。旧任务完成事件不得清理后来启动的新任务。

必须测试竞态：预检刚成功但 Python 尚未启动时用户取消。该情况下 Python 流水线不得启动。

## 21. 前端规则

`PipelinePhase` 增加：

```typescript
'environment'
```

`PipelineProgress` 增加：

```typescript
indeterminate?: boolean
errorCode?: string
```

交互要求：

- 环境阶段显示“环境检查”；
- 环境阶段不推进抽帧、对齐、导出三个真实进度；
- 每条环境事件实时进入日志；
- 点击停止后先显示“正在停止”，后端确认后才把 `running` 设为 false；
- 主动取消不得再追加重复的“启动失败”；
- `ALREADY_RUNNING` 只提示已有任务，不清空原任务状态。

## 22. Phase 2 测试矩阵

Rust 单元测试：

1. 正确解析结构化环境事件。
2. 无效 JSON 降级为普通日志。
3. `Idle -> Preflight -> Running -> Idle`。
4. 第二次启动返回 `ALREADY_RUNNING`。
5. 取消预检后不能进入 `Running`。
6. 旧 `run_id` 不能清理新任务。
7. spawn、wait、解析异常后均恢复 `Idle`。

Windows 子进程测试使用临时 PowerShell fixture：

- 立即输出第一条事件；
- 延迟输出第二条事件；
- 支持指定退出码；
- 支持生成子进程，用于验证整棵进程树取消；
- 不运行真实 pip，不访问网络。

GUI 手工验收：

- 第一条环境日志在 1 秒内出现；
- 预检期间窗口可拖动、最小化和切换页面；
- 取消后 3 秒内父子进程全部退出；
- 环境失败后 Python 流水线没有启动；
- 环境成功后 Python 流水线只启动一次；
- 成功、失败、取消后均可再次开始任务。

## 23. Phase 2 验收门

运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_dev.ps1
```

必须确认：

- 环境预检不再使用 `Command.output()`；
- 环境 PID 已纳入 Job Object；
- 取消覆盖环境和正式流水线；
- 所有退出路径恢复 `Idle`；
- 测试过程没有真实下载；
- 提交信息为 `fix: stream and cancel environment preflight`。

## 24. Phase 2 非目标与回滚

非目标：调整 Metashape/COLMAP 算法、重做全部 UI、修改安装器、发布 full/light 包。

若 Phase 2 失败，回滚 Phase 2 提交，保留已经验证的 Phase 0 和 Phase 1。

## 25. 三阶段完成定义

三阶段完成后只能声明：

- 环境判断语义已稳定；
- 环境过程可实时观察并取消；
- 开发验证基线可重复执行。

不能声明现有 release 已更新。发布脚本仍可能通过 `-SkipTauriBuild` 复制旧 EXE；发布可复现性、安装器、full/light 包矩阵和真实干净机器验收属于后续阶段。
