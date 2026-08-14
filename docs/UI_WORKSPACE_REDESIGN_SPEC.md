# xPano 三工作区 UI 重构与前后端适配规格

状态：已确认并实施中（Phase 0 已验收）
目标版本：新工程结构 v2
范围：素材导入与处理、对齐与重建、成果查看与后处理
原则：保留现有视觉 ID，只调整信息架构、交互流程、状态归属和后端契约

## 1. 目标与不变量

### 1.1 产品目标

xPano 从“把全部功能堆在一个工作台”改为三个有明确输入、输出和可恢复状态的工程工作区：

1. 素材导入与处理：完成素材识别、预览、剪辑、抽帧和参与帧筛选。
2. 对齐与重建：完成后端选择、参数配置、对齐、优化、导出和过程监控。
3. 成果查看与后处理：完成点云检查、坐标水平校正、致密化和最终训练点云选择。

三个工作区共享同一个活动工程、任务中心和工程清单。切换页面不得取消后台任务，不得丢失日志或进度，不得依赖浏览器历史恢复工程。

### 1.2 必须保持不变的视觉 ID

- 保留现有 xPano 图标、浅色/深色主题、蓝色品牌色、青色数据色和现有玻璃面板语言。
- 保留 `liquid-panel`、`glass-inset`、`glass-control` 的材质方向，但减少面板套面板。
- 保留 Lucide 图标风格、现有按钮动效、主题切换和窗口控制区。
- 保留点云查看器的全画布布局、左下工具区、右下坐标轴、相机视锥和现有取景逻辑。
- 不制作营销页，不加入新的品牌视觉、插画、渐变球或与当前产品无关的装饰。

### 1.3 数据正确性不变量

- UI 中取消选择只改变“是否参与后续流程”，不得删除源照片或已抽取帧。
- 全景的一组左右鱼眼是一个不可拆分的逻辑帧；勾选状态必须同时作用于左右图。
- 修改出入点或抽帧参数会使该轨道的抽帧结果失效；只取消某些已抽帧图片不会要求重新抽帧，但会使对齐结果失效。
- 对 COLMAP 坐标的旋转必须同时更新三维点和相机外参，不能只移动点或只移动相机中心。
- 标准点云和每次致密化点云都必须保留。切换训练点云只能原子更新兼容入口，不能删除其他版本。
- `colmap/images` 与 `colmap/sparse/0` 的对外目录结构保持不变，LichtFeld 等训练器继续读取标准 COLMAP 目录。
- 所有高影响写盘操作先写临时文件、校验成功后原子替换。失败时旧结果仍可用。

## 2. 当前实现与目标之间的差距

### 2.1 前端结构

- `PipelinePage.tsx` 同时管理导入、工程恢复、参数、剪辑、任务、日志和三个弹窗，约 2000 行。
- `PointCloudViewer.tsx` 同时管理 Three.js、点云读取、相机、致密化、日志和文件应用，约 1800 行。
- `usePipeline()` 挂在工作台页面，页面卸载时会取消后台 pipeline；这与跨工作区运行冲突。
- 当前工程状态主要保存在 `sessionStorage`，不是可验证、可恢复的工程状态。
- 当前点云路由把 Windows 路径当作 URL 参数，页面身份与文件路径耦合。

### 2.2 素材与抽帧

- FFmpeg 已使用 `-progress pipe:1`，能够持续读出 `frame` 和 `out_time_ms`。
- 当前 `ExtractionProgressAggregator` 只输出全部视频的汇总 `current/total`，事件没有 `trackId`，前端无法知道当前是哪条轨道。
- 当前预览事件只携带左右路径，没有工程 ID、轨道 ID、帧 ID和时间戳。
- 全景 FFmpeg 先生成临时平铺文件，结束后再移动进逐帧文件夹。实时预览若直接引用临时路径，文件移动后路径会失效。
- 当前 manifest 只记录最终参与的照片/帧，没有“全部项目项 + 是否启用”的编辑状态。

### 2.3 对齐进度

- Metashape 脚本只在几个 API 调用前后发固定百分比。`matchPhotos`、`alignCameras` 等长任务内部可能长时间没有结构化事件。
- 前端无法从当前百分比可靠判断“全景匹配、释放 Station、导入平面帧、增量对齐”等具体节点。
- COLMAP 当前按命令完成数计算进度，每条命令内部大多只有日志，没有统一的阶段事件。
- 当前 COLMAP plan 只收集 `panorama_video.frames`，会忽略普通视频、标准照片和航拍照片；混合素材选择 COLMAP 时，后端能力与 UI 表达不一致。

### 2.4 点云后处理

- 当前“轴向翻转”只翻转点坐标和相机中心，没有同步更新相机旋转，不能保证投影关系保持不变。
- 当前应用致密化会复制致密点到 `points3D.bin`，随后删除致密候选文件。虽然保留一次稀疏备份，但不支持多个结果和任意可逆切换。

## 3. 总体信息架构

## 3.1 持久应用壳

应用壳在三个工作区之间不卸载：

```text
┌──────────────────────────────────────────────────────────────┐
│ 标题栏：xPano / 主题 / 窗口控制                              │
├──────────────────────────────────────────────────────────────┤
│ 工程栏：工程名 / 路径 / 保存状态 / 环境状态 / 打开工程       │
├──────────────────────────────────────────────────────────────┤
│ [素材与处理]  →  [对齐与重建]  →  [成果与后处理]             │
├──────────────────────────────────────────────────────────────┤
│ 当前工作区                                                    │
├──────────────────────────────────────────────────────────────┤
│ 全局任务条：任务 / 阶段 / 总进度 / ETA / 日志 / 停止          │
└──────────────────────────────────────────────────────────────┘
```

工程栏规则：

- 工程名称使用首个素材名生成，可直接重命名；路径只读显示。
- 修改未落盘时显示小圆点；保存失败显示明确错误，不静默吞掉。
- 环境状态只显示“就绪/缺失/检查中”，点击打开环境设置弹窗。
- 页面刷新、进入点云页或打开设置，不得中断后台任务。

工作区导航规则：

