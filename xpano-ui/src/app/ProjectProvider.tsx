import { useCallback, useEffect, useMemo, useReducer, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type {
  MediaImportDraftInput,
  MediaItemFilter,
  MediaItemPage,
  ProjectValidationReport,
  ReconstructionBackend,
  TrackSettingsPatch,
  XpanoProjectV2,
  ProjectWorkspace,
} from '../lib/contracts'
import { normalizeDisplayPath } from '../lib/paths'
import { commandErrorMessage } from '../lib/commandError'
import { ProjectContext, type OpenProjectResult, type ProjectContextValue, type ProjectSaveState } from './projectContext'

interface ProjectState {
  projectRoot: string
  project: XpanoProjectV2 | null
  validation: ProjectValidationReport | null
  saveState: ProjectSaveState
  error: string
  viewerOnlyPath: string
  legacyName: string
  legacyRoot: string
  pendingDropPaths: string[]
}

type ProjectAction =
  | { type: 'opening' }
  | { type: 'settled' }
  | { type: 'opened'; result: OpenProjectResult }
  | { type: 'saved'; project: XpanoProjectV2 }
  | { type: 'error'; message: string }
  | { type: 'viewer'; path: string }
  | { type: 'legacy'; name: string; root: string }
  | { type: 'queue-drop'; paths: string[] }
  | { type: 'consume-drop' }

const devPreviewMode = import.meta.env.DEV && typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('preview') : null
const devTrainingStatus = devPreviewMode === 'training-running'
  ? 'running'
  : devPreviewMode === 'training-complete'
    ? 'complete'
    : devPreviewMode === 'training-failed'
      ? 'failed'
      : devPreviewMode === 'training-interrupted'
        ? 'interrupted'
        : 'idle'
const devTrainingConfig = {
  iterations: 30000,
  strategy: 'mrnf',
  shDegree: 3,
  maxGaussians: 1_000_000,
  resizeFactor: 'auto',
  maxWidth: 3840,
  testEvery: 0,
  useCpuCache: true,
  useFsCache: true,
  centralize: 'off',
  undistort: false,
  enableMip: false,
  bilateralGrid: true,
  enableEval: false,
  backgroundMode: 'solidcolor',
  backgroundColor: '#000000',
  gui: true,
}

const devPreviewProject: XpanoProjectV2 = {
  schemaVersion: 3,
  projectId: 'dev-phase3-project',
  name: '道路空间混合采集',
  createdAt: '2026-07-10T08:00:00Z',
  updatedAt: '2026-07-10T08:15:00Z',
  activeWorkspace: devPreviewMode?.startsWith('training') ? 'training' : devPreviewMode === 'phase4' ? 'results' : 'reconstruction',
  revision: 8,
  revisions: { media: 3, alignmentInput: 7, alignment: 5, geometry: 1 },
  tracks: [
    {
      id: 'preview-pano', type: 'panoramic_video', label: '道路全景主轨', sourcePath: 'D:/captures/CAM_0133_D.OSV', sourceFingerprint: { size: 1024, mtimeNs: 1 }, cameraProfile: null, trim: { start: 0, end: 100 }, extraction: { framesPerSecond: 1, frameLimit: 100 }, status: 'ready',
      items: Array.from({ length: 100 }, (_, index) => ({ id: `frame_${String(index + 1).padStart(5, '0')}`, timestamp: index, selected: index % 11 !== 0, left: `work/frames/pano/${index + 1}/left.jpg`, right: `work/frames/pano/${index + 1}/right.jpg`, thumbnailLeft: `work/thumbnails/pano/${index + 1}/left.jpg`, thumbnailRight: `work/thumbnails/pano/${index + 1}/right.jpg` })),
    },
    {
      id: 'preview-flat', type: 'ordinary_video', label: '补拍广角视频', sourcePath: 'D:/captures/reference.mp4', sourceFingerprint: { size: 1024, mtimeNs: 1 }, cameraProfile: 'wide', trim: { start: 0, end: 40 }, extraction: { framesPerSecond: 1, frameLimit: 40 }, status: 'ready',
      items: Array.from({ length: 40 }, (_, index) => ({ id: `frame_${String(index + 1).padStart(5, '0')}`, timestamp: index, selected: true, image: `work/frames/flat/frame_${String(index + 1).padStart(5, '0')}.jpg`, thumbnail: `work/thumbnails/flat/frame_${String(index + 1).padStart(5, '0')}.jpg` })),
    },
  ],
  reconstruction: { status: devPreviewMode === 'phase4' ? 'complete' : 'stale', inputRevision: 5, backend: 'metashape', config: { alignmentMode: 'backbone', metashapeKeypointLimit: 40000, metashapeTiepointLimit: 0, upAxis: '+Y', alignmentManifestPath: 'work/manifests/alignment_00000007.json', mediaManifestPath: 'work/manifests/media_full.json' }, projectPath: 'work/metashape/xpano.psx', colmapPath: 'colmap' },
  training: {
    status: devTrainingStatus,
    inputRevision: devTrainingStatus === 'idle' ? 0 : 1,
    config: devTrainingStatus === 'idle' ? {} : devTrainingConfig,
    outputPath: devTrainingStatus === 'idle' ? null : 'work/training/runs/training-preview',
    artifactPath: devTrainingStatus === 'complete' ? 'work/training/runs/training-preview/xpano_gaussian.ply' : null,
    sourceJobId: devTrainingStatus === 'idle' ? null : 'training-preview',
    lastIteration: devTrainingStatus === 'complete' ? 30000 : devTrainingStatus === 'idle' ? 0 : 12480,
    totalIterations: devTrainingStatus === 'idle' ? 0 : 30000,
    lastLoss: devTrainingStatus === 'idle' ? null : 0.031284,
    splatCount: devTrainingStatus === 'idle' ? 0 : 824512,
    error: devTrainingStatus === 'failed' ? '显存不足，训练进程已退出。' : devTrainingStatus === 'interrupted' ? 'training was interrupted' : null,
  },
  geometry: {
    transform: { worldFromCanonical: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], revision: 1 },
    activeVariantId: 'standard',
    variants: devPreviewMode === 'phase4' ? [
      { id: 'standard', label: '标准点云', kind: 'standard', canonicalPath: 'work/geometry/variants/standard/points3D.bin', pointCount: 91421, createdAt: '2026-07-10T08:15:00Z', sourceJobId: 'preview-job', protected: true, checksumSha256: 'a'.repeat(64), transformRevision: 1, status: 'ready' },
      { id: 'densified-preview-1', label: '致密化 #1', kind: 'densified', canonicalPath: 'work/geometry/variants/densified-preview-1/points3D.bin', pointCount: 502118, createdAt: '2026-07-10T08:18:00Z', sourceJobId: 'dense-job-1', protected: false, checksumSha256: 'b'.repeat(64), transformRevision: 1, status: 'ready' },
      { id: 'densified-preview-2', label: '致密化 #2', kind: 'densified', canonicalPath: 'work/geometry/variants/densified-preview-2/points3D.bin', pointCount: 618044, createdAt: '2026-07-10T08:22:00Z', sourceJobId: 'dense-job-2', protected: false, checksumSha256: 'c'.repeat(64), transformRevision: 1, status: 'ready' },
    ] : [],
  },
  jobs: [],
}

