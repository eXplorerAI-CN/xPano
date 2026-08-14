import { useEffect, useRef } from 'react'
import { Clock3, ExternalLink, FolderOpen, Play, RefreshCw, Square, Terminal, X } from 'lucide-react'
import type { ExecutionPlan } from '../../lib/contracts'
import type { PipelineProgress } from '../../lib/types'

interface ReconstructionMonitorProps {
  plan: ExecutionPlan | null
  progress: PipelineProgress
  logs: string[]
  running: boolean
  canStart: boolean
  blockReason: string
  showReexport: boolean
  canReexport: boolean
  reexportBusy: boolean
  reexportReason: string
  onStart: () => void
  onReexport: () => void
  onStop: () => void
  onOpenOutput: () => void
  onOpenProject: () => void
  onViewResults: () => void
  alignmentReport?: {
    alignedCameras?: number
    totalCameras?: number
    alignmentRate?: number
    warnings?: string[]
  } | null
  components?: Array<{ componentKey: string; alignedCameraCount: number; totalCameraCount: number }>
  exportedComponentKey?: string
  overlay?: boolean
  onClose?: () => void
}

function formatTime(seconds?: number) {
  const safe = Math.max(0, Math.floor(seconds || 0))
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`
}

export function ReconstructionMonitor({ plan, progress, logs, running, canStart, blockReason, showReexport, canReexport, reexportBusy, reexportReason, onStart, onReexport, onStop, onOpenOutput, onOpenProject, onViewResults, alignmentReport, components = [], exportedComponentKey = '', overlay = false, onClose }: ReconstructionMonitorProps) {
  const logRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  useEffect(() => {
    const node = logRef.current
    if (!node || !stickToBottomRef.current) return
    node.scrollTop = node.scrollHeight
  }, [logs])

  const activeNode = plan?.nodes.find((node) => node.stageId === progress.stage)
  const finished = progress.phase === 'complete'
  const historicalTotal = plan?.nodes.reduce((total, node) => total + (node.skipReason ? 0 : node.estimatedSeconds ?? 0), 0) ?? 0
  const historicalRemaining = historicalTotal > 0 ? Math.max(0, historicalTotal - progress.elapsed) : undefined
  const totalEta = typeof progress.etaSeconds === 'number'
    ? formatTime(progress.etaSeconds)
    : typeof historicalRemaining === 'number'
      ? `历史约 ${formatTime(historicalRemaining)}`
      : '正在估算'
  const exportedComponent = components.find((component) => component.componentKey === exportedComponentKey)

  return (
    <aside className={`reconstruction-monitor liquid-panel flex min-h-0 flex-col overflow-hidden p-0 ${overlay ? 'reconstruction-monitor-overlay' : ''}`} data-testid="reconstruction-monitor">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-3.5">
        <div><h2 className="text-[13px] font-semibold text-ink">任务监控</h2><p className="text-[10px] text-muted">进度、ETA 与日志</p></div>
        <div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${running ? 'bg-data shadow-[0_0_0_4px_rgb(var(--xp-data-rgb)/0.12)]' : finished ? 'bg-success' : progress.phase === 'error' ? 'bg-danger' : 'bg-muted/40'}`} />{overlay && <button type="button" onClick={onClose} className="motion-press grid h-7 w-7 place-items-center rounded-comfortable text-muted hover:bg-ink/[0.04] hover:text-ink" aria-label="关闭任务监控"><X className="h-3.5 w-3.5" /></button>}</div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3.5">
        <div className="grid grid-cols-2 gap-2">
          <div className="glass-inset rounded-comfortable p-3"><p className="font-mono text-[18px] font-semibold text-ink">{Math.round(progress.percent)}%</p><p className="mt-1 text-[10px] text-muted">总进度</p></div>
          <div className="glass-inset rounded-comfortable p-3"><p className="font-mono text-[18px] font-semibold text-ink">{formatTime(progress.elapsed)}</p><p className="mt-1 text-[10px] text-muted">已用时间</p></div>
        </div>

        <div className="mt-3 rounded-comfortable border border-[var(--xp-line)] p-3">
          <div className="flex items-center justify-between gap-2 text-[10px] text-muted"><span>整体进度</span><span className="font-mono">{Math.round(progress.percent)}%</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-ink/[0.06]"><div className="h-full rounded-full bg-data transition-[width] duration-300" style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%` }} /></div>
          <div className="mt-2 flex items-center justify-between gap-3 text-[10px] text-muted"><span className="flex min-w-0 items-center gap-1.5"><Clock3 className="h-3.5 w-3.5 shrink-0" /><span className="truncate">ETA {totalEta}</span></span><span className="shrink-0 text-ink/75">{activeNode?.label || (running ? progress.message : '等待启动')}</span></div>
        </div>

        {alignmentReport && <div className="mt-3 border-y border-[var(--xp-line)] py-3 text-[10px]">
          <div className="flex items-center justify-between gap-3"><span className="font-medium text-ink">对齐质量</span><span className="font-mono text-muted">{alignmentReport.alignedCameras ?? 0}/{alignmentReport.totalCameras ?? 0} · {(alignmentReport.alignmentRate ?? 0).toFixed(1)}%</span></div>
          {exportedComponentKey && <div className="mt-2 flex items-center justify-between gap-3 text-muted"><span>当前导出</span><span className="font-mono text-ink/75">Component #{exportedComponentKey}{exportedComponent ? ` · ${exportedComponent.alignedCameraCount} 相机` : ''}</span></div>}
          {(alignmentReport.warnings ?? []).map((warning) => <p key={warning} className="mt-2 leading-4 text-warning">{warning}</p>)}
        </div>}

        <div className="mt-3 flex items-center justify-between"><h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase text-muted"><Terminal className="h-3.5 w-3.5" /> 实时日志</h3><span className="font-mono text-[9px] text-muted/70">{logs.length} 行</span></div>
        <div
          ref={logRef}
          onScroll={(event) => {
            const node = event.currentTarget
            stickToBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 28
          }}
          className="mt-2 h-[220px] overflow-y-auto rounded-comfortable border border-[var(--xp-line)] bg-ink/[0.025] p-2.5 font-mono text-[9px] leading-4 text-muted"
        >
          {logs.length ? logs.map((line, index) => <p key={`${index}-${line}`} className="whitespace-pre-wrap break-words">{line}</p>) : <p className="text-muted/55">任务日志将在启动后显示</p>}
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--xp-line)] p-3">
        {finished && <div className="mb-2 grid grid-cols-3 gap-1.5"><button type="button" onClick={onViewResults} className="glass-control motion-press flex h-8 items-center justify-center gap-1 text-[9px] text-ink/70 hover:text-brand"><ExternalLink className="h-3 w-3" /> 成果</button><button type="button" onClick={onOpenProject} className="glass-control motion-press flex h-8 items-center justify-center gap-1 text-[9px] text-ink/70 hover:text-brand"><ExternalLink className="h-3 w-3" /> PSX</button><button type="button" onClick={onOpenOutput} className="glass-control motion-press flex h-8 items-center justify-center gap-1 text-[9px] text-ink/70 hover:text-brand"><FolderOpen className="h-3 w-3" /> 目录</button></div>}
        {running ? <button type="button" onClick={onStop} className="motion-press flex h-10 w-full items-center justify-center gap-2 rounded-comfortable bg-danger px-4 text-[12px] font-semibold text-white shadow-sm shadow-danger/20"><Square className="h-3.5 w-3.5 fill-current" /> 停止任务</button> : <div className={`grid gap-2 ${showReexport ? 'grid-cols-2' : 'grid-cols-1'}`}><button type="button" onClick={onStart} disabled={!canStart || reexportBusy} title={!canStart ? blockReason : undefined} className="motion-press flex h-10 items-center justify-center gap-2 rounded-comfortable bg-brand px-3 text-[12px] font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-45"><Play className="h-4 w-4 fill-current" /> 一键启动对齐</button>{showReexport && <button type="button" onClick={onReexport} disabled={!canReexport || reexportBusy} title={!canReexport ? reexportReason : '保留 PSX 中的手工相机修正，仅重新导出图像与 COLMAP'} className="glass-control motion-press flex h-10 items-center justify-center gap-1.5 rounded-comfortable px-2 text-[11px] font-semibold text-ink/80 hover:text-brand disabled:cursor-not-allowed disabled:opacity-45"><RefreshCw className={`h-3.5 w-3.5 ${reexportBusy ? 'animate-spin' : ''}`} /> {reexportBusy ? '读取 PSX' : '从 PSX 重新导出'}</button>}</div>}
        {!canStart && !running && <p className="mt-2 text-center text-[9px] leading-4 text-warning">{blockReason}</p>}
        {showReexport && !canReexport && !running && !reexportBusy && <p className="mt-1 text-center text-[9px] leading-4 text-warning">{reexportReason}</p>}
      </div>
    </aside>
  )
}