- 三个图标按钮始终可见，使用现有品牌色和玻璃选中态。
- 未满足前置条件的工作区仍可进入查看，但运行按钮禁用并显示缺失项。
- “下一步”只负责导航，不隐式启动任务。
- 对齐成功后自动进入成果页；用户可手动返回前两页查看和修改。

### 3.2 前端状态归属

不新增 Redux/Zustand。第一版使用两个 React Context + `useReducer`：

- `ProjectProvider`
  - 工程路径和工程清单。
  - 素材轨道、媒体项、选择状态和各阶段 revision。
  - 对齐配置、点云版本、坐标变换和当前工作区。
- `JobProvider`
  - 抽帧、对齐、致密化、训练任务的运行快照。
  - 阶段、逐轨进度、总进度、ETA、日志和取消能力。
  - 在应用壳挂载一次，统一订阅 Tauri 事件。

页面组件只读取状态并发出命令，不直接拥有后台进程生命周期。

### 3.3 路由

```text
/#/project/media
/#/project/reconstruction
/#/project/results
```

- 活动工程路径不写入 URL。
- 没有活动工程时，`media` 显示空态；其他页面显示前置条件。
- 点云查看器是 `results` 工作区主体，不再是脱离工程状态的独立应用。
- 纯 COLMAP 文件夹仍可进入 viewer-only 临时会话，但必须与 xPano 工程会话区分。

## 4. 工作区一：素材导入与处理

### 4.1 桌面布局

宽度大于等于 1180px 时使用三栏：

```text
┌──────── 240px ────────┬──────── minmax(520px, 1fr) ────────┬──── 300px ────┐
│ 素材轨道               │ 素材预览 / 编辑                    │ 抽帧任务       │
│ 独立纵向滚动           │ 独立纵向滚动                      │ 总进度与日志   │
└────────────────────────┴────────────────────────────────────┴───────────────┘
```

- 三栏高度都为工作区可用高度，不能靠整页滚动访问关键按钮。
- 左栏和中栏各自滚动；右栏日志滚动，底部“开始抽帧/停止”固定。
- 1024-1179px 时右栏变为抽屉，但全局任务条仍显示进度和开始/停止入口。
- 最小支持窗口为 1024x720；主验收分辨率为 1366x768、1440x900、1920x1080。

### 4.2 左侧素材轨道

空态：

- 保留当前大号加号图标和“点击添加或拖动到此处”文案。
- 整个空态是拖放目标，支持点击文件选择。

轨道行固定结构：

```text
[类型图标] 轨道名称                         [删除]
           类型标签 / 视角标签
           选中项数 / 总项数
           状态或逐轨进度
```

状态颜色：

- 未配置：中性灰点。
- 准备处理：品牌蓝点。
- 处理中：蓝青进度条，显示百分比与 ETA。
- 已处理：绿色状态条和完成图标。
- 已修改、需重抽：橙色提示。
- 失败：红色图标和简短错误，点击进入该轨道详情。

轨道交互：

- 单击选中并在中栏打开编辑器，不再通过再次点击取消选择。
- 删除按钮保留红色危险样式和确认弹窗，只移除工程引用，不删除源文件。
- 完成轨道仍可选中并查看已抽帧缩略图。
- 多轨道按导入顺序执行；完成的轨道不会因后续轨道开始而清除完成态。

### 4.3 照片文件夹编辑器

顶部工具行：

- 轨道名称、素材类型、源路径。
- `全选`、`全不选`、`反选`。
- 筛选：全部、已选择、未选择、读取失败。
- 计数：`已选择 386 / 412`。

缩略图网格：

- 缩略图目标宽度 112-136px，间距 6px，自动填充列数。
- 默认全部选中；选中态显示品牌色描边和右上勾选标识。
- 单击缩略图空白区域打开预览；单击勾选标识切换参与状态。
- `Ctrl+A` 仅在网格聚焦时全选；`Shift` 支持连续范围选择。
- 预览弹窗支持上一张/下一张、缩放、文件名、尺寸和 EXIF 摘要。
- 不把全分辨率照片一次性读入内存。后端生成最长边 320px 的 JPEG/WebP 缩略图并缓存。
- 同时存在的缩略图 DOM 节点不得超过 250；使用虚拟网格或等价窗口化实现。

### 4.4 全景视频编辑器

抽帧前：

- 顶部显示左右两个等宽鱼眼预览，稳定使用 2:1 容器，不因图片到达改变尺寸。
- 两个预览同步显示相同时间点；缺失一路时显示明确错误，不使用另一侧冒充。
- 下方依次为抽帧参数、缩略时间带、出点/入点双把手和时间码。
- 出入点范围外的时间带降低透明度；只有范围内内容参与抽帧。
- 参数使用“帧/秒”；帧数上限会按选区长度反算帧/秒。
- 普通视频额外显示“广角/标准视角”分段按钮。

抽帧中及抽帧后：

- 中栏从视频剪辑器转换为抽取结果网格，不再自动变回剪辑器。
- 每个全景逻辑帧卡片中并排显示左右鱼眼缩略图，勾选作用于整组。
- 新帧按事件到达实时追加，滚动位置不强制跳到底部。
- 顶部保留“修改抽帧范围”命令。使用该命令会先显示“将使当前抽帧失效”的确认，再返回剪辑器。
- 完成后允许取消不满意帧；物理文件保留，只从对齐输入清单排除。

### 4.5 普通视频编辑器

- 结构与全景一致，但预览区只有一个平面画面。
- 抽帧结果每项是一张平面帧缩略图。
- 保留广角/标准视角设置，并在抽帧完成后仍可查看；修改视角只使对齐失效，不要求重新抽帧。

### 4.6 拖入素材弹窗

弹窗不是独立的一套参数模型，必须复用与中栏相同的 `TrackEditorFields`。

尺寸：

- 建议宽 960px，最大宽度 `calc(100vw - 48px)`。
- 最大高度 `calc(100vh - 48px)`，主体独立滚动，底部按钮固定。
- 1366x768 下不得遮挡标题、底部按钮或参数说明。

布局：

