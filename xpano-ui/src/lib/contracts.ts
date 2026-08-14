export const XPANO_PROJECT_SCHEMA_VERSION = 3 as const
export const XPANO_JOB_EVENT_SCHEMA_VERSION = 1 as const
export const XPANO_EXECUTION_PLAN_SCHEMA_VERSION = 1 as const

export type ProjectWorkspace = 'media' | 'reconstruction' | 'results' | 'training'
export type ProjectTrackType = 'panoramic_video' | 'ordinary_video' | 'standard_photos' | 'aerial_photos'
export type ProjectTrackStatus = 'draft' | 'prepared' | 'running' | 'ready' | 'stale' | 'missing' | 'failed' | 'interrupted'
export type ReconstructionStatus = 'idle' | 'ready' | 'running' | 'complete' | 'stale' | 'failed' | 'interrupted' | 'repair'
export type ReconstructionBackend = 'metashape' | 'colmap'
export type TrainingStatus = 'idle' | 'ready' | 'running' | 'complete' | 'stale' | 'failed' | 'interrupted'
export type PointVariantKind = 'standard' | 'densified' | 'imported'
export type PointVariantStatus = 'ready' | 'missing' | 'corrupt' | 'stale'
export type JobState = 'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'skipped' | 'interrupted'
export type ProgressMode = 'counted' | 'indeterminate' | 'external_percent'

export type ProjectErrorCode =
  | 'revision_conflict'
  | 'missing_source'
  | 'invalid_project'
  | 'invalid_media_type'
  | 'project_exists'
  | 'job_conflict'
  | 'disk_full'
  | 'invalid_geometry'
  | 'artifact_corrupt'
  | 'backend_unavailable'

export type Matrix4 = readonly [
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
]

export const IDENTITY_WORLD_FROM_CANONICAL: Matrix4 = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1,
]

export interface ProjectRevisions {
  media: number
  alignmentInput: number
  alignment: number
  geometry: number
}

export interface SourceFingerprint {
  size: number
  mtimeNs: number
}

export interface ExtractionSettings {
  framesPerSecond: number
  frameLimit: number
  styleLutPath?: string | null
  colorLutPreset?: string | null
}

export interface ProjectMediaItem {
  id: string
  timestamp?: number | null
  selected: boolean
  left?: string
  right?: string
  thumbnailLeft?: string
  thumbnailRight?: string
  image?: string
  thumbnail?: string
}

export interface ProjectTrack {
  id: string
  type: ProjectTrackType
  label: string
  sourcePath: string
  sourceFingerprint: SourceFingerprint
  cameraProfile: 'standard' | 'wide' | null
  trim: { start: number; end: number } | null
  extraction: ExtractionSettings
  status: ProjectTrackStatus
  items: ProjectMediaItem[]
}

export interface ReconstructionState {
  status: ReconstructionStatus
  inputRevision: number
  backend: ReconstructionBackend
  config: Record<string, unknown>
  projectPath: string | null
  colmapPath: string | null
}

export interface TrainingState {
  status: TrainingStatus
  inputRevision: number
  config: Record<string, unknown>
  outputPath: string | null
  artifactPath: string | null
  sourceJobId: string | null
  lastIteration: number
  totalIterations: number
  lastLoss: number | null
  splatCount: number
  error: string | null
}

export interface WorldTransform {
  worldFromCanonical: Matrix4
  revision: number
}

export interface PointCloudVariant {
  id: string
  label: string
  kind: PointVariantKind
  canonicalPath: string
  pointCount: number
  createdAt: string
  sourceJobId: string | null
  protected: boolean
  checksumSha256: string
  transformRevision: number
  status: PointVariantStatus
}

export interface GeometryState {
  transform: WorldTransform
  activeVariantId: string
  variants: PointCloudVariant[]
}

export interface JobSnapshot {
  jobId: string
  projectRoot?: string | null
  taskId?: string | null
  workspace: ProjectWorkspace
  state: JobState
  stageId: string | null
  sequence: number
  startedAt: string
  updatedAt: string
}

export interface XpanoProjectV2 {
  schemaVersion: typeof XPANO_PROJECT_SCHEMA_VERSION
  projectId: string
  name: string
  createdAt: string
  updatedAt: string
  activeWorkspace: ProjectWorkspace
  revision: number
  revisions: ProjectRevisions
  tracks: ProjectTrack[]
  reconstruction: ReconstructionState
  training: TrainingState
  geometry: GeometryState
  jobs: JobSnapshot[]
}

export type JobEventKind =
  | 'job.started'
  | 'job.completed'
  | 'job.failed'
  | 'job.cancelled'
  | 'stage.started'
  | 'stage.progress'
  | 'stage.heartbeat'
  | 'stage.completed'
  | 'stage.skipped'
  | 'stage.failed'
  | 'artifact.created'
  | 'preview.item'
  | 'log.line'

export interface JobEvent {
  schemaVersion: typeof XPANO_JOB_EVENT_SCHEMA_VERSION
  sequence: number
  timestamp: string
  projectId: string
  projectRoot?: string | null
  jobId: string
  taskId?: string | null
  workspace: ProjectWorkspace
  kind: JobEventKind
  stageId: string | null
  trackId: string | null
  state: JobState
  current: number | null
  total: number | null
  unit: string | null
  percent: number | null
  etaSeconds: number | null
  message: string
  payload: Record<string, unknown>
}

export interface JobRecovery {
  snapshots: JobSnapshot[]
  events: JobEvent[]
}

export interface ExecutionPlanNode {
  stageId: string
  label: string
  dependsOn: string[]
  weight: number
  progressMode: ProgressMode
  slowHint: boolean
  skipReason: string | null
  estimatedSeconds?: number
}

export interface ExecutionPlan {
  schemaVersion: typeof XPANO_EXECUTION_PLAN_SCHEMA_VERSION
  planId: string
  projectId: string
  inputRevision: number
  backend: ReconstructionBackend
  createdAt: string
  nodes: ExecutionPlanNode[]
}

export interface ProjectCommandError {
  code: ProjectErrorCode
  message: string
  details?: Record<string, unknown>
}

export interface ProjectValidationReport {
  missingSourceTrackIds: string[]
  missingArtifactPaths: string[]
}

export interface MediaImportDraftInput {
  trackType: ProjectTrackType
  label: string
  sourcePath: string
  cameraProfile: 'standard' | 'wide' | null
  trim: { start: number; end: number } | null
  extraction: ExtractionSettings
}

export interface TrackSettingsPatch {
  trim?: { start: number; end: number } | null
  extraction?: ExtractionSettings
  cameraProfile?: 'standard' | 'wide' | null
}

export type MediaItemFilter = 'all' | 'selected' | 'unselected'

export interface MediaItemPage {
  items: ProjectMediaItem[]
  total: number
  nextCursor: number | null
}

export function isRelativeArtifactPath(value: string): boolean {
  const normalized = value.trim().replaceAll('\\', '/')
  return Boolean(normalized)
    && !normalized.startsWith('/')
    && !/^[A-Za-z]:\//.test(normalized)
    && !normalized.split('/').includes('..')
}
