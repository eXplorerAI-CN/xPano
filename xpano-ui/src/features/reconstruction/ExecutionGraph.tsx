import { useEffect, useMemo, useRef } from 'react'
import { AlertCircle, Check, Circle, Clock3, LoaderCircle, PanelRight, SkipForward } from 'lucide-react'
import type { ExecutionPlan, ExecutionPlanNode } from '../../lib/contracts'
import type { PipelineProgress } from '../../lib/types'
import type { PlanNodeState } from './reconstructionTypes'

interface ExecutionGraphProps {
  plan: ExecutionPlan | null
  error: string
  running: boolean
  progress: PipelineProgress
  projectComplete: boolean
  onToggleMonitor: () => void
}

function formatDuration(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds || 0))
  if (safe < 60) return `${safe} 秒`
  return `${Math.floor(safe / 60)} 分 ${safe % 60} 秒`
}

function stateForNode(
  node: ExecutionPlanNode,
  index: number,
  currentIndex: number,
  running: boolean,
  phase: PipelineProgress['phase'],
  projectComplete: boolean,
): PlanNodeState {
  if (node.skipReason) return 'skipped'
  if (phase === 'complete' || projectComplete) return 'done'
  if (currentIndex < 0) return 'pending'
  if (index < currentIndex) return 'done'
  if (index === currentIndex) return phase === 'error' ? 'failed' : running ? 'running' : 'pending'
  return 'pending'
}

function NodeIcon({ state }: { state: PlanNodeState }) {
  const common = 'h-4 w-4'
  if (state === 'done') return <Check className={common} />
  if (state === 'running') return <LoaderCircle className={`${common} animate-spin`} />
  if (state === 'failed') return <AlertCircle className={common} />
  if (state === 'skipped') return <SkipForward className={common} />
  return <Circle className={common} />
}