```text
┌─────────────────────────────────────────────────────────┐
│ 导入 N 项素材                              [关闭]        │
├──────── 220px ───────┬──────────────────────────────────┤
│ 待导入素材列表        │ 当前素材类型、名称和参数          │
│ 错误项可见            │ 视频显示预览与剪辑器              │
│ 每项显示配置状态      │ 照片显示数量和初始选择说明        │
├───────────────────────┴──────────────────────────────────┤
│ 3 项有效 / 1 项不支持          [取消] [导入有效素材]     │
└─────────────────────────────────────────────────────────┘
```

规则：

- OSV/INSV 类型锁定为全景轨道；普通视频不能伪装成全景。
- 照片文件夹允许标准照片/航拍照片切换。
- 每项必须完成必要参数后才标记为“可导入”。
- 不支持项保留在列表中并解释原因，不静默忽略。
- 导入确认后创建工程轨道；照片明细和缩略图可在主页面继续加载。

### 4.7 逐轨进度与 ETA

右侧监控面板从上到下固定为：

1. 当前素材：轨道名称、素材类型和 `第 N / M 条`。
2. 当前素材进度：当前子阶段、进度条、`current/total`、百分比和当前轨 ETA。
3. 全部素材进度：按全部计划工作量汇总的进度条、完成轨道数和总 ETA。
4. 实时日志：默认显示当前轨日志，可切换“全部日志”。
5. 固定操作区：开始抽帧、停止或重新处理失败轨道。

当前轨完成并切换下一轨时，上一个轨道结果保留在左栏；右栏通过 180-240ms 内容切换动画更新名称和计数，不能把总进度重置为 0。

后端必须把当前汇总回调改为逐轨事件。每个视频轨道至少包含：

1. `probe`：读取时长、流、分辨率和预估帧数。
2. `decode`：FFmpeg 解码和写出临时图像。
3. `finalize`：移动到稳定目录、写 EXIF、创建稳定缩略图和 manifest 条目。
4. `ready`：轨道抽帧结果可筛选。

照片轨道包含：

1. `scan`：枚举支持文件。
2. `metadata`：读取尺寸/EXIF并分组传感器。
3. `thumbnail`：生成缩略图索引。
4. `ready`。

ETA 计算：

- 每轨 `total` 优先使用 ffprobe 时长、出入点和抽帧间隔计算。
- 运行 3 秒或完成至少 3 个工作单元前显示“正在估算”。
- 即时速率使用 EWMA：`rate = 0.25 * currentRate + 0.75 * previousRate`。
- 当前轨 ETA 为 `(total - current) / rate`。
- 总 ETA 为当前轨剩余时间，加所有未开始轨道的预计时间。
- 未开始轨道使用同类素材历史速率；没有历史时用保守默认值并标记为“粗略估算”。
- 历史速率按素材类型、编码、分辨率和是否双鱼眼分桶，存入用户配置，不写入工程。
- ETA 每秒最多更新一次，避免抖动；计划、选区或帧数变化时允许立即重算。

### 4.8 抽帧完成后的状态传播

- 全部轨道 ready 后，工作区状态为 `prepared`。
- 轨道选择变化递增 `alignmentInputRevision`，对齐和成果页标记为 `stale`。
- 轨道抽帧参数变化递增 `mediaRevision`，该轨道变为 `needs_extract`，下游全部 stale。
- 不自动进入对齐页；显示“下一步：对齐与重建”。

### 4.9 状态失效矩阵

| 用户操作 | 是否重做抽帧 | 是否重做对齐 | 是否重做导出/成果 | 备注 |
|---|---:|---:|---:|---|
| 修改视频出入点 | 是，仅该轨 | 是 | 是 | 旧抽帧保留到新结果成功替换 |
| 修改帧/秒或帧数上限 | 是，仅该轨 | 是 | 是 | 重新计算预期帧数和 ETA |
| 普通视频切换广角/标准 | 否 | 是 | 是 | 已抽图片仍有效，初始内参变化 |
| 取消/重新勾选照片 | 否 | 是 | 是 | 只重建 active alignment manifest |
| 取消/重新勾选已抽帧 | 否 | 是 | 是 | 全景左右鱼眼成组变化 |
| 修改 Metashape/COLMAP 参数 | 否 | 是 | 是 | 不修改素材准备状态 |
| 手动修改 PSX 后仅重导出 | 否 | 否 | 是 | 需要明确选择“仅导出”执行计划 |
| 应用世界旋转 | 否 | 否 | 更新几何 revision | 训练状态变 stale |
| 切换训练点云版本 | 否 | 否 | 否 | 只更新 active variant，训练状态变 stale |

## 5. 工作区二：对齐与重建

### 5.1 首次进入引导弹窗

触发条件：

- 工程从未保存过有效对齐配置；或
- 上次配置引用的后端当前不可用；或
- 用户主动选择“重新配置”。

已配置但结果 stale 时不强制弹窗，主页面显示原因和“沿用参数重新对齐”。

向导为三步：

1. 后端选择。
2. 对应后端参数。
3. 输出目录和冲突策略。

底部固定显示 `上一步`、`下一步`；第三步右下角为“一键启动对齐”。关闭向导不会丢失已输入草稿，但未完成草稿不能视为有效配置。

#### 后端选择

- Metashape：显示安装/许可检测结果，推荐混合全景和平面素材使用。
- COLMAP：显示内置版本、CUDA 可用性和当前素材兼容性。
- 后端不可用时卡片可见但禁用，并给出修复入口。
- 在 COLMAP 混合素材后端尚未完成前，必须明确禁用，不得让 UI 启动一个会忽略平面素材的任务。

#### 参数帮助

每个非显然参数右侧使用圆形 `?` 图标。单击弹出小型说明层，包含：

- 参数作用。
- 当前单位和特殊值语义。
- 推荐值。
- 调高/调低对速度、内存和成功率的影响。
- 当前工程建议，基于素材数量和类型计算。

说明层宽 280-340px，支持滚动，不因鼠标移开自动关闭，`Esc` 关闭。

#### 输出目录

