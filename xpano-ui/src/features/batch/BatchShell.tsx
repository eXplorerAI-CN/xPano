import { useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Clock3,
  Copy,
  GripVertical,
  Images,
  Plus,
  Play,
  RotateCcw,
  ScanLine,
  Sparkles,
  Square,
  Trash2,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useBatch } from '../../app/useBatch'
import { useProject } from '../../app/useProject'
import { ThemeControls } from '../../components/layout/ThemeControls'
import { WindowControls } from '../../components/layout/WindowControls'
import { RuntimeReadinessBadge } from '../../components/layout/RuntimeReadinessBadge'
import { BrandAboutButton } from '../../components/layout/BrandAboutButton'
import { ConfirmDialog } from '../../components/shared/ConfirmDialog'
import type { JobEvent } from '../../lib/contracts'
import type { ResolvedTheme, ThemeMode } from '../../lib/types'
import { batchOverallPercent, batchQueueElapsedSeconds, latestBatchJobId, moveBatchTaskIds, type BatchStageStatus, type BatchTask } from './batchTypes'

interface Props {
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
  onThemeModeChange: (mode: ThemeMode) => void
}

const stageDefinitions = [
  { key: 'media' as const, label: '素材准备', icon: Images },
  { key: 'reconstruction' as const, label: '对齐重建', icon: ScanLine },
  { key: 'training' as const, label: '高斯训练', icon: Sparkles },
]

const statusLabels: Record<BatchTask['state'], string> = {
  draft: '草稿',
  queued: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

function formatTime(seconds?: number | null) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—'
  const safe = Math.max(0, Math.round(seconds))
  const hours = Math.floor(safe / 3600)
  const minutes = Math.floor((safe % 3600) / 60)
  const remaining = safe % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
}

function stageTone(status: BatchStageStatus) {
  if (status === 'completed') return 'done'
  if (status === 'running') return 'active'
  if (status === 'failed') return 'error'
  return 'idle'
}

function StageChain({ task }: { task: BatchTask }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted">
      {stageDefinitions.map((stage, index) => {
        const Icon = stage.icon
        const enabled = task.stages[stage.key]
        return (
          <span key={stage.key} className="flex min-w-0 items-center gap-1">
            <span className={`batch-stage-dot ${stageTone(task.stageStatus[stage.key])} ${enabled ? '' : 'opacity-35'}`}>
              <Icon className="h-3 w-3" />
            </span>
            <span className="hidden truncate xl:inline">{enabled ? stage.label : '未启用'}</span>
            {index < stageDefinitions.length - 1 && <span className="mx-0.5 text-muted/35">→</span>}
          </span>
        )
      })}
    </div>
  )
}

