import { useContext } from 'react'
import { ProjectContext } from './projectContext'

export function useProject() {
  const value = useContext(ProjectContext)
  if (!value) throw new Error('useProject must be used inside ProjectProvider')
  return value
}
