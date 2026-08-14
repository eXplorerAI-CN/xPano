import type { JobEvent, JobSnapshot } from './contracts'
import type { PipelineProgress } from './types'

const activeStates = new Set<JobSnapshot['state']>(['queued', 'running', 'cancelling'])

export interface RecoveredJobView {
  activeJobId: string | null
  running: boolean
  progress: PipelineProgress
  logs: string[]
  sequence: number
}

function phaseFor(snapshot: JobSnapshot): PipelineProgress['phase'] {
  if (snapshot.state === 'completed') return 'complete'
  if (snapshot.state === 'failed' || snapshot.state === 'cancelled' || snapshot.state === 'interrupted') return 'error'
  if (snapshot.workspace === 'media') return 'extract'
  if (snapshot.stageId?.startsWith('export.') || snapshot.stageId === 'output.validate') return 'export'
  return 'align'
}

function elapsedSeconds(snapshot: JobSnapshot, nowMs: number): number {
  const started = Date.parse(snapshot.startedAt)
  const updated = Date.parse(snapshot.updatedAt)
  const end = activeStates.has(snapshot.state) ? nowMs : updated
  if (!Number.isFinite(started) || !Number.isFinite(end)) return 0
  return Math.max(0, Math.floor((end - started) / 1000))
}

function phasePercents(phase: PipelineProgress['phase'], percent: number) {
  if (phase === 'extract') return { extract: percent, align: 0, export: 0 }
  if (phase === 'align') return { extract: 100, align: percent, export: 0 }
  if (phase === 'export') return { extract: 100, align: 100, export: percent }
  if (phase === 'complete') return { extract: 100, align: 100, export: 100 }
  return { extract: 0, align: 0, export: 0 }
}

function fallbackMessage(snapshot: JobSnapshot): string {
  if (snapshot.state === 'cancelling') return '正在取消任务'
  if (snapshot.state === 'completed') return '任务已完成'
  if (snapshot.state === 'cancelled') return '任务已取消'
  if (snapshot.state === 'failed' || snapshot.state === 'interrupted') return '任务已中断'
  return '正在恢复后台任务状态'
}

export function recoverJobView(
  snapshots: JobSnapshot[],
  events: JobEvent[],
  nowMs = Date.now(),
): RecoveredJobView | null {
  const snapshot = snapshots.at(-1)
  if (!snapshot) return null
  const orderedEvents = events
    .filter((event) => event.jobId === snapshot.jobId && event.sequence <= snapshot.sequence)
    .sort((left, right) => left.sequence - right.sequence)
  const latestEvent = orderedEvents.at(-1)
  const latestProgress = [...orderedEvents].reverse().find((event) => (
    event.percent !== null || event.current !== null || event.total !== null || event.etaSeconds !== null
  ))
  const phase = phaseFor(snapshot)
  const percent = snapshot.state === 'completed' ? 100 : Math.min(100, Math.max(0, latestProgress?.percent ?? 0))
  const logs: string[] = []
  for (const event of orderedEvents) {
    if (event.kind === 'stage.heartbeat' || !event.message.trim()) continue
    if (logs.at(-1) !== event.message) logs.push(event.message)
  }
  const running = activeStates.has(snapshot.state)
  return {
    activeJobId: running ? snapshot.jobId : null,
    running,
    sequence: snapshot.sequence,
    logs: logs.slice(-500),
    progress: {
      phase,
      stage: snapshot.stageId ?? undefined,
      percent,
      message: latestEvent?.message || fallbackMessage(snapshot),
      elapsed: elapsedSeconds(snapshot, nowMs),
      phasePercents: phasePercents(phase, percent),
      current: latestProgress?.current ?? undefined,
      total: latestProgress?.total ?? undefined,
      etaSeconds: latestProgress?.etaSeconds ?? undefined,
      heartbeat: latestEvent?.kind === 'stage.heartbeat',
    },
  }
}
