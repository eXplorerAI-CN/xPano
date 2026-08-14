# xPano 批量模式：调研结论与落地方案

状态：已按方案落地并完成静态逻辑、单元测试、构建与多尺寸 UI 验收；未启动真实 FFmpeg、Metashape、COLMAP 或 LichtFeld 任务，未制作发布包。

## 1. 目标与明确边界

批量模式是一个全局任务调度页，不是第五个工程工作区。它负责把多个独立 xPano 工程按顺序串联为：

```text
任务 A：素材准备 → 对齐/重建 → 高斯训练
任务 B：素材准备 → 对齐/重建 → 高斯训练
任务 C：素材准备 → 对齐/重建 → 高斯训练
```

每个任务拥有自己的工程目录、输入素材、配置、日志和输出。任务之间永远不并行；一个任务失败只影响该任务，队列记录失败后继续下一个任务。

本次不做：并行 GPU 调度、自动重试、跨任务共享素材、阶段内部算法重写、从任意中间文件猜测工程状态、前端定时器驱动夜间流程。

## 2. 当前代码证据

| 区域 | 当前事实 | 对批量模式的影响 |
|---|---|---|
| 路由 | 只有 `/project/media`、`/project/reconstruction`、`/project/results`、`/project/training`，均套 `AppShell` | 新增顶层 `/batch`，不能复用四栏底栏 |
| 工程状态 | `ProjectProvider` 只持有一个 `projectRoot/project` | 批量队列不能靠切换当前工程循环执行 |
| 前端任务 | `JobProvider/usePipeline(projectRoot)` 只订阅当前工程视角 | 批量页必须有独立的 `BatchProvider`；事件要带工程/作业身份 |
| 进程执行 | Rust `AppState` 只有一个 `PipelineState`，`ensure_startable()` 拒绝第二个进程 | 这是现成的串行执行 seam，应在其上排队，不新增并行执行器 |
| 抽帧 | `start_media_job` 写 marker、改 track status，然后调用未注册的 `pipeline.start()` | 先补 registered media job，才能在重启/失败时可靠判定终态 |
| 对齐 | 通过 execution plan 和 `start_reconstruction_job` 创建 registered job | 批量只需调用同一入口 |
| 训练 | `start_training_job` 创建 registered job，参数从 `TrainingConfig` 传入 | 需增加“不启动即保存配置”的小入口，供单页编辑器保存 |
| 持久化 | 工程使用原子 JSON；job 使用 snapshot/events/log | 队列使用同样的原子 JSON；详细日志留在工程 job 目录 |
| 关闭窗口 | 当前窗口销毁会取消 pipeline | 关闭应用时应把当前批量任务标为 interrupted，待下次显式继续；最小化/切路由不应影响 worker |

## 3. 两轮方案迭代

### 方案一：前端队列编排（否决）

React 在 `/batch` 维护任务数组，依次调用三个已有 Tauri command，并在 `pipeline:complete/error` 后进入下一阶段。

否决原因：

1. 队列依赖页面挂载和前端 listener；刷新、路由切换或异常会丢失“下一步做什么”。
2. 现有事件没有 `projectRoot/jobId`，打开任务详情时会把另一个工程的进度显示到当前工程。
3. 抽帧没有 durable `JobSnapshot`，重启后无法区分进程丢失、结果已落盘未提交和真正失败。
4. 全局 `PipelineState` 只在启动瞬间防并发，阶段之间的空隙可被手动按钮抢占。
5. 失败继续、停止、恢复和 revision conflict 会散落在多个 React effect，测试和维护都困难。

### 方案二：Rust application-scoped coordinator（采用）

新增一个小而深的 `batch` module：持久化队列、持有 worker、获得执行租约、启动一个阶段、等待该工程的 durable job 终态、提交任务状态，然后推进下一任务。前端只编辑任务和渲染快照。

采用原因：

- 与已有单进程 `PipelineState` 对齐，天然串行。
- UI 卸载、窗口最小化和路由切换不影响 worker。
- 失败隔离、自动继续和取消语义集中在一个 seam。
- 复用工程 schema、execution plan、JobSnapshot/JobEvent，不复制算法参数。
- 以持久化终态作为真相，重启恢复可以诊断而非猜测。

### 方案三：独立 Python/PowerShell supervisor（不采用）

可脱离 UI，但会复制 Rust 的环境解析、进程树终止、工程事务、job 事件和错误映射；两个 supervisor 最终会产生分叉状态，维护成本高于收益。

## 4. 最终数据模型

队列存放于 Tauri `app.path().app_local_data_dir()/batch/queue.json`，而不是 `localStorage`。写入采用现有原子 JSON 规则；坏文件必须显式报错并备份为 `.corrupt.<timestamp>`，不能静默清空任务。

