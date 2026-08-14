import { useContext } from 'react'
import { BatchContext } from './batchContext'

export function useBatch() {
  const value = useContext(BatchContext)
  if (!value) throw new Error('useBatch must be used inside BatchProvider')
  return value
}
