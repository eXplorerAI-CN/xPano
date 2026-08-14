import type { PipelineProgress } from './types'

const zeroPhasePercents = { extract: 0, align: 0, export: 0 }

const phaseLabels: Record<PipelineProgress['phase'], string> = {
  idle: '准备',
  extract: '抽帧',
  align: '对齐',
  export: '导出',
  train: '训练',
  complete: '完成',
  error: '错误',
}

function roundPercent(value: number | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.round(Math.min(100, Math.max(0, value)))
}

export function sanitizeProgress(next: PipelineProgress, prev: PipelineProgress): PipelineProgress {
  const rawPercent = roundPercent(next.percent)
  const allowReset = next.phase === 'idle' || prev.phase === 'complete' || prev.phase === 'error'
  const percent = allowReset ? rawPercent : Math.max(roundPercent(prev.percent), rawPercent)
  const phasePercents = next.phasePercents || zeroPhasePercents
  const nextElapsed = Math.max(0, Math.floor(next.elapsed || 0))
  const keepMonotonicPhasePercents = !allowReset && next.phase !== 'idle'
  const samePhase = next.phase === prev.phase
  const sameStage = next.stage === undefined || next.stage === prev.stage
  const sameTrack = next.trackId === undefined || next.trackId === prev.trackId
  const inheritScope = !allowReset && samePhase && sameStage && sameTrack
  const sameCounterScope = inheritScope && (next.total === undefined || next.total === prev.total)
  let current = next.current
  if (sameCounterScope && next.current === undefined) current = prev.current
  if (sameCounterScope && next.current !== undefined && prev.current !== undefined) {
    current = Math.max(prev.current, next.current)
  }

  return {
    ...next,
    stage: next.stage ?? (inheritScope ? prev.stage : undefined),
    trackId: next.trackId ?? (inheritScope ? prev.trackId : undefined),
    percent,
    current,
    total: next.total ?? (sameCounterScope ? prev.total : undefined),
    etaSeconds: next.etaSeconds ?? (sameCounterScope ? prev.etaSeconds : undefined),
    message: next.message || phaseLabels[next.phase] || '处理中',
    elapsed: allowReset ? nextElapsed : Math.max(prev.elapsed, nextElapsed),
    alignedCameras: next.alignedCameras ?? (allowReset ? undefined : prev.alignedCameras),
    totalCameras: next.totalCameras ?? (allowReset ? undefined : prev.totalCameras),
    alignmentRate: next.alignmentRate ?? (allowReset ? undefined : prev.alignmentRate),
    loss: next.loss ?? (inheritScope ? prev.loss : undefined),
    splatCount: next.splatCount ?? (inheritScope ? prev.splatCount : undefined),
    trainerState: next.trainerState ?? (inheritScope ? prev.trainerState : undefined),
    phasePercents: {
      extract: keepMonotonicPhasePercents
        ? Math.max(roundPercent(prev.phasePercents.extract), roundPercent(phasePercents.extract))
        : roundPercent(phasePercents.extract),
      align: keepMonotonicPhasePercents
        ? Math.max(roundPercent(prev.phasePercents.align), roundPercent(phasePercents.align))
        : roundPercent(phasePercents.align),
      export: keepMonotonicPhasePercents
        ? Math.max(roundPercent(prev.phasePercents.export), roundPercent(phasePercents.export))
        : roundPercent(phasePercents.export),
    },
  }
}