```text
BatchQueueFile {
  schemaVersion: 1,
  revision: number,
  state: idle | running | stopping,
  activeTaskId: string | null,
  tasks: BatchTask[]
}

BatchTask {
  taskId, projectId, projectRoot, label, order,
  configuredRevision,
  stages: { media: boolean, reconstruction: boolean, training: boolean },
  stageState: { media, reconstruction, training },
  state: draft | queued | running | completed | failed | cancelled | interrupted,
  currentStage: media | reconstruction | training | null,
  stageJobIds: { media?, reconstruction?, training? },
  createdAt, startedAt?, finishedAt?, updatedAt,
  progress: { percent, message, current?, total?, etaSeconds? },
  lastError: { code, stage, message } | null
}
```

规则：

- 阶段开关只允许“前缀”组合：`抽帧`、`抽帧+对齐`、`抽帧+对齐+训练`。关闭前一阶段时，后续开关自动关闭且变为 disabled；不允许隐式跳过前置阶段。
- 参数不在队列里复制。素材、抽帧设置、对齐配置和训练配置以工程文件为权威；`configuredRevision` 用于入队冻结，运行前 revision 不一致则任务失败并提示“配置已改变，请重新保存任务”。
- 新任务在“保存任务”时创建独立 xPano 工程并导入素材，不能默认复用同源目录下已有 `xPano` 工程；用户可选择唯一工程根目录，避免多个任务互相覆盖。
- 运行中的阶段进度只在内存实时广播，阶段切换、终态和错误才强制落盘；这样不会因每条 FFmpeg 日志写队列造成额外 I/O。
- ETA 只采用阶段事件提供的 ETA；没有可靠估计时显示“计算中”，不伪造总剩余时间。

## 5. 后端执行 seam 与状态契约

### 5.1 Coordinator

`BatchCoordinator` 放入 `AppState`，包含：队列 store、worker 控制信号、当前执行租约和取消意图。所有锁按 `batch → pipeline` 顺序获取，避免与手动启动死锁。

worker 的单次循环：

1. 读取并校验 queue；选择第一个 `queued` 任务并原子写入 `running/activeTaskId`。
2. 打开工程，校验 `projectId`、源文件、阶段前缀和 `configuredRevision`。
3. 为当前阶段启动 registered job；记录 `stageJobId`。
4. 以项目 `JobSnapshot` 的 durable 终态为控制真相，约 1 秒轮询；实时进度由 pipeline 事件转发，不由前端轮询。
5. `completed`：刷新工程 revision，标记阶段完成，进入下一阶段；所有启用阶段完成后标记任务完成。
6. `failed/cancelled/interrupted`：原子写入任务终态和错误，释放租约；若是普通阶段失败，继续下一个 queued 任务。
7. 队列文件写入失败、job 快照损坏或执行器状态无法确认时停止整个队列并报告高严重度错误，不继续造成不可追踪结果。

### 5.2 三个阶段的入口

- 抽帧：把 `start_media_job` 改为 `begin_job_impl(ProjectWorkspace::Media)` + 现有 marker，pipeline watcher 统一调用 media finalize/fail 和 `finish_job_impl`。
- 对齐：复用 `build_execution_plan` 后的 `start_reconstruction_job`；队列不自行拼 Metashape/COLMAP 参数。
- 训练：新增 `save_training_config`（只校验和保存配置，不启动进程），启动时仍复用 `start_training_job` 与现有 LFS supervisor。

### 5.3 并发与手动模式

- 队列处于 `running/stopping` 时，手动三阶段启动命令返回可读的 `batch_queue_active`，而不是让用户撞上 `ALREADY_RUNNING`。
- 队列任务工程的素材、抽帧、对齐和训练输入配置在 `queued/running` 状态锁定；只允许查看和切换详情页，禁止改变会影响 revision 的输入。
- “不使用任务模式”是本次会话的导航操作，进入 `/project/media`；它不会清空或暂停队列。若队列占用执行器，手动启动按钮保持禁用并说明原因。
- 关闭窗口时取消当前 native process，将任务记为 `interrupted`，保留其它 queued 任务；下次启动只恢复展示，用户点击“继续队列”后才运行，避免无人确认的重复写入。

### 5.4 事件身份

为 `pipeline:progress/complete/error/preview` 增加可选 `projectRoot`、`jobId`、`taskId`；旧调用保持兼容。`usePipeline` 只接收当前工程事件，`BatchProvider` 只接收其 taskId 的事件，防止详情页串台。

## 6. UI 设计规范

### 6.1 顶层任务列表 `/batch`

- 使用独立 `BatchShell`：保留最新版源码中的单行 xPano 品牌标题栏、环境状态、主题和窗口控制；批量页不显示当前工程名，也不显示手动模式的四栏工作区切换。
- 工作区采用“单一队列账本”而非左侧导航 + 中间列表 + 右侧详情三栏：最新版 xPano 已经通过素材页和训练页验证了全高主面板 + 持续底栏状态的层级，重复的任务侧栏会挤压阶段链。
- 队列面板标题行显示 `任务列表`、紧凑统计和唯一常驻主按钮 `新增任务`；列表固定表头、内部滚动。
- 主体用 `liquid-panel` 表格卡片，不使用开发者命令文本。每行显示：拖拽/序号、任务名、工程路径（截断可复制）、三阶段链、当前阶段、总进度、已用时间、ETA、错误摘要和一个详情箭头。
- 阶段链用三个小节点（素材、对齐、训练）和连接线；禁用为灰色，运行中为品牌蓝，完成为绿色，失败为红色；行内错误只显示一行，点击展开最近 job 日志。
- 底部固定 dock 延续最新版 xPano 的语法：左侧队列状态，中部当前任务与全局细进度，右侧 `开始/继续批量`、运行时条件出现的 `停止队列`、最右 `不使用任务模式`。停止队列必须二次确认。
- 复用 `liquid-panel/mica-card/glass-inset/glass-control/theme-input/text-*`、现有圆角、边框和动效；最低窗口 1024×720，1366×768 为主要验收尺寸。

