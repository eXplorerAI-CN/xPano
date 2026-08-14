import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, Check, ChevronDown, CircleAlert, FolderOpen, RotateCcw, Square, TerminalSquare } from 'lucide-react'
import { ConfirmDialog } from '../../components/shared/ConfirmDialog'
import type { TrainingState } from '../../lib/contracts'
import type { PipelineProgress } from '../../lib/types'
import type { TrainingConfig, TrainingWorkspaceMode } from './trainingConfig'

function duration(seconds?: number) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const rounded = Math.round(seconds)
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const rest = rounded % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

function metric(value: number | undefined, digits = 4) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function artifactName(path: string | null) {
  if (!path) return '训练产物'
  return path.replaceAll('\\', '/').split('/').filter(Boolean).pop() || '训练产物'
}

function friendlyError(message: string | null, mode: TrainingWorkspaceMode) {
  if (mode === 'interrupted' || message === 'training was interrupted') return '训练已停止，可以检查运行详情后重新开始。'
  return message || '训练未正常完成，请检查运行详情后重试。'
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt className="text-[11px] text-muted">{label}</dt><dd className="mt-1 truncate text-[12px] font-semibold tabular-nums text-ink" title={value}>{value}</dd></div>
}

interface TrainingDiagnosticsDrawerProps {
  logs: string[]
  elapsed?: number
  defaultOpen?: boolean
}