- 文件素材默认：`源文件父目录/xPano`。
- 文件夹素材默认：`该文件夹/xPano`，扫描源照片时必须排除这个工程子目录。
- 第一次导入时允许先创建同一路径下的 draft 工程，以便永久保存抽帧结果；向导负责确认最终位置。
- 目标为已有 xPano 工程时提供“打开并继续”，不能静默覆盖。
- 目标为非空普通目录时禁止直接使用，提供自动生成 `xPano_001`。
- 修改已有工程位置使用单独的“移动工程”事务，先检查空间，成功后再更新路径。

### 5.2 主页面三栏布局

```text
┌──── 280px ────┬────────── minmax(480px, 1fr) ──────────┬──── 320px ────┐
│ 参数设置       │ 动态流程图                              │ 总进度与日志   │
│ 独立滚动       │ 当前节点、节点进度、耗时和提示          │ 启动/停止      │
└────────────────┴────────────────────────────────────────┴───────────────┘
```

左栏：

- 顶部后端分段按钮。
- 只显示当前后端参数，不保留无效后端的占位空间。
- 参数按“对齐策略、特征、性能、坐标与导出”分组。
- 参数修改后显示“尚未应用”，不在运行中热修改当前任务。
- 运行期间参数只读，可复制配置。

中栏：

- 流程从上到下，节点间用细线连接。
- 节点状态：pending、running、done、skipped、warning、failed。
- running 节点显示真实计数进度；没有内部计数时显示不确定进度动画、已用时间和心跳。
- 容易长时间无日志的节点固定显示“该步骤计算量较大，期间日志可能暂停，请耐心等待”。
- 完成节点显示耗时；失败节点显示短错误和“展开日志”。
- 当前节点自动滚入可见区域，但用户手动滚动后不抢回滚动位置。

右栏：

- 总进度、总 ETA、已用时间、当前节点和节点 ETA。
- 日志区域持续更新，保留用户向上滚动阅读时的位置。
- 底部固定“一键启动对齐”或“停止任务”。
- 任务结束显示“查看成果”“打开 Metashape 工程”“打开输出目录”。

### 5.3 动态流程图生成规则

流程图不是前端根据日志猜测，而由后端在任务开始前返回 `ExecutionPlan`。每个节点有稳定 `stageId`、标题、权重、是否可能长时间无输出和前置节点。

#### Metashape：全景 + 平面，骨架模式

1. `input.validate`：校验选择项和文件。
2. `metashape.project.create`：创建工程与 chunk。
3. `metashape.pano.import`：导入左右鱼眼和全景组。
4. `metashape.pano.station`：将全景组设为 Station。
5. `metashape.pano.match`：匹配全景素材并保留关键点。
6. `metashape.pano.align`：求解全景骨架。
7. `metashape.pano.release`：将全景组恢复为 Folder。
8. `metashape.pano.optimize`：优化全景骨架。
9. `metashape.frame.import`：导入普通视频帧/照片/航拍。
10. `metashape.frame.match`：匹配新增平面素材并复用已保留的全景关键点。
11. `metashape.frame.align`：不重置已有解，增量接入平面相机。
12. `metashape.all.optimize`：全局优化。
13. `metashape.project.save`：保存 PSX。
14. `coordinate.auto_level`：自动地面方向。
15. `export.images`：切图和普通帧导出。
16. `export.colmap`：写 cameras/images/points3D。
17. `output.validate`：记录数、文件和相机对齐率验证。

若只有全景，跳过平面导入、匹配、接入和全局优化节点。若只有平面素材，跳过全景分支，执行平面导入、匹配、对齐和优化。旧工程中的 `mixed` 配置在加载时归一化为同一稳定分阶段流程，UI 不再暴露联合匹配入口。

#### COLMAP：全景

1. 校验输入和模型。
2. 准备左右鱼眼图像目录与传感器分组。
3. 特征提取。
4. 特征匹配。
5. 增量 mapper。
6. 模型选择和完整性检查。
7. 鱼眼到训练图像导出。
8. 发布标准 COLMAP 目录。
9. 输出验证。

#### COLMAP：平面或混合素材

需要先扩展当前后端：

- 从 manifest 收集普通视频帧、标准照片和航拍照片，不能只收集 panorama frames。
- 按传感器组分别配置相机模型和初始参数，不能对全部素材使用一个全局 camera model。
- 多组 feature extraction 写入同一个数据库，再执行跨组匹配。
- 时间不对应的不同轨道不能默认使用纯 sequential matcher；小中型混合工程推荐 exhaustive，大型工程需要经过验证的检索/词汇树策略。
- 未完成并通过混合数据回归前，UI 不开放 COLMAP 混合模式。

### 5.4 参数初始规范

Metashape：

| 参数 | 默认值 | UI 说明摘要 |
|---|---:|---|
| 策略 | 骨架 | 先建立全景骨架，再增量接入平面素材；混合素材推荐 |
| 关键点上限 | 40000 | 每张图候选关键点；提高会增加时间和内存 |
| 连接点上限 | 0 | 保持当前脚本语义；实现前按安装版本 API 文档确认 0 的准确含义 |
| 向上轴 | +Y | 只决定自动坐标处理目标；成果页仍可手动校正 |

COLMAP：

| 参数 | 默认值 | UI 说明摘要 |
|---|---:|---|
| 密度 | 稳定 | 控制当前已有参数预设，不代表致密化点数 |
| 匹配 | 顺序/自动 | 单一连续视频可顺序；混合、补拍照片自动切换穷举或受验证策略 |
| 最大图像尺寸 | 1600 | 特征提取缩放上限；更大更慢、更占显存 |
| 最大特征数 | 4096 | 每张图 SIFT 特征上限 |
| GPU | 可用则开 | 启动前检测 CUDA，失败回退必须在 UI 中可见 |

### 5.5 对齐 ETA 和长任务反馈

- 有明确计数的导入、导出使用计数 ETA。
- Metashape 长 API 没有可靠内部计数时，不伪造精确百分比；节点显示不确定进度、已用时间和历史估计。
- 后端 supervisor 每 1 秒发送 heartbeat，即使第三方进程没有日志，UI 仍保持“运行中”。
- 总 ETA 使用各 stage 的历史耗时模型，至少按后端、stageId、相机数、图像像素量、GPU 模式分桶。
- 第一次无历史数据时显示“估算中/粗略”，不能显示看似精确但没有依据的时间。
- 每次完成把实际耗时写入用户级性能档案，用滚动中位数抵抗异常值。