### 6.2 单页任务编辑器

- `/batch/task/new` 和 `/batch/task/:id/edit` 使用一个页面，不拆成四栏。
- 顶部是任务名、工程目录、保存/取消；其下是三阶段开关条，箭头明确依赖。
- 主体两列：左列素材清单（添加文件/文件夹、类型、删轨、每轨抽帧 FPS/上限/裁剪/LUT）；右列对齐和训练参数。高级参数折叠，默认只显示高频选项和预设。
- 表单只复用现有纯配置类型、校验器和字段组件；不要复制一份 Metashape 或 LichtFeld 参数命令拼接逻辑。
- 保存顺序由后端统一完成：创建独立工程 → 提交素材 → 保存对齐配置 → 保存训练配置 → 最后写入 queue task。中间失败时保留已创建工程并明确提示，不留下“已入队但未配置”的假任务。

### 6.3 详情跳转与手动入口

- 点击 `查看详情` 调用现有 `open_project`，进入该工程最后保存的四栏页面；顶部提供 `返回批量任务`。
- 详情页保持现有 UI，不新增批量专属页面；批量锁定时只禁用会改变输入的按钮。
- “不使用任务模式”只改变路由到 `/project/media`，不改变队列数据；下次应用启动仍默认进入 `/batch`。

## 7. 失败、取消和恢复

| 情况 | 当前任务 | 队列 |
|---|---|---|
| 抽帧/对齐/训练进程返回失败 | `failed`，保存 stage/code/message | 释放租约，自动下一个 `queued` |
| 用户停止当前任务并继续 | `cancelled` | 自动下一个 |
| 用户停止整个队列 | 当前 `cancelled`，未执行任务保留 `queued` | `stopping → idle` |
| 应用关闭/崩溃 | 当前 `interrupted` | 其它 queued 保留；下次需显式继续 |
| 队列 JSON/JobSnapshot 无法持久化 | 不冒险继续 | 整体停止并显示恢复路径 |
| 配置 revision 与入队快照不一致 | `failed`，提示重新保存任务 | 自动下一个 |

默认不自动重试。失败任务提供 `复制为新任务` 或 `重新入队`，重新入队前必须重新读取工程状态并让用户确认，避免重复覆盖已有输出。

## 8. 分阶段落地顺序

1. **纯模型与持久化**：新增 queue schema、原子 store、状态 reducer/transition 函数和迁移/损坏文件测试；不接 UI。
2. **执行租约与事件身份**：在 AppState 加 coordinator/lease，给 pipeline 事件补工程/job 身份；手动启动增加 batch 占用检查。
3. **统一三阶段 job**：素材准备接入 registered job；增加训练配置保存 command；补齐三阶段终态映射。
4. **Rust worker**：实现单任务阶段循环、失败隔离、自动下一个、停止当前/停止队列、窗口关闭标记 interrupted；全程复用现有 start commands。
5. **BatchProvider 与列表页**：订阅 batch snapshot/event，完成独立 `BatchShell`、列表、阶段链、进度/耗时/ETA/错误摘要。
6. **单页编辑器**：素材、抽帧、对齐、训练参数一次配置；依赖开关和保存事务；创建任务时使用唯一工程根目录。
7. **详情与手动模式**：任务详情跳转、返回入口、批量锁定提示、“不使用任务模式”、当前工程事件过滤。
8. **恢复与可观测性**：重启恢复展示、继续队列、日志展开、任务复制/重新入队；补齐诊断信息和高严重度存储失败提示。
9. **验收**：Rust/TS 单测、Python 回归、模拟 worker 终态、真实轻量素材串行 smoke、窗口/路由交互验收；用户明确要求前不打包。

## 9. 验收门槛

- 三个阶段严格按开关依赖执行；无法越级启动。
- 同一时刻最多一个 native pipeline；批量在阶段间也不会被手动任务抢占。
- 一个任务失败后，后续任务仍能开始；失败任务的工程、日志和错误可重新打开。
- 切换路由、最小化窗口不丢进度；详情页不会显示别的工程的进度。
- 重启后不把 running 任务伪装成 completed；用户能看到 interrupted 并显式继续。
- 队列和工程文件均可独立恢复；队列损坏不会静默清空。
- 任务编辑器一页完成素材/抽帧/对齐/训练配置，并与手动模式使用同一份参数校验与命令入口。
- UI 在 1024×720、1366×768、深浅主题下无遮挡，文案面向用户而非内部 stage id。
