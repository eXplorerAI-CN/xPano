export type TrackType = 'panoramic_video' | 'ordinary_video' | 'standard_photos' | 'aerial_photos'
export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type CameraProfile = 'standard' | 'wide'

export interface MaterialTrack {
  id: string
  type: TrackType
  label: string
  path: string
  /** Optional time window (seconds) for video trimming. */
  trim?: { start: number; end: number }
  /** Per-track preparation settings. */
  extract?: { framesPerSecond: number; frameLimit: number; styleLutPath?: string | null; colorLutPreset?: string | null }
  /** Ordinary-video view preset used to initialize flat camera intrinsics. */
  cameraProfile?: CameraProfile
  /** Counts restored from an existing xPano manifest. */
  restoredFrameCount?: number
  restoredPhotoCount?: number
}

export type AlignmentEngine = 'metashape' | 'colmap'
export type MetashapeAlignmentMode = 'backbone' | 'mixed'
export type ColmapDensityPreset = 'stable' | 'high-density' | 'experimental-high-density'
export type ColmapMatcher = 'sequential' | 'exhaustive'

export interface PipelineConfig {
  outputDir: string
  metashapePath: string
  colmapPath: string
  framesPerSecond: number
  frameLimit: number
  alignmentEngine: AlignmentEngine
  // Metashape
  metaAlignmentMode: MetashapeAlignmentMode
  metaKeypointLimit: number
  metaTiepointLimit: number
  metaComponentKey?: string
  upAxis: string
  // COLMAP
  colmapDensityPreset: ColmapDensityPreset
  colmapUseGpu: boolean
  colmapMatcher: ColmapMatcher
  colmapMaxImageSize: number
  colmapMaxNumFeatures: number
}

export interface ProjectRunOptions {
  skipExtract?: boolean
  reexportOnly?: boolean
  existingProjectPath?: string
  manifestPath?: string
  reconstruction?: {
    projectRoot: string
    expectedRevision: number
    planId: string
  }
}

export type PipelinePhase = 'idle' | 'extract' | 'align' | 'export' | 'train' | 'complete' | 'error'

export interface PipelineProgress {
  projectRoot?: string | null; jobId?: string | null; taskId?: string | null
  phase: PipelinePhase; stage?: string; trackId?: string
  percent: number; message: string; elapsed: number
  phasePercents: { extract: number; align: number; export: number }
  current?: number; total?: number; etaSeconds?: number
  alignedCameras?: number; totalCameras?: number; alignmentRate?: number
  loss?: number; splatCount?: number; trainerState?: string
  heartbeat?: boolean
}

export interface PipelineComplete { outputPath: string; jobKind?: 'media' | 'reconstruction' | 'training'; projectRoot?: string | null; jobId?: string | null; taskId?: string | null }
export interface PipelineError { error: string; jobKind?: 'media' | 'reconstruction' | 'training'; projectRoot?: string | null; jobId?: string | null; taskId?: string | null }

export interface PointCloudData {
  points: Float32Array; colors: Float32Array; numPoints: number; cameras: CameraPose[]
  totalPoints?: number; sampled?: boolean; preparedForThree?: boolean; viewBounds?: PointCloudViewBounds
}

export interface PointCloudBounds {
  min: [number, number, number]
  max: [number, number, number]
}

export interface PointCloudViewBounds {
  point: PointCloudBounds
  subject: PointCloudBounds
  all: PointCloudBounds
}

export interface CameraPose {
  id: number
  position: [number, number, number]; rotation: [number, number, number, number]
  frustum?: { fov: number; aspect: number; near: number; far: number }
}