const devPreviewEnabled = devPreviewMode === 'phase3' || devPreviewMode === 'phase4' || Boolean(devPreviewMode?.startsWith('training'))

const initialState: ProjectState = devPreviewEnabled ? {
  projectRoot: 'D:/xPano-preview',
  project: devPreviewProject,
  validation: { missingSourceTrackIds: [], missingArtifactPaths: [] },
  saveState: 'saved',
  error: '',
  viewerOnlyPath: '',
  legacyName: '',
  legacyRoot: '',
  pendingDropPaths: [],
} : {
  projectRoot: '',
  project: null,
  validation: null,
  saveState: 'saved',
  error: '',
  viewerOnlyPath: '',
  legacyName: '',
  legacyRoot: '',
  pendingDropPaths: [],
}

function reducer(state: ProjectState, action: ProjectAction): ProjectState {
  switch (action.type) {
    case 'opening':
      return { ...state, saveState: 'saving', error: '' }
    case 'settled':
      return { ...state, saveState: 'saved', error: '' }
    case 'opened':
      return {
        ...state,
        projectRoot: action.result.projectRoot,
        project: action.result.project,
        validation: action.result.validation,
        saveState: 'saved',
        error: '',
        viewerOnlyPath: '',
        legacyName: '',
        legacyRoot: '',
      }
    case 'saved':
      return {
        ...state,
        project: action.project,
        validation: state.validation
          ? {
              ...state.validation,
              missingSourceTrackIds: state.validation.missingSourceTrackIds.filter((id) => action.project.tracks.some((track) => track.id === id)),
            }
          : null,
        saveState: 'saved',
        error: '',
      }
    case 'error':
      return { ...state, saveState: 'error', error: action.message }
    case 'viewer':
      return { ...state, viewerOnlyPath: action.path, project: null, projectRoot: '', validation: null, saveState: 'saved', error: '' }
    case 'legacy':
      return state.project
        ? state
        : { ...state, legacyName: action.name, legacyRoot: action.root, viewerOnlyPath: '' }
    case 'queue-drop':
      return { ...state, pendingDropPaths: action.paths }
    case 'consume-drop':
      return { ...state, pendingDropPaths: [] }
  }
}

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const hasRecoverableMedia = state.project?.tracks.some((track) => track.status === 'running' || track.status === 'failed') ?? false
  const hasRecoverableReconstruction = Boolean(
    state.project
      && !state.project.reconstruction.colmapPath
      && ['running', 'stale', 'failed', 'interrupted'].includes(state.project.reconstruction.status),
  )

  useEffect(() => {
    if (!isTauriRuntime() || !state.projectRoot || !hasRecoverableMedia) return
    let disposed = false
    invoke<XpanoProjectV2>('sync_media_job_result', { projectRoot: state.projectRoot })
      .then((project) => {
        if (!disposed) dispatch({ type: 'saved', project })
      })
      .catch((error) => {
        if (!disposed) dispatch({ type: 'error', message: commandErrorMessage(error) })
      })
    return () => { disposed = true }
  }, [hasRecoverableMedia, state.projectRoot])

  useEffect(() => {
    if (!isTauriRuntime() || !state.projectRoot || !hasRecoverableReconstruction) return
    let disposed = false
    invoke<XpanoProjectV2>('sync_reconstruction_job_result', { projectRoot: state.projectRoot })
      .then((project) => {
        if (!disposed) dispatch({ type: 'saved', project })
      })
      .catch((error) => {
        console.debug('Existing reconstruction artifacts are not recoverable for the active input revision', error)
      })
    return () => { disposed = true }
  }, [hasRecoverableReconstruction, state.projectRoot])

  useEffect(() => {
    if (!isTauriRuntime()) return
    let disposed = false
    const unlisteners: Array<() => void> = []
    const setup = async () => {
      const register = (unlisten: () => void) => {
        if (disposed) unlisten()
        else unlisteners.push(unlisten)
      }
      register(await listen<{ projectRoot: string; project: XpanoProjectV2 }>('project:updated', (event) => {
        if (disposed || normalizeDisplayPath(event.payload.projectRoot) !== normalizeDisplayPath(state.projectRoot)) return
        dispatch({ type: 'saved', project: event.payload.project })
      }))
      register(await listen<{ jobKind?: string; projectRoot?: string | null }>('pipeline:complete', async (event) => {
        if (disposed || !state.projectRoot) return
        if (event.payload.projectRoot && normalizeDisplayPath(event.payload.projectRoot) !== normalizeDisplayPath(state.projectRoot)) return
        try {
          const command = event.payload.jobKind === 'media'
            ? 'sync_media_job_result'
            : event.payload.jobKind === 'reconstruction'
              ? 'sync_reconstruction_job_result'
              : ''
          if (command) {
            const project = await invoke<XpanoProjectV2>(command, { projectRoot: state.projectRoot })
            if (!disposed) dispatch({ type: 'saved', project })
          } else {
            const result = await invoke<OpenProjectResult | null>('open_project', { path: state.projectRoot })
            if (!disposed && result) dispatch({ type: 'opened', result })
          }
        } catch (error) {
          if (!disposed) dispatch({ type: 'error', message: commandErrorMessage(error) })
        }
      }))
      register(await listen<{ jobKind?: string; projectRoot?: string | null }>('pipeline:error', async (event) => {
        if (disposed || !state.projectRoot) return
        if (event.payload.projectRoot && normalizeDisplayPath(event.payload.projectRoot) !== normalizeDisplayPath(state.projectRoot)) return
        try {
          if (event.payload.jobKind === 'media') {
            const project = await invoke<XpanoProjectV2>('sync_media_job_result', { projectRoot: state.projectRoot })
            if (!disposed) dispatch({ type: 'saved', project })
          } else {
            const result = await invoke<OpenProjectResult | null>('open_project', { path: state.projectRoot })
            if (!disposed && result) dispatch({ type: 'opened', result })
          }
        } catch (error) {
          if (!disposed) dispatch({ type: 'error', message: commandErrorMessage(error) })
        }
      }))
    }
    setup().catch((error) => {
      if (!disposed) dispatch({ type: 'error', message: commandErrorMessage(error) })
    })
    return () => {
      disposed = true
      unlisteners.forEach((unlisten) => unlisten())
    }
  }, [state.projectRoot])

  const openProject = useCallback(async (path: string) => {
    if (!isTauriRuntime()) return null
    dispatch({ type: 'opening' })
    try {
      const result = await invoke<OpenProjectResult | null>('open_project', { path })
      if (result) dispatch({ type: 'opened', result })
      else dispatch({ type: 'settled' })
      return result
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return null
    }
  }, [])

  const createProject = useCallback(async (name: string, firstSource: string, optionalRoot?: string) => {
    if (!isTauriRuntime()) return null
    dispatch({ type: 'opening' })
    try {
      const result = await invoke<OpenProjectResult>('create_project', {
        name,
        firstSource,
        optionalRoot: optionalRoot || null,
      })
      dispatch({ type: 'opened', result })
      return result
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return null
    }
  }, [])

  const commitImport = useCallback(async (drafts: MediaImportDraftInput[]) => {
    if (!drafts.length || !isTauriRuntime()) return false
    dispatch({ type: 'opening' })
    try {
      let projectRoot = state.projectRoot
      let currentProject = state.project
      let validation = state.validation
      if (!currentProject || !projectRoot) {
        const first = drafts[0]
        const created = await invoke<OpenProjectResult>('open_or_create_project', {
          name: first.label,
          firstSource: first.sourcePath,
          optionalRoot: null,
        })
        projectRoot = created.projectRoot
        currentProject = created.project
        validation = created.validation
      }
      const project = await invoke<XpanoProjectV2>('commit_import', {
        projectRoot,
        expectedRevision: currentProject.revision,
        drafts,
      })
      dispatch({
        type: 'opened',
        result: {
          projectRoot,
          project,
          validation: validation ?? { missingSourceTrackIds: [], missingArtifactPaths: [] },
        },
      })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      throw error
    }
  }, [state.project, state.projectRoot, state.validation])

  const updateTrackSettings = useCallback(async (trackId: string, patch: TrackSettingsPatch) => {
    if (!state.project || !state.projectRoot || !isTauriRuntime()) return false
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('update_track_settings', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        trackId,
        patch,
      })
      dispatch({ type: 'saved', project })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return false
    }
  }, [state.project, state.projectRoot])

  const removeTrack = useCallback(async (trackId: string) => {
    if (!state.project || !state.projectRoot || !isTauriRuntime()) return false
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('remove_project_track', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        trackId,
      })
      dispatch({ type: 'saved', project })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return false
    }
  }, [state.project, state.projectRoot])

  const setItemSelection = useCallback(async (trackId: string, itemIds: string[], selected: boolean) => {
    if (!state.project || !state.projectRoot || !isTauriRuntime()) return false
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('set_track_item_selection', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        trackId,
        itemIds,
        selected,
      })
      dispatch({ type: 'saved', project })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return false
    }
  }, [state.project, state.projectRoot])

  const listTrackItems = useCallback(async (trackId: string, cursor: number, limit: number, filter: MediaItemFilter) => {
    if (!state.projectRoot || !isTauriRuntime()) return null
    try {
      return await invoke<MediaItemPage>('list_track_items', {
        projectRoot: state.projectRoot,
        trackId,
        cursor,
        limit,
        filter,
      })
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return null
    }
  }, [state.projectRoot])

  const renameProject = useCallback(async (name: string) => {
    if (!state.project || !state.projectRoot || !isTauriRuntime()) return false
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('rename_project', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        name,
      })
      dispatch({ type: 'saved', project })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return false
    }
  }, [state.project, state.projectRoot])

  const setWorkspace = useCallback(async (workspace: ProjectWorkspace) => {
    if (!state.project || !state.projectRoot) return true
    if (state.project.activeWorkspace === workspace) return true
    if (!isTauriRuntime()) {
      dispatch({ type: 'saved', project: { ...state.project, activeWorkspace: workspace } })
      return true
    }
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('set_project_workspace', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        workspace,
      })
      dispatch({ type: 'saved', project })
      return true
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return false
    }
  }, [state.project, state.projectRoot])

  const saveReconstructionConfig = useCallback(async (backend: ReconstructionBackend, config: Record<string, unknown>) => {
    if (!state.project || !state.projectRoot || !isTauriRuntime()) return null
    dispatch({ type: 'opening' })
    try {
      const project = await invoke<XpanoProjectV2>('update_reconstruction_config', {
        projectRoot: state.projectRoot,
        expectedRevision: state.project.revision,
        backend,
        config,
      })
      dispatch({ type: 'saved', project })
      return project
    } catch (error) {
      dispatch({ type: 'error', message: commandErrorMessage(error) })
      return null
    }
  }, [state.project, state.projectRoot])

  const setViewerOnlyPath = useCallback((path: string) => dispatch({ type: 'viewer', path }), [])
  const registerLegacySession = useCallback((root: string, name: string) => dispatch({ type: 'legacy', root, name }), [])
  const queueDropPaths = useCallback((paths: string[]) => dispatch({ type: 'queue-drop', paths }), [])
  const consumeDropPaths = useCallback(() => dispatch({ type: 'consume-drop' }), [])

  const value = useMemo<ProjectContextValue>(() => ({
    ...state,
    displayName: state.project?.name || state.legacyName || (state.viewerOnlyPath ? 'COLMAP 预览' : '未打开工程'),
    displayPath: state.projectRoot || state.legacyRoot || state.viewerOnlyPath,
    openProject,
    createProject,
    commitImport,
    updateTrackSettings,
    removeTrack,
    setItemSelection,
    listTrackItems,
    renameProject,
    setWorkspace,
    saveReconstructionConfig,
    setViewerOnlyPath,
    registerLegacySession,
    queueDropPaths,
    consumeDropPaths,
  }), [commitImport, consumeDropPaths, createProject, listTrackItems, openProject, queueDropPaths, registerLegacySession, removeTrack, renameProject, saveReconstructionConfig, setItemSelection, setViewerOnlyPath, setWorkspace, state, updateTrackSettings])

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
}