## 6. 工作区三：成果查看与后处理

### 6.1 保持现有布局

- 保持当前全屏 Three.js 画布、顶部返回/工程信息、左下统计与工具、右下坐标轴。
- 保持相机视锥显隐、主体/全部取景、重置视角、致密化入口和消息提示位置。
- 新功能以现有工具条按钮和浮动面板加入，不把画布改回卡片布局。

### 6.2 三轴可视化水平校正

交互：

- 工具条新增“水平校正”按钮，激活后在点云焦点中心显示三轴旋转 gizmo。
- 第一版只允许旋转，不开放缩放；平移不是水平校正必要条件，避免无意改变场景原点。
- X/Y/Z 使用红/绿/蓝旋转环，支持自由拖动和可选 1°/5°/15°吸附。
- 拖动时点云和相机视锥作为一个 scene root 实时旋转，地面网格保持世界坐标不动。
- 拖动 gizmo 时暂时禁用 OrbitControls，释放后恢复。
- 面板显示欧拉角仅用于阅读；写盘使用 3x3 正交旋转矩阵，避免欧拉角累计误差。
- 提供重置预览、撤销上一步、应用到工程。未点击应用时不写磁盘。

旋转中心：

- 默认使用当前主体取景包围盒中心。
- 当前一次交互记录旋转矩阵与 pivot；相机和所有点云版本使用同一增量变换。
- 工程最终不累计欧拉角，而保存规范坐标到当前世界坐标的 4x4 齐次刚体矩阵 `worldFromCanonical`。
- 用户在新 pivot 再次旋转时，按 `T(p) * G * T(-p) * worldFromCanonical` 组合，能够正确表达不同旋转中心产生的平移分量。

### 6.3 COLMAP 正确写回数学

COLMAP image 外参满足：

```text
x_camera = R * X_world + t
camera_center C = -R^T * t
```

对世界应用旋转 `G` 和 pivot `p`：

```text
X' = G * (X - p) + p
C' = G * (C - p) + p
R' = R * G^T
t' = -R' * C'
```

必须更新：

- `images.bin` 中每个 image 的 quaternion 和 translation。
- 标准点云及所有致密化版本中每个点的 XYZ。

不得修改：

- `cameras.bin` 内参。
- image ID、camera ID、image name。
- points2D、point3D ID、track 关联、颜色和误差。

只接受 `G^T G ≈ I` 且 `det(G) ≈ +1` 的有限矩阵。单轴镜像的行列式为 -1，不能当作普通四元数旋转写回。现有 X/Y/Z“翻转”按钮应改为明确的“绕该轴旋转 180°”快捷预设，或只做视图预览，不能继续使用不完整镜像写盘逻辑。

### 6.4 原子应用事务

1. 获取项目写锁，拒绝与对齐、致密化或训练写盘并发。
2. 读取不可变基准外参和各点云基准版本。
3. 使用累计 transform 重新从基准生成，避免反复旋转产生漂移。
4. 写入同目录 `.tmp` 文件并 flush。
5. 重新读取临时文件，验证记录数、ID集合、四元数、点数和有限值。
6. 随机抽样验证变换前后投影坐标误差小于容差。
7. 原子替换 `colmap/sparse/0/images.bin` 和活动 `points3D.bin`。
8. 更新工程清单的 transform revision 和校验和。
9. 任一步失败则不替换，UI 显示错误并保留旧文件。

### 6.5 点云版本与训练输入选择

点云版本类型：

- `standard`：对齐导出的原始稀疏点云，永久保留。
- `densified`：每次致密化结果，允许多个 run。
- 后续如有需要可增加 imported，但本次不提前实现。

规范坐标规则：

- `work/geometry/variants` 内所有不可变基准版本都保存于 canonical 坐标系。
- `colmap/sparse/0` 是应用 `worldFromCanonical` 后的当前兼容物化结果。
- 如果在已旋转工程上运行致密化，致密输出位于当前世界坐标；注册为版本前必须乘 `inverse(worldFromCanonical)` 归一化回 canonical 坐标。
- 预览任意版本时，要么在 Three.js scene root 应用 `worldFromCanonical`，要么读取已物化的同 revision 文件；禁止混用 canonical 点与 world 相机。

结果页增加紧凑的点云版本选择器：

```text
训练点云
(●) 标准点云       91,421 点    当前
( ) 致密化 #1     502,118 点    2026-...
( ) 致密化 #2     618,044 点
```

- 单击行先切换画布预览，不立即改变训练输入。
- 点击“设为训练点云”后，原子物化到 `colmap/sparse/0/points3D.bin`。
- 切回标准点云执行同一事务，完全可逆。
- 删除致密化版本必须二次确认，且不能删除标准点云或当前训练点云。
- 致密化完成后新增版本，不再使用“应用后删除候选”的逻辑。
- 每个版本记录点数、创建时间、来源任务、文件校验和、transform revision 和状态。

### 6.6 对外 COLMAP 兼容目录

```text
<project>/
  xpano_project.json
  colmap/
    images/
    sparse/0/
      cameras.bin
      images.bin
      points3D.bin          # 当前训练点云，兼容所有外部软件
  work/
    geometry/
      base_images.bin       # 不可变外参基准
      variants/
        standard/points3D.bin
        densified_<id>/points3D.bin
```

外部软件看到的 `colmap` 结构不变；版本管理文件全部放在 `work` 下。

## 7. 工程文件 v2

新增根目录权威文件 `xpano_project.json`。现有 `xpano_manifest.json` 退化为“当前对齐输入 manifest”，由工程状态生成，不再承担 UI 编辑状态。

建议结构：