export function BatchShell({ themeMode, resolvedTheme, onThemeModeChange }: Props) {
  void resolvedTheme
  const navigate = useNavigate()
  const { openProject } = useProject()
  const { queue, loading, error, requeueTask, removeTask, reorderTasks, startQueue, stopQueue } = useBatch()
  const [expanded, setExpanded] = useState<string | null>(null)
  const [dragged, setDragged] = useState<string | null>(null)
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<BatchTask | null>(null)
  const [requeueTarget, setRequeueTarget] = useState<BatchTask | null>(null)
  const [taskLogs, setTaskLogs] = useState<Record<string, string[]>>({})
  const [loadingLogTaskId, setLoadingLogTaskId] = useState<string | null>(null)
  const running = queue.state === 'running' || queue.state === 'stopping'
  const tasks = useMemo(() => [...queue.tasks].sort((left, right) => left.order - right.order), [queue.tasks])
  const queued = tasks.filter((task) => task.state === 'queued').length
  const completed = tasks.filter((task) => task.state === 'completed').length
  const failed = tasks.filter((task) => task.state === 'failed').length
  const active = tasks.find((task) => task.taskId === queue.activeTaskId)
  const overall = batchOverallPercent(tasks)
  const elapsed = batchQueueElapsedSeconds(tasks)
  const activeIndex = active ? tasks.findIndex((task) => task.taskId === active.taskId) + 1 : 0
  const finished = tasks.length > 0 && tasks.every((task) => ['completed', 'failed', 'cancelled', 'interrupted'].includes(task.state))
  const resumed = tasks.some((task) => ['failed', 'cancelled', 'interrupted'].includes(task.state))
  const queueLabel = running
    ? `正在运行第 ${activeIndex || 1} / ${tasks.length} 项 · ${active?.label || '当前任务'}`
    : queued
      ? '等待启动'
      : finished
        ? failed
          ? `队列已结束，${failed} 项失败`
          : '全部任务已完成'
        : '尚未添加任务'

  const openTask = async (task: BatchTask) => {
    const opened = await openProject(task.projectRoot)
    if (opened) navigate(`/project/${opened.project.activeWorkspace}?batchTask=${encodeURIComponent(task.taskId)}`)
  }

  const dropBefore = async (targetId: string) => {
    if (!dragged || dragged === targetId || running) return setDragged(null)
    const ids = tasks.map((task) => task.taskId)
    const from = ids.indexOf(dragged)
    const to = ids.indexOf(targetId)
    if (from < 0 || to < 0) return setDragged(null)
    ids.splice(to, 0, ids.splice(from, 1)[0])
    setDragged(null)
    await reorderTasks(ids)
  }

  const moveTask = async (taskId: string, offset: -1 | 1) => {
    if (running) return
    const current = tasks.map((task) => task.taskId)
    const next = moveBatchTaskIds(current, taskId, offset)
    if (next !== current) await reorderTasks(next)
  }

  const toggleExpanded = async (task: BatchTask) => {
    if (expanded === task.taskId) {
      setExpanded(null)
      return
    }
    setExpanded(task.taskId)
    const jobId = latestBatchJobId(task)
    if (!jobId || taskLogs[task.taskId]) return
    setLoadingLogTaskId(task.taskId)
    try {
      const events = await invoke<JobEvent[]>('read_job_events', {
        projectRoot: task.projectRoot,
        jobId,
        afterSequence: 0,
        limit: 4096,
      })
      const lines = events
        .map((event) => event.message.trim())
        .filter(Boolean)
        .slice(-5)
      setTaskLogs((current) => ({ ...current, [task.taskId]: lines }))
    } catch {
      setTaskLogs((current) => ({ ...current, [task.taskId]: [] }))
    } finally {
      setLoadingLogTaskId(null)
    }
  }

  return (
    <div className="app-shell relative z-10 h-screen min-h-[720px] min-w-[1024px] overflow-hidden text-ink">
      <header className="liquid-topbar app-titlebar drag-region flex min-w-0 items-center justify-between px-3.5">
        <div className="flex items-center gap-2.5">
          <BrandAboutButton />
          <span className="titlebar-section-divider" />
          <span className="text-[11px] font-medium text-muted">批量任务</span>
        </div>
        <div className="topbar-control-group no-drag flex items-center gap-1">
          <RuntimeReadinessBadge />
          <ThemeControls themeMode={themeMode} onThemeModeChange={onThemeModeChange} />
          <span className="topbar-control-divider" />
          <WindowControls />
        </div>
      </header>

      <main className="app-workspace min-h-0 overflow-hidden p-4 md:p-6">
        <section className="liquid-panel flex h-full min-h-0 flex-col overflow-hidden">
          <header className="flex shrink-0 items-center justify-between border-b border-ink/[0.08] px-5 py-4">
            <div>
              <h1 className="text-[16px] font-semibold">任务列表</h1>
              <p className="mt-1 text-[11px] text-muted">
                共 {tasks.length} 项 · {running ? '正在串行处理' : `${queued} 项等待中`} · {completed} 项完成{failed ? ` · ${failed} 项失败` : ''}
              </p>
            </div>
            <button
              type="button"
              onClick={() => navigate('/batch/task/new')}
              className="motion-press flex h-9 items-center gap-1.5 rounded-comfortable bg-brand px-3.5 text-[11px] font-semibold text-white shadow-sm hover:bg-brand-hover"
            >
              <Plus className="h-3.5 w-3.5" />
              新增任务
            </button>
          </header>
          {error && <div className="mx-5 mt-3 rounded-comfortable border border-danger/20 bg-danger/8 px-3 py-2 text-[11px] text-danger">{error}</div>}

          <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
            {loading ? (
              <div className="grid h-full place-items-center text-[12px] text-muted">正在读取任务队列…</div>
            ) : !tasks.length ? (
              <div className="grid h-full place-items-center">
                <div className="text-center">
                  <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-brand/8 text-brand">
                    <Plus className="h-5 w-5" />
                  </div>
                  <p className="text-[13px] font-medium">尚未添加批量任务</p>
                  <p className="mt-1 text-[11px] text-muted">把多个工程排好队，夜间自动依次处理</p>
                  <button
                    type="button"
                    onClick={() => navigate('/batch/task/new')}
                    className="mt-4 rounded-comfortable bg-brand px-4 py-2 text-[11px] font-semibold text-white"
                  >
                    新增任务
                  </button>
                </div>
              </div>
            ) : (
              <div className="overflow-hidden rounded-comfortable border border-ink/[0.08]">
                <div className="sticky top-0 z-10 grid grid-cols-[34px_minmax(190px,1.2fr)_minmax(220px,1.4fr)_minmax(170px,1fr)_104px_88px_32px] gap-3 bg-[var(--xp-bg)] px-3 py-2 text-[10px] font-medium text-muted">
                  <span>#</span>
                  <span>任务 / 工程</span>
                  <span>阶段</span>
                  <span>进度</span>
                  <span>时间</span>
                  <span>状态</span>
                  <span />
                </div>
                {tasks.map((task) => (
                  <div
                    key={task.taskId}
                    tabIndex={0}
                    aria-label={`打开任务 ${task.label}`}
                    draggable={!running && task.state !== 'running'}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && event.target === event.currentTarget) void openTask(task)
                    }}
                    onDoubleClick={() => void openTask(task)}
                    onDragStart={() => setDragged(task.taskId)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={() => void dropBefore(task.taskId)}
                    className={`batch-task-row grid grid-cols-[34px_minmax(190px,1.2fr)_minmax(220px,1.4fr)_minmax(170px,1fr)_104px_88px_32px] items-center gap-3 border-t border-ink/[0.06] px-3 py-3 outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${task.state === 'running' ? 'is-running' : ''}`}
                  >
                    <span className="flex items-center gap-1 font-mono text-[10px] text-muted">
                      <GripVertical className="h-3 w-3 opacity-45" />
                      {String(task.order + 1).padStart(2, '0')}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-[12px] font-semibold" title={task.label}>
                        {task.label || '未命名任务'}
                      </p>
                      <p className="mt-0.5 truncate text-[10px] text-muted" title={task.projectRoot}>
                        {task.projectRoot} · {task.pipeline.mediaTrackIds.length} 条素材轨道
                      </p>
                      {task.state === 'failed' && (
                        <p className="mt-1 truncate text-[9px] text-danger">{task.lastError?.message || '任务失败'} · 已跳过，队列继续</p>
                      )}
                    </div>
                    <StageChain task={task} />
                    <div className="min-w-0">
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="truncate text-muted">{task.progress.message || '等待启动'}</span>
                        <span className="ml-2 shrink-0 font-mono text-ink/70">{Math.round(task.progress.percent)}%</span>
                      </div>
                      {task.state !== 'queued' && task.state !== 'draft' && (
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink/[0.08]">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-brand to-data transition-[width] duration-300"
                            style={{
                              width: `${Math.max(0, Math.min(100, task.progress.percent))}%`,
                            }}
                          />
                        </div>
                      )}
                    </div>
                    <span className="text-[9px] text-muted">
                      <span className="flex items-center gap-1">
                        <Clock3 className="h-3 w-3" />
                        {task.state === 'queued' || task.state === 'draft' ? '—' : formatTime(task.progress.elapsedSeconds)}
                      </span>
                      <span className="mt-1 block">
                        {task.state === 'running'
                          ? `ETA ${typeof task.progress.etaSeconds === 'number' ? formatTime(task.progress.etaSeconds) : '计算中'}`
                          : 'ETA —'}
                      </span>
                    </span>
                    <span className={`batch-status-pill state-${task.state}`}>{statusLabels[task.state]}</span>
                    <button
                      type="button"
                      onClick={() => void toggleExpanded(task)}
                      className="motion-press grid h-7 w-7 place-items-center rounded-subtle text-muted hover:bg-ink/[0.05] hover:text-brand"
                      aria-label="任务操作"
                      aria-expanded={expanded === task.taskId}
                    >
                      <ChevronDown className={`h-4 w-4 transition-transform ${expanded === task.taskId ? 'rotate-180' : ''}`} />
                    </button>
                    {expanded === task.taskId && (
                      <div className="col-span-full rounded-subtle bg-ink/[0.025] px-3 py-2.5">
                        <div className="flex items-start justify-between gap-3 text-[10px] text-muted">
                          <div className="min-w-0 flex-1">
                            <p className="truncate">{task.lastError?.message || '工程、日志和阶段详情保存在该任务工程中'}</p>
                            {loadingLogTaskId === task.taskId ? (
                              <p className="mt-1.5 text-[9px]">正在读取最近日志…</p>
                            ) : taskLogs[task.taskId]?.length ? (
                              <div className="mt-1.5 space-y-0.5 font-mono text-[9px] text-ink/65">
                                {taskLogs[task.taskId].map((line, index) => (
                                  <p key={`${index}-${line}`} className="truncate">
                                    {line}
                                  </p>
                                ))}
                              </div>
                            ) : null}
                          </div>
                          <div className="flex shrink-0 flex-wrap justify-end gap-1">
                            <button
                              type="button"
                              disabled={running || task.order === 0}
                              onClick={() => void moveTask(task.taskId, -1)}
                              className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1 disabled:opacity-35"
                            >
                              <ArrowUp className="h-3 w-3" />
                              上移
                            </button>
                            <button
                              type="button"
                              disabled={running || task.order === tasks.length - 1}
                              onClick={() => void moveTask(task.taskId, 1)}
                              className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1 disabled:opacity-35"
                            >
                              <ArrowDown className="h-3 w-3" />
                              下移
                            </button>
                            <button
                              type="button"
                              onClick={() => void navigator.clipboard?.writeText(task.projectRoot)}
                              className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1"
                            >
                              <Copy className="h-3 w-3" />
                              复制路径
                            </button>
                            {['draft', 'failed', 'cancelled', 'interrupted'].includes(task.state) && (
                              <button
                                type="button"
                                onClick={() => navigate(`/batch/task/${task.taskId}/edit`)}
                                className="glass-control rounded-subtle px-2 py-1"
                              >
                                编辑
                              </button>
                            )}
                            {task.state === 'completed' && (
                              <button
                                type="button"
                                onClick={() => navigate(`/batch/task/${task.taskId}/edit?duplicate=1`)}
                                className="glass-control rounded-subtle px-2 py-1"
                              >
                                复制为新任务
                              </button>
                            )}
                            {['failed', 'cancelled', 'interrupted'].includes(task.state) && (
                              <button
                                type="button"
                                onClick={() => setRequeueTarget(task)}
                                className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1"
                              >
                                <RotateCcw className="h-3 w-3" />
                                重新入队
                              </button>
                            )}
                            {!['queued', 'running'].includes(task.state) && (
                              <button
                                type="button"
                                onClick={() => setRemoveTarget(task)}
                                className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1 text-danger"
                              >
                                <Trash2 className="h-3 w-3" />
                                移除
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => void openTask(task)}
                              className="glass-control flex items-center gap-1 rounded-subtle px-2 py-1"
                            >
                              打开详情
                              <ChevronRight className="h-3 w-3" />
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </main>

      <footer className="app-bottom-dock batch-bottom-dock min-w-0">
        <div className="global-job-bar grid min-w-0 items-center gap-3">
          <div className="job-summary flex min-w-0 items-center gap-2">
            <span className={`beacon h-2 w-2 shrink-0 ${running ? '' : 'beacon-idle'}`} />
            <span className="truncate text-[11px] font-medium">{queueLabel}</span>
          </div>
          <div className="job-progress-group flex min-w-0 items-center gap-2">
            <div className="job-progress-track overflow-hidden rounded-full">
              <div className="h-full rounded-full bg-gradient-to-r from-brand to-data" style={{ width: `${overall}%` }} />
            </div>
            <span className="font-mono text-[10px] text-muted">{Math.round(overall)}%</span>
          </div>
          <span className="hidden text-[10px] text-muted md:inline" title={`${completed}/${tasks.length || 0} 完成`}>
            已用 {formatTime(elapsed)}
          </span>
          <div className="flex items-center justify-end gap-1">
            <button
              type="button"
              disabled={running || !queued}
              title={running ? '队列已在运行' : !queued ? '没有等待中的任务' : undefined}
              onClick={() => void startQueue()}
              className="motion-press flex h-8 items-center gap-1.5 rounded-comfortable bg-brand px-3 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Play className="h-3 w-3" />
              {resumed ? '继续批量' : '开始批量'}
            </button>
            {running && (
              <button
                type="button"
                onClick={() => setStopConfirmOpen(true)}
                className="motion-press flex h-8 items-center gap-1.5 rounded-comfortable border border-danger/25 bg-danger/10 px-3 text-[11px] text-danger"
              >
                <Square className="h-3 w-3 fill-current" />
                停止队列
              </button>
            )}
            <button
              type="button"
              onClick={() => navigate('/project/media')}
              className="glass-control motion-press flex h-8 items-center gap-1.5 rounded-comfortable px-3 text-[11px] text-ink/70"
            >
              <ArrowLeft className="h-3 w-3" />
              不使用任务模式
            </button>
          </div>
        </div>
      </footer>
      <ConfirmDialog
        open={stopConfirmOpen}
        title="停止批量队列？"
        message="当前任务将被取消，尚未执行的任务会保留在队列中。"
        confirmText="停止队列"
        danger
        onConfirm={() => {
          setStopConfirmOpen(false)
          void stopQueue()
        }}
        onCancel={() => setStopConfirmOpen(false)}
      />
      <ConfirmDialog
        open={Boolean(removeTarget)}
        title="移除这个任务？"
        message={`只会从队列移除“${removeTarget?.label || ''}”，不会删除工程和输出文件。`}
        confirmText="移除任务"
        danger
        onConfirm={() => {
          const task = removeTarget
          setRemoveTarget(null)
          if (task) void removeTask(task.taskId)
        }}
        onCancel={() => setRemoveTarget(null)}
      />
      <ConfirmDialog
        open={Boolean(requeueTarget)}
        title="重新加入队列？"
        message={`将重新读取“${requeueTarget?.label || ''}”的工程配置，并从素材准备阶段重新执行；已有输出可能被覆盖。`}
        confirmText="确认重新入队"
        onConfirm={() => {
          const task = requeueTarget
          setRequeueTarget(null)
          if (task) void requeueTask(task.taskId)
        }}
        onCancel={() => setRequeueTarget(null)}
      />
    </div>
  )
}
