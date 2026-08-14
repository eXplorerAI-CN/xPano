export type BatchQueueState = 'idle' | 'running' | 'stopping'
export type BatchTaskState = 'draft' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export type BatchStageStatus = 'disabled' | 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface BatchStages { media: boolean; reconstruction: boolean; training: boolean }
export interface BatchStageStatuses { media: BatchStageStatus; reconstruction: BatchStageStatus; training: BatchStageStatus }
export interface BatchProgress { percent: number; message: string; current?: number | null; total?: number | null; etaSeconds?: number | null; elapsedSeconds: number }
export interface BatchError { code: string; stage?: string | null; message: string }
export interface BatchPipelineInput {
  mediaTrackIds: string[]
}
export interface BatchTask {
  taskId: string; projectId: string; projectRoot: string; label: string; order: number; configuredRevision: number
  stages: BatchStages; stageStatus: BatchStageStatuses; state: BatchTaskState; currentStage?: string | null
  stageJobIds: Record<string, unknown>; progress: BatchProgress; lastError?: BatchError | null
  pipeline: BatchPipelineInput; createdAt: string; startedAt?: string | null; finishedAt?: string | null; updatedAt: string
}
export interface BatchQueueFile { schemaVersion: number; revision: number; state: BatchQueueState; activeTaskId?: string | null; tasks: BatchTask[] }

export const emptyBatchStages = (): BatchStages => ({ media: true, reconstruction: true, training: true })
export const emptyBatchTask = (): BatchTask => {
  const now = new Date().toISOString()
  return {
    taskId: '', projectId: '', projectRoot: '', label: '', order: 0, configuredRevision: 0,
    stages: emptyBatchStages(),
    stageStatus: { media: 'pending', reconstruction: 'pending', training: 'pending' },
    state: 'draft', currentStage: null, stageJobIds: {},
    progress: { percent: 0, message: '', current: null, total: null, etaSeconds: null, elapsedSeconds: 0 },
    lastError: null, pipeline: { mediaTrackIds: [] },
    createdAt: now, startedAt: null, finishedAt: null, updatedAt: now,
  }
}

export function validateStagePrefix(stages: BatchStages): string | null {
  if (!stages.media && !stages.reconstruction && !stages.training) return '请至少开启素材准备阶段'
  if (stages.reconstruction && !stages.media) return '开启对齐前必须先开启素材准备'
  if (stages.training && !stages.reconstruction) return '开启训练前必须先开启对齐'
  return null
}

export function setBatchStage(stages: BatchStages, key: keyof BatchStages, value: boolean): BatchStages {
  const next = { ...stages, [key]: value }
  if (key === 'media' && !value) {
    next.reconstruction = false
    next.training = false
  }
  if (key === 'reconstruction' && !value) next.training = false
  return next
}

export function enabledStageCount(stages: BatchStages) {
  return Number(stages.media) + Number(stages.reconstruction) + Number(stages.training)
}

export function batchOverallPercent(tasks: BatchTask[]) {
  if (!tasks.length) return 0
  const terminal = new Set<BatchTaskState>(['completed', 'failed', 'cancelled', 'interrupted'])
  return tasks.reduce((sum, task) => sum + (terminal.has(task.state) ? 100 : task.progress.percent), 0) / tasks.length
}

export function batchEditorProjectRoot(existingTaskRoot?: string, requestedRoot?: string | null) {
  return requestedRoot?.trim() || existingTaskRoot?.trim() || ''
}

export function moveBatchTaskIds(taskIds: string[], taskId: string, offset: -1 | 1) {
  const currentIndex = taskIds.indexOf(taskId)
  const nextIndex = currentIndex + offset
  if (currentIndex < 0 || nextIndex < 0 || nextIndex >= taskIds.length) return taskIds
  const next = [...taskIds]
  next.splice(nextIndex, 0, next.splice(currentIndex, 1)[0])
  return next
}

export function batchQueueElapsedSeconds(tasks: BatchTask[]) {
  return tasks.reduce((total, task) => {
    if (task.state === 'draft' || task.state === 'queued') return total
    return total + Math.max(0, task.progress.elapsedSeconds || 0)
  }, 0)
}

export function latestBatchJobId(task: BatchTask) {
  for (const stage of ['training', 'reconstruction', 'media']) {
    const value = task.stageJobIds[stage]
    if (typeof value === 'string' && value) return value
  }
  return null
}

export function batchTaskInputLocked(tasks: BatchTask[], taskId?: string | null) {
  if (!taskId) return false
  return tasks.some((task) => task.taskId === taskId && (task.state === 'queued' || task.state === 'running'))
}