```json
{
  "schemaVersion": 3,
  "projectId": "uuid",
  "name": "CAM_001",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "activeWorkspace": "media",
  "revisions": {
    "media": 3,
    "alignmentInput": 7,
    "alignment": 5,
    "geometry": 2
  },
  "tracks": [
    {
      "id": "uuid",
      "type": "panoramic_video",
      "label": "大厅全景",
      "sourcePath": "D:/capture/CAM.OSV",
      "sourceFingerprint": { "size": 0, "mtimeNs": 0 },
      "cameraProfile": null,
      "trim": { "start": 0.0, "end": 100.0 },
      "extraction": { "framesPerSecond": 1.0, "frameLimit": 0 },
      "status": "prepared",
      "items": [
        {
          "id": "frame_00001",
          "timestamp": 0.0,
          "selected": true,
          "left": "work/frames/.../left.jpg",
          "right": "work/frames/.../right.jpg",
          "thumbnailLeft": "work/thumbnails/.../left.jpg",
          "thumbnailRight": "work/thumbnails/.../right.jpg"
        }
      ]
    }
  ],
  "reconstruction": {
    "status": "complete",
    "inputRevision": 7,
    "backend": "metashape",
    "config": {},
    "projectPath": "work/metashape/xpano.psx",
    "colmapPath": "colmap"
  },
  "geometry": {
    "transform": {
      "worldFromCanonical": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
      "revision": 2
    },
    "activeVariantId": "standard",
    "variants": []
  },
  "jobs": []
}
```

路径规则：

- 工程内生成物统一保存相对路径，禁止写开发机绝对路径。
- 外部源素材允许绝对路径，同时保存 size/mtime fingerprint。
- 工程移动后生成物仍可用；源素材缺失时轨道显示 missing，可重新定位。

写入规则：

- 所有修改经 Tauri 命令写入，不让前端直接拼 JSON。
- 使用 `xpano_project.json.tmp` 写入、flush、parse-back 后原子替换。
- 每次写入增加 revision，后端命令带 `expectedRevision`，防止旧页面覆盖新状态。

## 8. 项目拖入与恢复

识别顺序：

1. 拖入目录自身或向上查找有限层级的 `xpano_project.json`。
2. 找到后按 v2 工程载入，进入保存的 `activeWorkspace`；如果不存在则进入第一个未完成工作区。
3. 校验源文件、抽帧项、PSX、COLMAP 和点云版本，分别标记 ready/stale/missing/corrupt。
4. 恢复三个工作区的参数、选择状态、缩略图、最后任务日志和成果选择。
5. 只有找不到 xPano 工程标记、但目录包含合法 COLMAP 时，才进入 viewer-only 模式。

中断恢复：

- 已完成轨道直接复用。
- 正在处理的轨道使用临时目录；重开后删除该轨道不完整临时结果并从该轨重新开始。
- 不重新处理此前已完成轨道。
- 对齐中断保留 PSX 和日志，但标记 failed/interrupted；是否复用 PSX 由后端明确判断，不由 UI 猜测。

本规格不要求兼容没有 `xpano_project.json` 的旧工程结构；旧目录只按普通 COLMAP viewer-only 或不支持处理，避免隐式错误恢复。

## 9. 统一任务与事件协议

所有长任务使用同一事件封装：

```json
{
  "schemaVersion": 1,
  "sequence": 42,
  "timestamp": "ISO-8601",
  "projectId": "uuid",
  "jobId": "uuid",
  "workspace": "media",
  "kind": "stage.progress",
  "stageId": "extract.decode",
  "trackId": "uuid-or-null",
  "state": "running",
  "current": 18,
  "total": 100,
  "unit": "frame",
  "percent": 18.0,
  "etaSeconds": 47,
  "message": "正在抽取 18/100 帧",
  "payload": {}
}
```

事件类型：

- `job.started`、`job.completed`、`job.failed`、`job.cancelled`。
- `stage.started`、`stage.progress`、`stage.heartbeat`、`stage.completed`、`stage.skipped`、`stage.failed`。
- `artifact.created`、`preview.item`、`log.line`。

协议规则：

- `(jobId, sequence)` 唯一且严格递增；前端 reducer 幂等忽略旧事件。
- 所有 stage 必须先 started，再 completed/failed。
- 每个运行 stage 至少每秒 heartbeat，确保 UI 不静默。
- preview payload 必须含稳定 itemId、trackId、缩略图路径和最终 artifact 相对路径。
- 事件追加写入 `work/jobs/<jobId>/events.ndjson`，日志写 `job.log`。
- Tauri 增加 `get_job_snapshot(projectId)`，页面挂载时先取快照再订阅事件，避免漏事件。
- 取消请求先进入 `cancelling`，只有进程树退出后才显示 cancelled。

### 9.1 Tauri 命令契约

| 命令 | 关键输入 | 返回 | 写盘行为 |
|---|---|---|---|
| `create_project` | name, firstSource, optionalRoot | ProjectSnapshot | 原子创建 v2 工程 |
| `open_project` | droppedPath | ProjectSnapshot + ValidationReport | 只读校验，不自动修复 |
| `analyze_import_paths` | paths | ImportCandidate[] | 不写工程 |
| `commit_import` | projectId, expectedRevision, drafts | ProjectSnapshot | 添加轨道并保存 |
| `list_track_items` | projectId, trackId, cursor, filter | MediaItemPage | 分页返回缩略项 |
| `set_track_item_selection` | projectId, trackId, itemIds, selected, expectedRevision | RevisionResult | 只更新选择与 stale 状态 |
| `update_track_settings` | trim, extraction, cameraProfile | RevisionResult | 按失效矩阵更新状态 |
| `start_media_job` | projectId, targetTrackIds | JobSnapshot | 创建 job 和临时目录 |
| `build_execution_plan` | projectId, reconstructionConfig | ExecutionPlan | 不启动进程 |
| `start_reconstruction_job` | projectId, planId, expectedRevision | JobSnapshot | 锁定输入 revision 后启动 |
| `get_job_snapshot` | projectId | JobSnapshot[] | 只读恢复状态 |
| `cancel_job` | projectId, jobId | JobSnapshot | 先 cancelling，进程退出后 cancelled |
| `list_point_variants` | projectId | PointCloudVariant[] | 只读 |
| `preview_point_variant` | projectId, variantId | PointCloudDescriptor | 不改变训练输入 |
| `apply_world_transform` | projectId, matrix, pivot, expectedGeometryRevision | GeometryResult | 校验后原子写回 |
| `set_active_point_variant` | projectId, variantId, expectedGeometryRevision | GeometryResult | 原子物化兼容 points3D.bin |
| `delete_point_variant` | projectId, variantId | GeometryResult | 禁止删除 standard/active |

