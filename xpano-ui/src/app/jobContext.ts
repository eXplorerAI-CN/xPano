import { createContext } from 'react'
import type { usePipeline } from '../hooks/usePipeline'

export type JobContextValue = ReturnType<typeof usePipeline>
export const JobContext = createContext<JobContextValue | null>(null)
