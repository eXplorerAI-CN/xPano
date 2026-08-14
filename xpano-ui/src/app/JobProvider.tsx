import type { ReactNode } from 'react'
import { usePipeline } from '../hooks/usePipeline'
import { JobContext } from './jobContext'
import { useProject } from './useProject'

export function JobProvider({ children }: { children: ReactNode }) {
  const { projectRoot } = useProject()
  const pipeline = usePipeline(projectRoot)
  return <JobContext.Provider value={pipeline}>{children}</JobContext.Provider>
}