所有写命令必须返回新的工程 revision 和结构化错误码。前端不得通过错误字符串判断状态，错误至少区分：`revision_conflict`、`missing_source`、`invalid_project`、`job_conflict`、`disk_full`、`invalid_geometry`、`artifact_corrupt`、`backend_unavailable`。

### 9.2 ExecutionPlan 契约

```json
{
  "planId": "uuid",
  "projectId": "uuid",
  "inputRevision": 7,
  "backend": "metashape",
  "nodes": [
    {
      "stageId": "metashape.frame.match",
      "label": "匹配新增普通素材",
      "dependsOn": ["metashape.frame.import"],
      "weight": 0.17,
      "progressMode": "indeterminate",
      "slowHint": true,
      "skipReason": null
    }
  ]
}
```

- 前端只渲染后端返回的节点，不复制一份流程判断代码。
- `inputRevision` 或对齐配置变化后旧 `planId` 失效，启动命令返回 `revision_conflict`。
- 节点权重只用于总进度和 ETA，不能替代节点真实状态。
- `progressMode` 为 `counted`、`indeterminate` 或 `external_percent`；三者对应不同 UI，不得统一伪装成百分比条。

## 10. 前后端模块拆分

前端建议：

```text
src/app/
  AppShell.tsx
  ProjectProvider.tsx
  JobProvider.tsx
src/features/media/
  MediaWorkspace.tsx
  TrackList.tsx
  TrackEditor.tsx
  PhotoGrid.tsx
  FramePairGrid.tsx
  VideoClipEditor.tsx
  MaterialImportDialog.tsx
  ExtractionMonitor.tsx
src/features/reconstruction/
  ReconstructionWorkspace.tsx
  ReconstructionWizard.tsx
  BackendSettings.tsx
  ExecutionGraph.tsx
  ReconstructionMonitor.tsx
src/features/results/
  ResultsWorkspace.tsx
  ThreeViewport.ts
  ViewerToolbar.tsx
  LevelingGizmo.ts
  PointCloudVariants.tsx
  DensifyPanel.tsx
src/features/jobs/
  jobTypes.ts
  jobReducer.ts
  JobBar.tsx
  JobLogDrawer.tsx
```

后端建议：

```text
src-tauri/src/
  project.rs             # v2 工程读取、校验、原子保存
  media.rs               # 素材分析、缩略图分页、选择更新
  jobs.rs                # 任务注册、快照、事件持久化
  reconstruction.rs      # plan 生成和 pipeline supervisor
  geometry.rs            # 点云版本、旋转事务、COLMAP 校验
scripts/
  project_manifest.py
  extraction_pipeline.py
  reconstruction_events.py
  colmap_transform.py
```

先拆责任再搬代码，不在同一提交同时重写算法和视觉样式。

## 11. 视觉与交互设计规范

### 11.1 尺寸

- 标题栏：40px，外边距保持当前 8px。
- 工程栏：40px。
- 工作区导航：44px。
- 全局任务条：48-52px。
- 主面板圆角沿用现有 14px；内部工具面板 8-10px；按钮 6-8px。
- 图标按钮命中区最小 32x32px，主命令高度 40-44px。
- 正文最小 12px；参数和值 12-13px；只有路径、计数和次要元数据允许 10-11px。

### 11.2 层级

- 一个工作区最多三个一级面板，不在一级面板内继续堆多个浮动卡片。
- 分组优先使用标题、分隔线和背景层级，卡片只用于重复素材项、弹窗和结果版本。
- 每页只保留一个视觉主按钮：开始抽帧、启动对齐或应用坐标。
- 次要命令使用玻璃控制；危险操作使用红色背景或红色边框。

### 11.3 颜色与状态

- 品牌蓝：可执行、选中、当前节点。
- 数据青：进度、数据结果、ETA。
- 成功绿：已完成且结果有效。
- 警告橙：stale、需重新处理、粗略估计。
- 危险红：失败、删除、不可恢复操作。
- 不能只靠颜色表达状态，必须同时提供图标或文本。

### 11.4 滚动与弹层

- 左列表、中编辑器、右日志分别管理滚动位置。
- 主命令、弹窗底栏和任务停止按钮不能随内容滚走。
- 所有弹层使用 portal，避免 `.liquid-panel { position: relative }` 覆盖 fixed 定位。
- 下拉菜单、帮助层和预览弹窗必须在 1366x768 内自动翻转方向并限制最大高度。

### 11.5 动效

- 保留现有 180-460ms 的按压、切页和玻璃高亮。
- 任务进度使用线性或缓出，不通过动画伪造未上报的进度。
- 运行中的 heartbeat 只做低频呼吸，不持续大范围粒子动画占用 GPU。
- 尊重 `prefers-reduced-motion`。

## 12. 错误处理

- 源照片缩略图生成失败：单项降级，显示失败占位，轨道仍可编辑。
- ffprobe 失败但 FFmpeg 可读：允许抽帧，ETA 标记不可用；错误必须写日志。
- manifest、PSX、COLMAP 缺失：工程进入 repair 状态，禁止错误复用。
- 对齐、点云旋转、训练点云切换等高影响操作无安全 fallback 时立即失败，不继续写盘。
- 事件解析失败：保留原始日志并显示“结构化进度不可用”，不能静默冻结进度条。
- 磁盘空间不足：开始抽帧、对齐、致密化和旋转前分别估算并阻止启动。

## 13. 实施阶段

### 阶段 0：契约冻结与基线

- 为 v2 project、JobEvent、ExecutionPlan 和 point variant 建立 JSON schema/类型。
- 固化当前正确的 Metashape 20 帧和 COLMAP 20 帧回归数据。
- 增加当前页面截图和任务事件基线。

