import { useContext } from 'react'
import { JobContext } from './jobContext'

export function useJob() {
  const value = useContext(JobContext)
  if (!value) throw new Error('useJob must be used inside JobProvider')
  return value
}