export function ExecutionGraph({ plan, error, running, progress, projectComplete, onToggleMonitor }: ExecutionGraphProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const lastManualScrollRef = useRef(0)
  const programmaticRef = useRef(false)
  const currentIndex = plan?.nodes.findIndex((node) => node.stageId === progress.stage) ?? -1
  const activeNode = currentIndex >= 0 ? plan?.nodes[currentIndex] : null

  const states = useMemo(() => plan?.nodes.map((node, index) => stateForNode(node, index, currentIndex, running, progress.phase, projectComplete)) ?? [], [currentIndex, plan, progress.phase, projectComplete, running])

  useEffect(() => {
    const container = scrollRef.current
    if (!container || currentIndex < 0 || Date.now() - lastManualScrollRef.current < 3500) return
    const target = container.querySelector<HTMLElement>(`[data-plan-index="${currentIndex}"]`)
    if (!target) return
    const nextTop = Math.max(0, target.offsetTop - container.clientHeight * 0.32)
    if (Math.abs(container.scrollTop - nextTop) < 12) return
    programmaticRef.current = true
    container.scrollTo({ top: nextTop, behavior: 'smooth' })
    window.setTimeout(() => { programmaticRef.current = false }, 360)
  }, [currentIndex])

  return (
    <section className="liquid-panel flex min-h-0 flex-col overflow-hidden p-0" data-testid="reconstruction-execution-graph">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-4">
        <div>
          <h2 className="text-[13px] font-semibold text-ink">动态执行流程</h2>
          <p className="text-[10px] text-muted">由后端按当前素材组合生成</p>
        </div>
        <div className="flex items-center gap-2">
          {plan && <span className="rounded-full bg-brand/8 px-2 py-1 font-mono text-[10px] text-brand">{plan.nodes.filter((node) => !node.skipReason).length} 步</span>}
          <button type="button" onClick={onToggleMonitor} className="glass-control motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:text-brand xl:hidden" aria-label="显示任务监控"><PanelRight className="h-4 w-4" /></button>
        </div>
      </header>

      <div
        ref={scrollRef}
        onScroll={() => { if (!programmaticRef.current) lastManualScrollRef.current = Date.now() }}
        className="execution-graph-scroll relative min-h-0 flex-1 overflow-y-auto px-5 py-5"
      >
        {error ? (
          <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
            <span className="grid h-11 w-11 place-items-center rounded-full bg-danger/10 text-danger"><AlertCircle className="h-5 w-5" /></span>
            <h3 className="mt-3 text-[13px] font-semibold text-ink">暂时无法生成执行流程</h3>
            <p className="mt-1 text-[11px] leading-5 text-muted">{error}</p>
          </div>
        ) : !plan ? (
          <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center text-center text-muted">
            <LoaderCircle className="h-5 w-5 animate-spin text-brand" />
            <p className="mt-3 text-[11px]">正在根据素材和参数生成流程</p>
          </div>
        ) : (
          <div className="relative mx-auto w-full max-w-3xl pb-4">
            <div className="absolute bottom-8 left-[21px] top-8 w-px bg-[var(--xp-line-strong)]" />
            {plan.nodes.map((node, index) => {
              const state = states[index]
              const current = index === currentIndex
              const counted = current && progress.current !== undefined && progress.total !== undefined && progress.total > 0
              const nodePercent = counted ? Math.max(0, Math.min(100, progress.current! / progress.total! * 100)) : 0
              return (
                <article key={node.stageId} data-plan-index={index} className={`execution-node relative mb-2.5 ml-0 grid min-h-[66px] grid-cols-[44px_minmax(0,1fr)] rounded-comfortable border px-0 py-0 transition-colors ${state === 'running' ? 'border-brand/35 bg-brand/[0.055]' : state === 'failed' ? 'border-danger/35 bg-danger/[0.045]' : 'border-[var(--xp-line)] bg-[rgb(var(--xp-surface-rgb)/0.18)]'}`}>
                  <div className="relative z-10 grid place-items-center">
                    <span className={`grid h-7 w-7 place-items-center rounded-full border bg-[rgb(var(--xp-surface-rgb))] ${state === 'done' ? 'border-success/40 text-success' : state === 'running' ? 'border-brand/50 text-brand shadow-[0_0_0_4px_rgb(var(--xp-brand-rgb)/0.08)]' : state === 'failed' ? 'border-danger/50 text-danger' : state === 'skipped' ? 'border-[var(--xp-line)] text-muted/55' : 'border-[var(--xp-line-strong)] text-muted/65'}`}><NodeIcon state={state} /></span>
                  </div>
                  <div className="min-w-0 py-3 pr-3.5">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0"><h3 className={`truncate text-[12px] font-semibold ${state === 'skipped' ? 'text-muted/60' : 'text-ink'}`}>{node.label}</h3><p className="mt-0.5 truncate font-mono text-[9px] text-muted/65">{node.stageId}</p></div>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-medium ${state === 'done' ? 'bg-success/10 text-success' : state === 'running' ? 'bg-brand/10 text-brand' : state === 'failed' ? 'bg-danger/10 text-danger' : state === 'skipped' ? 'bg-ink/[0.035] text-muted' : 'bg-ink/[0.035] text-muted'}`}>{state === 'done' ? '完成' : state === 'running' ? '运行中' : state === 'failed' ? '失败' : state === 'skipped' ? '跳过' : '等待'}</span>
                    </div>
                    {state === 'skipped' && <p className="mt-2 text-[10px] text-muted/70">{node.skipReason}</p>}
                    {state === 'running' && (
                      <div className="mt-2.5">
                        {counted ? (
                          <><div className="h-1.5 overflow-hidden rounded-full bg-ink/[0.06]"><div className="h-full rounded-full bg-data transition-[width] duration-300" style={{ width: `${nodePercent}%` }} /></div><div className="mt-1.5 flex items-center justify-between text-[9px] text-muted"><span>{progress.current}/{progress.total}</span><span>{Math.round(nodePercent)}%</span></div></>
                        ) : (
                          <div className="execution-indeterminate h-1.5 overflow-hidden rounded-full bg-ink/[0.06]"><span className="block h-full w-1/3 rounded-full bg-data" /></div>
                        )}
                        <div className="mt-2 flex items-center gap-3 text-[9px] text-muted"><span className="flex items-center gap-1"><Clock3 className="h-3 w-3" /> 已用 {formatDuration(progress.elapsed)}</span>{progress.heartbeat && <span className="text-success">心跳正常</span>}</div>
                        {node.slowHint && <p className="mt-2 text-[10px] leading-4 text-warning">该步骤计算量较大，期间日志可能暂停，请耐心等待。</p>}
                      </div>
                    )}
                  </div>
                </article>
              )
            })}
            {activeNode && <p className="sr-only">当前步骤：{activeNode.label}</p>}
          </div>
        )}
      </div>
    </section>
  )
}
