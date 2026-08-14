import { createContext } from 'react'
import type { BatchQueueFile, BatchTask } from '../features/batch/batchTypes'
import type { ReconstructionConfigDraft } from '../features/reconstruction/reconstructionTypes'
import type { TrainingConfig } from '../features/training/trainingConfig'

export interface BatchTaskSubmission {
  task: BatchTask
  reconstructionBackend: ReconstructionConfigDraft['backend'] | null
  reconstructionConfig: Record<string, unknown> | null
  reconstructionPlanConfig: {
    backend: ReconstructionConfigDraft['backend']
    alignmentMode: ReconstructionConfigDraft['alignmentMode']
    metashapePath: string | null
  } | null
  trainingConfig: TrainingConfig | null
}

export interface BatchContextValue {
  queue: BatchQueueFile
  loading: boolean
  error: string | null
  saveAndEnqueueTask: (input: BatchTaskSubmission) => Promise<BatchQueueFile>
  requeueTask: (taskId: string) => Promise<boolean>
  removeTask: (taskId: string) => Promise<void>
  reorderTasks: (taskIds: string[]) => Promise<boolean>
  startQueue: () => Promise<void>
  stopQueue: () => Promise<void>
  reload: () => Promise<void>
}

export const BatchContext = createContext<BatchContextValue | null>(null)