function TrainingDiagnosticsDrawer({ logs, elapsed, defaultOpen = false }: TrainingDiagnosticsDrawerProps) {
  const [open, setOpen] = useState(defaultOpen)
  const logRef = useRef<HTMLDivElement>(null)
  const userScrolledUpRef = useRef(false)
  const recentLogs = useMemo(() => logs.slice(-120), [logs])

  useEffect(() => {
    if (defaultOpen) setOpen(true)
  }, [defaultOpen])

  useEffect(() => {
    const element = logRef.current
    if (!open || !element || userScrolledUpRef.current) return
    element.scrollTop = element.scrollHeight
  }, [open, recentLogs])

  return (
    <section className="border-t border-line">
      <button type="button" onClick={() => setOpen((value) => !value)} className="motion-press flex h-11 w-full items-center justify-between gap-4 px-5 text-left" aria-expanded={open}>
        <span className="flex min-w-0 items-center gap-2 text-[12px] font-medium text-ink/80"><TerminalSquare className="h-4 w-4 text-brand" />运行详情<span className="text-[11px] font-normal text-muted">{recentLogs.length ? `最近 ${recentLogs.length} 条` : '暂无日志'}</span></span>
        <span className="flex shrink-0 items-center gap-3 text-[11px] text-muted"><span>{duration(elapsed)}</span><ChevronDown className={`h-4 w-4 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} /></span>
      </button>
      {open && <div className="border-t border-line bg-[#080d11] px-3 py-2"><div ref={logRef} onScroll={() => { const element = logRef.current; if (element) userScrolledUpRef.current = element.scrollHeight - element.scrollTop - element.clientHeight >= 12 }} className="h-36 overflow-y-auto overflow-x-hidden font-mono text-[11px] leading-5 text-white/65">{recentLogs.length ? recentLogs.map((line, index) => <p key={`${index}-${line.slice(0, 20)}`} className="break-words">{line}</p>) : <p className="text-white/35">当前任务没有可用日志。</p>}</div></div>}
    </section>
  )
}

interface TrainingTaskViewProps {
  mode: Exclude<TrainingWorkspaceMode, 'setup'>
  training: TrainingState
  config: TrainingConfig
  progress: PipelineProgress
  percent: number
  iteration: number
  total: number
  loss?: number
  splats: number
  logs: string[]
  onCancel: () => void
  onConfigure: () => void
  onOpenOutput: () => void
}

export function TrainingTaskView({ mode, training, config, progress, percent, iteration, total, loss, splats, logs, onCancel, onConfigure, onOpenOutput }: TrainingTaskViewProps) {
  const [confirmStop, setConfirmStop] = useState(false)
  const running = mode === 'running'
  const complete = mode === 'complete'

  if (!running && !complete) {
    const interrupted = mode === 'interrupted'
    return (
      <section className="liquid-panel flex h-full min-h-0 flex-col overflow-hidden rounded-panel">
        <div className="flex min-h-0 flex-1 items-center justify-center px-8 py-6">
          <div className="w-full max-w-[620px] text-center">
            <span className={`mx-auto grid h-12 w-12 place-items-center rounded-full ${interrupted ? 'bg-warning/12 text-warning' : 'bg-danger/12 text-danger'}`}><CircleAlert className="h-5 w-5" /></span>
            <h1 className="mt-4 text-[18px] font-semibold text-ink">{interrupted ? '训练已中断' : '训练失败'}</h1>
            <p className="mx-auto mt-2 max-w-[520px] text-[12px] leading-6 text-muted">{friendlyError(training.error, mode)}</p>
            <div className="mt-5 flex justify-center gap-2"><button type="button" onClick={onConfigure} className="motion-press inline-flex h-10 items-center gap-2 rounded-comfortable bg-brand px-5 text-[12px] font-semibold text-white"><RotateCcw className="h-4 w-4" />返回设置</button>{!interrupted && <button type="button" onClick={onOpenOutput} className="glass-control motion-press inline-flex h-10 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/75"><FolderOpen className="h-4 w-4" />打开诊断目录</button>}</div>
          </div>
        </div>
        <TrainingDiagnosticsDrawer logs={logs} elapsed={progress.elapsed} defaultOpen={mode === 'failed'} />
      </section>
    )
  }

  if (complete) {
    return (
      <section className="liquid-panel flex h-full min-h-0 flex-col overflow-hidden rounded-panel">
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="flex items-start gap-4 border-b border-line pb-5"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-success/12 text-success"><Check className="h-5 w-5" /></span><div><h1 className="text-[18px] font-semibold text-ink">训练完成</h1><p className="mt-1 text-[12px] text-muted">高斯模型已生成并归档到当前工程</p></div></div>
          <dl className="grid grid-cols-3 gap-5 border-b border-line py-5"><SummaryItem label="迭代次数" value={training.totalIterations.toLocaleString()} /><SummaryItem label="最终 Loss" value={metric(training.lastLoss ?? undefined, 6)} /><SummaryItem label="高斯数量" value={training.splatCount ? training.splatCount.toLocaleString() : '—'} /></dl>
          <div className="flex items-center justify-between gap-5 py-5"><div className="min-w-0"><p className="text-[11px] text-muted">训练结果</p><p className="mt-1 truncate text-[13px] font-semibold text-ink" title={training.artifactPath || ''}>{artifactName(training.artifactPath)}</p></div><div className="flex shrink-0 items-center gap-2"><button type="button" onClick={onConfigure} className="glass-control motion-press flex h-10 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/75"><RotateCcw className="h-4 w-4" />再次训练</button><button type="button" onClick={onOpenOutput} className="motion-press flex h-10 items-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-semibold text-white"><FolderOpen className="h-4 w-4" />打开结果目录</button></div></div>
        </div>
        <TrainingDiagnosticsDrawer logs={logs} elapsed={progress.elapsed} />
      </section>
    )
  }

  const statusMessage = progress.message || '正在训练三维高斯模型'
  return (
    <section className="liquid-panel flex h-full min-h-0 flex-col overflow-hidden rounded-panel">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <header className="flex items-start justify-between gap-5 border-b border-line px-6 py-5"><div><div className="flex items-center gap-2 text-[11px] font-medium text-brand"><span className="h-2 w-2 animate-pulse rounded-full bg-brand" />训练进行中</div><h1 className="mt-2 text-[18px] font-semibold text-ink">{iteration.toLocaleString()} / {total.toLocaleString()} 次迭代</h1></div><p className="text-[28px] font-semibold tabular-nums text-ink">{percent.toFixed(1)}%</p></header>

        <div className="px-6 py-5"><div className="h-2 overflow-hidden rounded-full bg-ink/8"><div className="h-full rounded-full bg-brand transition-[width] duration-300" style={{ width: `${percent}%` }} /></div><div className="mt-2 flex items-center justify-between gap-4 text-[11px] text-muted"><span className="truncate">{statusMessage}</span><span className="shrink-0">预计剩余 {duration(progress.etaSeconds)}</span></div></div>

        <dl className="grid grid-cols-3 border-y border-line px-6 py-4"><div className="border-r border-line pr-5"><SummaryItem label="Loss" value={metric(loss, 6)} /></div><div className="border-r border-line px-5"><SummaryItem label="高斯数量" value={splats ? splats.toLocaleString() : '—'} /></div><div className="pl-5"><SummaryItem label="已运行时间" value={duration(progress.elapsed)} /></div></dl>

        <div className="grid grid-cols-[minmax(0,1fr)_280px] gap-6 px-6 py-5"><div><p className="flex items-center gap-2 text-[12px] font-semibold text-ink"><Activity className="h-4 w-4 text-brand" />当前状态</p><p className="mt-2 text-[13px] font-medium text-ink/80">{statusMessage}</p><p className="mt-1 text-[11px] text-muted">训练窗口已打开，关闭训练窗口会结束当前任务。</p></div><div className="border-l border-line pl-5"><p className="text-[12px] font-semibold text-ink">本次配置</p><dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3"><SummaryItem label="方案" value={`${config.iterations.toLocaleString()} 步`} /><SummaryItem label="优化" value={config.strategy.toUpperCase()} /><SummaryItem label="分辨率" value={config.resizeFactor === 'auto' ? `自动 ${config.maxWidth || ''}`.trim() : `1/${config.resizeFactor}`} /><SummaryItem label="外观补偿" value={config.bilateralGrid ? '双边网格开启' : '关闭'} /></dl></div></div>
      </div>

      <div className="flex shrink-0 items-center justify-end border-t border-line px-5 py-3"><button type="button" onClick={() => setConfirmStop(true)} className="motion-press flex h-9 items-center gap-2 rounded-comfortable border border-danger/35 bg-danger/8 px-4 text-[12px] font-semibold text-danger"><Square className="h-3.5 w-3.5 fill-current" />停止训练</button></div>
      <TrainingDiagnosticsDrawer logs={logs} elapsed={progress.elapsed} />
      <ConfirmDialog open={confirmStop} title="停止高斯训练" message="当前训练将立即停止，已生成的中间状态不作为正式结果。" confirmText="停止训练" danger onConfirm={() => { setConfirmStop(false); onCancel() }} onCancel={() => setConfirmStop(false)} />
    </section>
  )
}
