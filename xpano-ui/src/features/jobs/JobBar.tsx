import { createPortal } from 'react-dom'
import { CheckCircle2, Clock3, RotateCcw, Square, Terminal, X } from 'lucide-react'
import { useState } from 'react'
import { useJob } from '../../app/useJob'

const phaseLabels = {
  idle: '准备',
  extract: '抽帧',
  align: '对齐',
  export: '导出',
  train: '训练',
  complete: '完成',
  error: '错误',
} as const

function formatTime(seconds?: number) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '--:--'
  const safe = Math.max(0, Math.round(seconds))
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`
}

export function JobBar() {
  const { progress, running, logs, cancel, reset } = useJob()
  const [logsOpen, setLogsOpen] = useState(false)
  const terminalState = progress.phase === 'complete' || progress.phase === 'error'
  const statusLabel = running ? phaseLabels[progress.phase] : terminalState ? phaseLabels[progress.phase] : '无运行任务'

  return (
    <>
      <div className="global-job-bar grid min-w-0 items-center gap-2">
        <div className="job-summary flex min-w-0 items-center gap-2">
          <span className={`beacon h-2 w-2 shrink-0 ${running ? '' : 'beacon-idle'}`} />
          <p className="truncate text-[11px] font-medium text-ink" title={progress.message}>{running ? progress.message : statusLabel}</p>
        </div>

        <div className="job-progress-group flex min-w-0 items-center gap-2">
          <div className={`job-progress-track overflow-hidden rounded-full bg-ink/[0.08] ${running || terminalState ? '' : 'is-idle'}`}>
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand to-data transition-[width] duration-300"
              style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%` }}
            />
          </div>
          <span className="job-progress-percent text-right font-mono text-[10px] text-ink/70">{Math.round(progress.percent)}%</span>
        </div>

        <span className="job-elapsed flex items-center gap-1 text-[10px] text-muted">
          <Clock3 className="h-3 w-3" />
          {running && typeof progress.etaSeconds === 'number' ? `剩余 ${formatTime(progress.etaSeconds)}` : formatTime(progress.elapsed)}
        </span>

        <div className="flex items-center justify-end gap-1">
          <button
            type="button"
            onClick={() => setLogsOpen(true)}
            className="glass-control motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted transition-colors hover:text-brand"
            title="任务日志"
            aria-label="任务日志"
          >
            <Terminal className="h-4 w-4" />
          </button>
          {running ? (
            <button
              type="button"
              onClick={cancel}
              className="motion-press grid h-8 w-8 place-items-center rounded-comfortable border border-danger/25 bg-danger/10 text-danger transition-colors hover:bg-danger/16"
              title="停止任务"
              aria-label="停止任务"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </button>
          ) : terminalState ? (
            <button
              type="button"
              onClick={reset}
              className="glass-control motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted transition-colors hover:text-brand"
              title="清除任务状态"
              aria-label="清除任务状态"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          ) : (
            <CheckCircle2 className="mx-2 h-4 w-4 text-ink/20" aria-hidden="true" />
          )}
        </div>
      </div>

      {logsOpen && createPortal(
        <div className="fixed inset-0 z-[120]" role="presentation" onMouseDown={() => setLogsOpen(false)}>
          <div className="absolute inset-0 bg-ink/10 backdrop-blur-[2px]" />
          <section
            className="app-modal-panel job-log-drawer bottom-[68px] left-2 right-2 ml-auto flex max-h-[min(62vh,520px)] max-w-3xl flex-col overflow-hidden p-0"
            role="dialog"
            aria-modal="true"
            aria-label="任务日志"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="app-modal-header flex h-11 shrink-0 items-center justify-between px-4">
              <div className="flex min-w-0 items-center gap-2">
                <Terminal className="h-4 w-4 text-brand" />
                <span className="text-[12px] font-semibold text-ink">任务日志</span>
                <span className="font-mono text-[10px] text-muted">{logs.length} lines</span>
              </div>
              <button onClick={() => setLogsOpen(false)} className="motion-press grid h-7 w-7 place-items-center rounded-subtle text-muted hover:bg-ink/[0.05] hover:text-ink" aria-label="关闭日志">
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="terminal min-h-36 flex-1 overflow-y-auto rounded-none border-0 px-4 py-3 font-mono text-[11px] leading-5 text-ink/72">
              {logs.length ? logs.map((line, index) => <p key={`${index}-${line}`} className="break-words">{line}</p>) : <p className="text-muted">等待任务启动…</p>}
            </div>
          </section>
        </div>,
        document.body,
      )}
    </>
  )
}
