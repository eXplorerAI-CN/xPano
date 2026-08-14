import { createContext } from 'react'
import type {
  MediaImportDraftInput,
  MediaItemFilter,
  MediaItemPage,
  ProjectValidationReport,
  ReconstructionBackend,
  ProjectWorkspace,
  TrackSettingsPatch,
  XpanoProjectV2,
} from '../lib/contracts'

export interface OpenProjectResult {
  projectRoot: string
  project: XpanoProjectV2
  validation: ProjectValidationReport
}

export type ProjectSaveState = 'saved' | 'saving' | 'error'

export interface ProjectContextValue {
  projectRoot: string
  project: XpanoProjectV2 | null
  validation: ProjectValidationReport | null
  saveState: ProjectSaveState
  error: string
  viewerOnlyPath: string
  legacyName: string
  legacyRoot: string
  pendingDropPaths: string[]
  displayName: string
  displayPath: string
  openProject: (path: string) => Promise<OpenProjectResult | null>
  createProject: (name: string, firstSource: string, optionalRoot?: string) => Promise<OpenProjectResult | null>
  commitImport: (drafts: MediaImportDraftInput[]) => Promise<boolean>
  updateTrackSettings: (trackId: string, patch: TrackSettingsPatch) => Promise<boolean>
  removeTrack: (trackId: string) => Promise<boolean>
  setItemSelection: (trackId: string, itemIds: string[], selected: boolean) => Promise<boolean>
  listTrackItems: (trackId: string, cursor: number, limit: number, filter: MediaItemFilter) => Promise<MediaItemPage | null>
  renameProject: (name: string) => Promise<boolean>
  setWorkspace: (workspace: ProjectWorkspace) => Promise<boolean>
  saveReconstructionConfig: (backend: ReconstructionBackend, config: Record<string, unknown>) => Promise<XpanoProjectV2 | null>
  setViewerOnlyPath: (path: string) => void
  registerLegacySession: (root: string, name: string) => void
  queueDropPaths: (paths: string[]) => void
  consumeDropPaths: () => void
}

export const ProjectContext = createContext<ProjectContextValue | null>(null)
