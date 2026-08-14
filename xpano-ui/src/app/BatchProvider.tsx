import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { BatchQueueFile } from '../features/batch/batchTypes'
import { commandErrorMessage } from '../lib/commandError'
import { BatchContext, type BatchTaskSubmission } from './batchContext'

const emptyQueue: BatchQueueFile = {
  schemaVersion: 1,
  revision: 0,
  state: 'idle',
  activeTaskId: null,
  tasks: [],
}
export function BatchProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<BatchQueueFile>(emptyQueue)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef(false)

  const reload = useCallback(async () => {
    if (!loadedRef.current) setLoading(true)
    try {
      const next = await invoke<BatchQueueFile>('get_batch_queue')
      setQueue(next)
      setError(null)
    } catch (reason) {
      // Browser preview has no Tauri commands; keep the empty queue usable there.
      if (!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__) setError(null)
      else setError(commandErrorMessage(reason))
    } finally {
      loadedRef.current = true
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])
  useEffect(() => {
    if (!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__) return
    let disposed = false
    const unlisten: Array<() => void> = []
    Promise.all([
      listen<BatchQueueFile>('batch:queue', (event) => {
        if (disposed) return
        loadedRef.current = true
        setLoading(false)
        setQueue(event.payload)
      }),
      listen<{ code?: string; message?: string }>('batch:error', (event) => {
        if (!disposed) setError(commandErrorMessage(event.payload))
      }),
    ])
      .then((disposers) => {
        if (disposed) disposers.forEach((dispose) => dispose())
        else unlisten.push(...disposers)
      })
      .catch((reason) => {
        if (!disposed) setError(commandErrorMessage(reason))
      })
    return () => {
      disposed = true
      unlisten.forEach((dispose) => dispose())
    }
  }, [])

  const saveAndEnqueueTask = useCallback(
    async (input: BatchTaskSubmission) => {
      try {
      const next = await invoke<BatchQueueFile>('save_and_enqueue_batch_task', {
        task: input.task,
        reconstructionBackend: input.reconstructionBackend,
        reconstructionConfig: input.reconstructionConfig,
        reconstructionPlanConfig: input.reconstructionPlanConfig,
        trainingConfig: input.trainingConfig,
      })
        setQueue(next)
        setError(null)
        return next
      } catch (reason) {
        setError(commandErrorMessage(reason))
        throw reason
      }
    },
    [],
  )

  const requeueTask = useCallback(async (taskId: string) => {
    try {
      setQueue(await invoke<BatchQueueFile>('requeue_batch_task', { taskId }))
      setError(null)
      return true
    } catch (reason) {
      setError(commandErrorMessage(reason))
      return false
    }
  }, [])

  const removeTask = useCallback(async (taskId: string) => {
    try {
      setQueue(await invoke<BatchQueueFile>('remove_batch_task', { taskId }))
      setError(null)
    } catch (reason) {
      setError(commandErrorMessage(reason))
    }
  }, [])

  const reorderTasks = useCallback(async (taskIds: string[]) => {
    try {
      setQueue(await invoke<BatchQueueFile>('reorder_batch_tasks', { taskIds }))
      setError(null)
      return true
    } catch (reason) {
      setError(commandErrorMessage(reason))
      return false
    }
  }, [])

  const startQueue = useCallback(async () => {
    try {
      setQueue(await invoke<BatchQueueFile>('start_batch_queue'))
      setError(null)
    } catch (reason) {
      setError(commandErrorMessage(reason))
    }
  }, [])

  const stopQueue = useCallback(async () => {
    try {
      setQueue(await invoke<BatchQueueFile>('stop_batch_queue'))
      setError(null)
    } catch (reason) {
      setError(commandErrorMessage(reason))
    }
  }, [])

  const value = useMemo(
    () => ({
      queue,
      loading,
      error,
      saveAndEnqueueTask,
      requeueTask,
      removeTask,
      reorderTasks,
      startQueue,
      stopQueue,
      reload,
    }),
    [queue, loading, error, saveAndEnqueueTask, requeueTask, removeTask, reorderTasks, startQueue, stopQueue, reload],
  )
  return <BatchContext.Provider value={value}>{children}</BatchContext.Provider>
}