### 阶段 1：应用壳与工程状态

- 建立 AppShell、ProjectProvider、JobProvider 和三个嵌套路由。
- 把任务监听从 `PipelinePage` 提升到应用级。
- 实现 project v2 原子读写、revision 和拖入恢复。
- 当前旧 UI 暂时挂到 reconstruction route，保证功能不中断。

### 阶段 2：素材工作区

- 拆出素材列表、编辑器、导入弹窗和缩略图服务。
- 将抽帧从一键总 pipeline 拆为可独立执行的 prepare job。
- 增加逐轨事件、稳定缩略图、逐轨 ETA 和筛选状态。
- 根据选择项生成对齐输入 manifest。

### 阶段 3：对齐工作区

- 实现配置向导、参数帮助和三栏页面。
- 后端生成 ExecutionPlan，Metashape/COLMAP 发稳定 stageId 和 heartbeat。
- 实现输入组合对应的动态流程图。
- 先开放现有已验证后端组合；COLMAP 混合素材通过回归后再解除禁用。

### 阶段 4：成果与后处理

- 从 PointCloudViewer 拆出 viewport、致密化和点云版本状态。
- 实现旋转 gizmo 和非写盘预览。
- 实现正确外参/点云变换、原子事务和投影不变量测试。
- 改造致密化为版本新增，支持可逆训练点云选择。

### 阶段 5：性能、视觉与打包

- 完成 5000 项缩略图性能测试和点云大数据测试。
- 完成 1024/1366/1440/1920 分辨率 Playwright 截图检查。
- 检查 full/light 包中的新 schema、脚本、缩略图工具和离线依赖。
- 更新 README 和工程格式文档。

## 14. 验收标准

### 14.1 素材工作区

- 混合导入 1 个 OSV、1 个普通视频、2 个照片文件夹，所有轨道独立显示。
- 每条视频轨都有自己的 current/total/percent/ETA；第二条开始时第一条仍为绿色完成态。
- 抽帧中网格持续新增，不等待整个任务结束后一次显示。
- 抽帧完成后页面仍为帧网格；取消任意项后重开工程，选择状态保持。
- 全景任意逻辑帧只出现一个勾选状态，左右图不会分离参与。
- 5000 张缩略图滚动无明显卡顿，DOM 同时不超过 250 项。

### 14.2 对齐工作区

- 三种输入组合分别生成正确流程：仅全景、仅平面、全景+平面分阶段模式；旧 `mixed` 配置生成相同的分阶段流程。
- 所有后台长节点每秒至少有 heartbeat，UI 不出现静默假死。
- 没有真实内部计数的节点显示不确定进度，不伪装为精确百分比。
- 任务成功后自动进入成果页；失败后停留并定位失败节点。
- COLMAP 在支持混合前明确禁用；支持后测试证明所有选择素材都进入数据库和最终模型。

### 14.3 成果工作区

- 拖动 gizmo 时点云、相机视锥同步，地面网格不动。
- 应用旋转后 points3D 与 images 记录数和 ID 不变。
- 随机投影样本变换前后像素误差在数值容差内。
- 在写入中注入失败，旧 `images.bin/points3D.bin` 仍可读取。
- 标准点云和两个致密化结果同时存在，可来回切换训练点云，重开工程后保持。

### 14.4 工程恢复

- 分别在导入后、抽帧中断后、抽帧完成后、对齐完成后、致密化完成后重开工程。
- 三个工作区均恢复正确状态、参数、选择项、日志和可继续操作入口。
- 拖入工程根目录进入工程工作区；拖入纯 COLMAP 目录才直接进入 viewer-only。

### 14.5 UI

- 1366x768 下无关键按钮遮挡、无整页不可达内容、无状态面板破坏网格。
- 所有面板文字对比度清晰，正文不低于 12px。
- 浅色、深色和系统主题下弹窗、帮助层、日志和点云工具都可读。

## 15. 实现前官方资料复核清单

当前机器访问外部官方站点返回连接重置，以下链接尚未在本轮在线读取。进入实现阶段前需在网络恢复后逐项复核版本相关 API：

- FFmpeg `-progress`：https://ffmpeg.org/ffmpeg-doc.html
- COLMAP output format 与 image pose 约定：https://colmap.github.io/format.html
- Three.js TransformControls：https://threejs.org/docs/pages/TransformControls.html
- Tauri 2 frontend events/state：https://v2.tauri.app/develop/calling-frontend/
- React reducer + context：https://react.dev/learn/scaling-up-with-reducer-and-context
- Metashape 2.3 Python API：https://www.agisoft.com/pdf/metashape_python_api_2_3_0.pdf

在官方资料可访问前，COLMAP 变换方案必须以本项目二进制读写器和“投影保持不变”自动测试作为硬性正确性证据，不能只凭视觉判断。

### 15.1 本机已验证的工具能力

- 当前依赖为 React 19.2.7、Tauri 2.11.3、Three.js 0.184.0。
- 本机 FFmpeg `-h full` 明确提供 `-progress <url>` 和 `-stats_period <time>`，可以按固定周期输出机器可读进度。
- 当前 Three.js 源码包含 `TransformControls`、`rotationSnap` 和各轴控制，可直接支撑三轴旋转 gizmo，不需要自行绘制交互轴。
- 内置 COLMAP 为 `4.1.0.dev0 (5b76f53)`；`feature_extractor` 提供 `image_list_path`、`existing_camera_id`、`camera_model`、`camera_params` 和多种 single-camera 选项，为分传感器组分批提取提供接口基础。
- 内置 COLMAP 提供 sequential、exhaustive、spatial、transitive、vocab-tree 等 matcher，但每种策略仍需在目标混合数据上验证后才可由 UI 自动选择。
- 内置 COLMAP 提供 `model_transformer --transform_path`。实现坐标写回时应将它作为优先候选或交叉校验器；其矩阵文件格式和版本行为需在官方文档恢复访问后确认。
- 当前这份内置 COLMAP 明确显示 `without CUDA`。环境检测必须读取实际 build capability；“用户有 NVIDIA 显卡”不等于当前 COLMAP 可使用 CUDA。
