import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import {
  Camera,
  ChevronDown,
  Crosshair,
  ChevronUp,
  CheckCircle2,
  Cpu,
  Eye,
  FolderOpen,
  Gauge,
  Image,
  PanelRight,
  Plane,
  Play,
  Plus,
  Scissors,
  Square,
  Terminal,
  Trash2,
  Video,
} from 'lucide-react'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'
import gsap from 'gsap'
import { ThemeControls } from '../layout/ThemeControls'
import { WindowControls } from '../layout/WindowControls'
import { ToastContainer } from '../shared/Toast'
import { ConfirmDialog } from '../shared/ConfirmDialog'
import { VideoTrimmer } from './VideoTrimmer'
import { useToast } from '../../hooks/useToast'
import { useJob } from '../../app/useJob'
import { useProject } from '../../app/useProject'
import { useMorphSwap } from '../../hooks/useGsap'
import { joinDisplayPath, normalizeDisplayPath } from '../../lib/paths'
import { framesPerSecondForLimit } from '../../lib/extractionRate'
import type {
  AlignmentEngine,
  CameraProfile,
  ColmapDensityPreset,
  ColmapMatcher,
  MaterialTrack,
  PipelineConfig,
  PipelinePhase,
  PipelineProgress,
  ProjectRunOptions,
  ThemeMode,
  TrackType,
} from '../../lib/types'

interface PipelinePageProps {
  embedded?: boolean
  themeMode: ThemeMode
  onThemeModeChange: (mode: ThemeMode) => void
}

const trackMeta: Record<TrackType, { icon: ReactNode; label: string; hint: string }> = {
  panoramic_video: { icon: <Video size={18} strokeWidth={1.8} />, label: '全景视频', hint: '.osv / .insv' },
  ordinary_video: { icon: <Video size={18} strokeWidth={1.8} />, label: '普通视频', hint: '.mp4 / .mov / .avi' },
  standard_photos: { icon: <Image size={18} strokeWidth={1.8} />, label: '标准照片', hint: '照片文件夹' },
  aerial_photos: { icon: <Plane size={18} strokeWidth={1.8} />, label: '航拍照片', hint: '无人机航线' },
}

const stageLabels: Record<'extract' | 'align' | 'export', string> = {
  extract: '抽帧',
  align: '对齐',
  export: '导出',
}

const phaseLabels: Record<PipelinePhase, string> = {
  idle: '等待开始',
  extract: '抽帧中',
  align: '对齐中',
  export: '导出中',
  train: '训练中',
  complete: '处理完成',
  error: '处理出错',
}

const defaultConfig: PipelineConfig = {
  outputDir: '',
  metashapePath: '',
  colmapPath: '',
  framesPerSecond: 1.0,
  frameLimit: 0,
  alignmentEngine: 'metashape',
  metaAlignmentMode: 'backbone',
  metaKeypointLimit: 40000,
  metaTiepointLimit: 0,
  upAxis: '+Y',
  colmapDensityPreset: 'stable',
  colmapUseGpu: false,
  colmapMatcher: 'sequential',
  colmapMaxImageSize: 1600,
  colmapMaxNumFeatures: 4096,
}

const pipelineSessionKey = 'xpano-pipeline-session'

interface LoadedProjectTrack {
  id: string
  type: TrackType
  label: string
  path: string
  cameraProfile?: CameraProfile | null
  frameCount: number
  photoCount: number
}

interface LoadedProjectState {
  projectDir: string
  manifestPath: string
  metashapeProjectPath: string
  backend: string
  metashapeAlignmentMode: string
  framesPerSecond: number
  maxFrames: number
  tracks: LoadedProjectTrack[]
}

interface PipelineSessionState {
  tracks?: MaterialTrack[]
  config?: Partial<PipelineConfig>
  selectedTrackId?: string | null
  showRightPanel?: boolean
  loadedProject?: LoadedProjectState | null
  projectSteps?: ProjectStepSelection
}

interface ProjectStepSelection {
  extract: boolean
  align: boolean
  export: boolean
}

function readPipelineSession(): PipelineSessionState | null {
  if (typeof window === 'undefined') return null
  try {
    const saved = window.sessionStorage.getItem(pipelineSessionKey)
    if (!saved) return null
    const parsed = JSON.parse(saved) as PipelineSessionState
    return {
      tracks: Array.isArray(parsed.tracks) ? parsed.tracks : [],
      config: parsed.config && typeof parsed.config === 'object' ? parsed.config : {},
      selectedTrackId: typeof parsed.selectedTrackId === 'string' ? parsed.selectedTrackId : null,
      showRightPanel: Boolean(parsed.showRightPanel),
      loadedProject: parsed.loadedProject && typeof parsed.loadedProject === 'object' ? parsed.loadedProject : null,
      projectSteps: parsed.projectSteps && typeof parsed.projectSteps === 'object'
        ? {
            extract: Boolean(parsed.projectSteps.extract),
            align: Boolean(parsed.projectSteps.align),
            export: Boolean(parsed.projectSteps.export),
          }
        : undefined,
    }
  } catch {
    return null
  }
}

const defaultExtractConfig = { framesPerSecond: 1.0, frameLimit: 0 }
const defaultProjectSteps: ProjectStepSelection = { extract: true, align: true, export: true }
const loadedProjectDefaultSteps: ProjectStepSelection = { extract: false, align: true, export: true }
const cameraProfileOptions: Array<{ value: CameraProfile; label: string }> = [
  { value: 'wide', label: '广角视角' },
  { value: 'standard', label: '标准视角' },
]

interface ImportPathInfo {
  path: string
  label?: string
  name?: string
  suggestedType?: TrackType | 'unsupported'
  kind?: TrackType | 'unsupported'
  validPhotoFolder?: boolean
  valid?: boolean
  message: string
  photoCount: number
}

interface ImportDraft {
  id: string
  info: ImportPathInfo
  type: TrackType
  label: string
  trim?: { start: number; end: number }
  extract: { framesPerSecond: number; frameLimit: number }
  cameraProfile?: CameraProfile
  duration?: number
}

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

function isVideoTrack(type: TrackType) {
  return type === 'panoramic_video' || type === 'ordinary_video'
}

function defaultCameraProfile(type: TrackType): CameraProfile | undefined {
  return type === 'ordinary_video' ? 'wide' : undefined
}

function cameraProfileLabel(profile?: CameraProfile) {
  return cameraProfileOptions.find((item) => item.value === (profile ?? 'wide'))?.label ?? '广角视角'
}

function normalizeLoadedProjectTracks(project: LoadedProjectState): MaterialTrack[] {
  return project.tracks.map((track, index) => ({
    id: track.id || `loaded_track_${index + 1}`,
    type: track.type,
    label: track.label || `Track ${index + 1}`,
    path: track.path,
    ...(isVideoTrack(track.type) ? {
      extract: {
        framesPerSecond: project.framesPerSecond || defaultExtractConfig.framesPerSecond,
        frameLimit: project.maxFrames > 0 ? project.maxFrames : 0,
      },
    } : {}),
    ...(track.type === 'ordinary_video' ? {
      cameraProfile: track.cameraProfile === 'standard' ? 'standard' : 'wide',
    } : {}),
    restoredFrameCount: track.frameCount,
    restoredPhotoCount: track.photoCount,
  }))
}

function baseName(path: string) {
  return path.split(/[/\\]/).pop() || path
}

function labelFromPath(path: string, fallback: string) {
  return baseName(path).replace(/\.[^.]+$/, '') || fallback
}

function importKind(info: ImportPathInfo): TrackType | 'unsupported' {
  const kind = info.kind ?? info.suggestedType
  return kind && kind in trackMeta ? kind as TrackType : 'unsupported'
}

function isImportValid(info: ImportPathInfo) {
  const kind = importKind(info)
  if (kind === 'unsupported') return false
  if (kind === 'standard_photos' || kind === 'aerial_photos') return info.valid ?? info.validPhotoFolder ?? info.photoCount > 0
  return info.valid ?? true
}

export function PipelinePage({
  embedded = false,
  themeMode,
  onThemeModeChange,
}: PipelinePageProps) {
  const navigate = useNavigate()
  const {
    project: v2Project,
    projectRoot: v2ProjectRoot,
    pendingDropPaths,
    consumeDropPaths,
    setViewerOnlyPath,
    registerLegacySession,
  } = useProject()
  const initialSessionRef = useRef<PipelineSessionState | null>(readPipelineSession())
  const [tracks, setTracks] = useState<MaterialTrack[]>(() => initialSessionRef.current?.tracks ?? [])
  const [config, setConfig] = useState<PipelineConfig>(() => ({
    ...defaultConfig,
    ...(initialSessionRef.current?.config ?? {}),
  }))
  const [showRightPanel, setShowRightPanel] = useState(() => initialSessionRef.current?.showRightPanel ?? false)
  const [confirmRemove, setConfirmRemove] = useState<MaterialTrack | null>(null)
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(() => initialSessionRef.current?.selectedTrackId ?? null)
  const [loadedProject, setLoadedProject] = useState<LoadedProjectState | null>(() => initialSessionRef.current?.loadedProject ?? null)
  const [projectSteps, setProjectSteps] = useState<ProjectStepSelection>(() => initialSessionRef.current?.projectSteps ?? defaultProjectSteps)
  const [importDrafts, setImportDrafts] = useState<ImportDraft[]>([])
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const { progress, running, logs, start: startPipeline, cancel, reset } = useJob()
  const { toasts, removeToast, toast } = useToast()
  const shellRef = useRef<HTMLDivElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const lastOutcomeToastRef = useRef<PipelinePhase | null>(null)

  // Surface pipeline completion / failure as a toast so the outcome is never silent.
  useEffect(() => {
    if (progress.phase !== 'complete' && progress.phase !== 'error') {
      lastOutcomeToastRef.current = null
      return
    }
    if (lastOutcomeToastRef.current === progress.phase) return
    lastOutcomeToastRef.current = progress.phase

    if (progress.phase === 'complete') toast.success('高斯对齐完成，已导出 Colmap 数据')
    else if (progress.message.includes('取消')) toast.warning('任务已取消，可重新开始')
    else toast.error(progress.message || '高斯对齐过程中出错')
  }, [progress.phase, progress.message, toast])

  // Keep the terminal pinned to the latest line while logs stream in,
  // but only when the user hasn't scrolled up to read past entries.
  const userScrolledUp = useRef(false)
  useEffect(() => {
    const el = logRef.current
    if (!el) return
    const onScroll = () => {
      userScrolledUp.current = el.scrollHeight - el.scrollTop - el.clientHeight >= 40
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useLayoutEffect(() => {
    const el = logRef.current
    if (!el) return
    if (!userScrolledUp.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [logs])

  useEffect(() => {
    let savedConfig: Partial<PipelineConfig> = {}
    const hasLiveSessionConfig = Boolean(initialSessionRef.current?.config)
    try {
      const saved = localStorage.getItem('xpano-config')
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<PipelineConfig>
        const { outputDir: _discardedOutputDir, ...restoredConfig } = parsed
        savedConfig = restoredConfig
        if (!hasLiveSessionConfig) {
          setConfig((prev) => ({ ...prev, ...savedConfig }))
        }
      }
    } catch {
      // Defaults keep the app usable when local state is malformed.
    }
    if (!isTauriRuntime()) return
    if (!savedConfig.metashapePath) {
      invoke<string>('detect_metashape')
        .then((path) => {
          if (path) setConfig((prev) => prev.metashapePath ? prev : { ...prev, metashapePath: path })
        })
        .catch(() => {})
    }
    if (!savedConfig.colmapPath) {
      invoke<string>('detect_colmap')
        .then((path) => {
          if (path) setConfig((prev) => prev.colmapPath ? prev : { ...prev, colmapPath: path })
        })
        .catch(() => {})
    }
  }, [])

  useEffect(() => {
    const nodes = shellRef.current?.querySelectorAll('[data-enter]')
    if (!nodes?.length) return
    gsap.fromTo(
      nodes,
      { autoAlpha: 0, y: 14 },
      { autoAlpha: 1, y: 0, duration: 0.56, ease: 'power3.out', stagger: 0.035 }
    )
  }, [])

  useEffect(() => {
    const root = shellRef.current
    if (!root) return

    let frame = 0
    let mouseX = -9999
    let mouseY = -9999
    const radius = 118

    const update = () => {
      frame = 0
      const icons = root.querySelectorAll<HTMLElement>('.icon-tile, .icon-tile-lg')
      icons.forEach((icon) => {
        const rect = icon.getBoundingClientRect()
        const cx = rect.left + rect.width / 2
        const cy = rect.top + rect.height / 2
        const dx = mouseX - cx
        const dy = mouseY - cy
        const distance = Math.sqrt(dx * dx + dy * dy)
        const proximity = Math.max(0, 1 - distance / radius)
        // Only brightness/saturation lift — no translate, so click targets stay put.
        icon.style.setProperty('--icon-proximity', proximity.toFixed(3))
      })
    }

    const requestUpdate = () => {
      if (!frame) frame = requestAnimationFrame(update)
    }

    const onMove = (event: MouseEvent) => {
      mouseX = event.clientX
      mouseY = event.clientY
      requestUpdate()
    }

    const onLeave = () => {
      mouseX = -9999
      mouseY = -9999
      requestUpdate()
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseout', onLeave)
    requestUpdate()

    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseout', onLeave)
    }
  }, [])

  useEffect(() => {
    if (!config.outputDir && !config.metashapePath && !config.colmapPath) return
    try {
      const { outputDir: _discardedOutputDir, ...persistedConfig } = config
      localStorage.setItem('xpano-config', JSON.stringify(persistedConfig))
    } catch {
      // Local persistence is a convenience only.
    }
  }, [config])

  useEffect(() => {
    try {
      if (!tracks.length && !config.outputDir) {
        window.sessionStorage.removeItem(pipelineSessionKey)
        return
      }
      window.sessionStorage.setItem(pipelineSessionKey, JSON.stringify({
        tracks,
        config,
        selectedTrackId,
        showRightPanel,
        loadedProject,
        projectSteps,
      } satisfies PipelineSessionState))
    } catch {
      // Session persistence only protects page-to-page navigation.
    }
  }, [tracks, config, selectedTrackId, showRightPanel, loadedProject, projectSteps])

  useEffect(() => {
    if (embedded || !isTauriRuntime()) return
    let disposed = false
    let unlisten: (() => void) | undefined
    getCurrentWindow().onDragDropEvent((event) => {
      if (disposed) return
      if (event.payload.type === 'over') {
        setDragOver(true)
      } else if (event.payload.type === 'leave') {
        setDragOver(false)
      } else if (event.payload.type === 'drop') {
        setDragOver(false)
        openImportSession(event.payload.paths)
      }
    }).then((fn) => {
      if (disposed) fn()
      else unlisten = fn
    }).catch((error) => {
      toast.warning(`拖拽导入初始化失败：${error}`)
    })
    return () => {
      disposed = true
      unlisten?.()
    }
    // openImportSession intentionally reads current UI state when a drop happens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded, toast])

  const createImportDrafts = useCallback((infos: ImportPathInfo[]): ImportDraft[] => infos.map((info) => {
    const detectedType = importKind(info)
    const initialType = detectedType === 'unsupported' ? 'standard_photos' : detectedType
    return {
      id: crypto.randomUUID(),
      info,
      type: initialType,
      label: info.label || info.name || labelFromPath(info.path, trackMeta[initialType].label),
      extract: defaultExtractConfig,
      cameraProfile: defaultCameraProfile(initialType),
    }
  }), [])

  const openImportSession = useCallback(async (paths: string[]) => {
    const cleanPaths = Array.from(new Set(paths.filter(Boolean).map(normalizeDisplayPath)))
    if (!cleanPaths.length) return
    try {
      const projectDir = await invoke<string | null>('resolve_xpano_project_dir', { paths: cleanPaths })
      if (projectDir) {
        const project = await invoke<LoadedProjectState>('load_xpano_project', { path: projectDir })
        const loadedTracks = normalizeLoadedProjectTracks(project)
        registerLegacySession(project.projectDir, loadedTracks[0]?.label || 'xPano 工程')
        setLoadedProject(project)
        setProjectSteps(loadedProjectDefaultSteps)
        setTracks(loadedTracks)
        setSelectedTrackId(loadedTracks[0]?.id ?? null)
        setConfig((prev) => ({
          ...prev,
          outputDir: normalizeDisplayPath(project.projectDir),
          alignmentEngine: project.backend === 'colmap' ? 'colmap' : 'metashape',
          metaAlignmentMode: 'backbone',
          framesPerSecond: project.framesPerSecond || prev.framesPerSecond,
          frameLimit: project.maxFrames > 0 ? project.maxFrames : 0,
        }))
        setImportDialogOpen(false)
        setImportDrafts([])
        toast.success(`已载入 xPano 工程：${loadedTracks.length} 条素材轨，可复用抽帧与工程文件`)
        return
      }
      const previewDir = await invoke<string | null>('resolve_colmap_preview_dir', { paths: cleanPaths })
      if (previewDir) {
        setImportDialogOpen(false)
        setImportDrafts([])
        setViewerOnlyPath(normalizeDisplayPath(previewDir))
        navigate('/project/results')
        toast.success('已识别 COLMAP 点云，进入预览模式')
        return
      }
      const infos = await invoke<ImportPathInfo[]>('analyze_import_paths', { paths: cleanPaths })
      const drafts = createImportDrafts(infos)
      if (!drafts.length) return
      setImportDrafts(drafts)
      setImportDialogOpen(true)
    } catch (error) {
      toast.error(`素材分析失败：${error}`)
    }
  }, [createImportDrafts, navigate, registerLegacySession, setViewerOnlyPath, toast])

  useEffect(() => {
    if (!embedded || !pendingDropPaths.length) return
    const paths = pendingDropPaths
    consumeDropPaths()
    void openImportSession(paths)
  }, [consumeDropPaths, embedded, openImportSession, pendingDropPaths])

  const loadedV2KeyRef = useRef('')
  useEffect(() => {
    if (!v2Project || !v2ProjectRoot) return
    const loadKey = `${v2Project.projectId}:${v2Project.revisions.media}:${v2Project.revisions.alignmentInput}`
    if (loadedV2KeyRef.current === loadKey) return
    loadedV2KeyRef.current = loadKey
    const loadedTracks: MaterialTrack[] = v2Project.tracks.map((track) => ({
      id: track.id,
      type: track.type,
      label: track.label,
      path: normalizeDisplayPath(track.sourcePath),
      trim: track.trim ?? undefined,
      extract: track.extraction,
      cameraProfile: track.cameraProfile ?? undefined,
      restoredFrameCount: track.type === 'panoramic_video' ? track.items.length : undefined,
      restoredPhotoCount: track.type === 'standard_photos' || track.type === 'aerial_photos' ? track.items.length : undefined,
    }))
    const firstVideo = v2Project.tracks.find((track) => isVideoTrack(track.type))
    const reconstructionConfig = v2Project.reconstruction.config
    const alignmentMode = 'backbone'
    const alignmentManifestPath = typeof reconstructionConfig.alignmentManifestPath === 'string'
      ? reconstructionConfig.alignmentManifestPath
      : ''
    setTracks(loadedTracks)
    setSelectedTrackId(loadedTracks[0]?.id ?? null)
    setLoadedProject({
      projectDir: normalizeDisplayPath(v2ProjectRoot),
      manifestPath: alignmentManifestPath ? joinDisplayPath(v2ProjectRoot, alignmentManifestPath) : '',
      metashapeProjectPath: v2Project.reconstruction.projectPath
        ? joinDisplayPath(v2ProjectRoot, v2Project.reconstruction.projectPath)
        : '',
      backend: v2Project.reconstruction.backend,
      metashapeAlignmentMode: alignmentMode,
      framesPerSecond: firstVideo?.extraction.framesPerSecond ?? 1,
      maxFrames: firstVideo?.extraction.frameLimit ?? 0,
      tracks: loadedTracks.map((track) => ({
        id: track.id,
        type: track.type,
        label: track.label,
        path: track.path,
        cameraProfile: track.cameraProfile,
        frameCount: track.restoredFrameCount ?? 0,
        photoCount: track.restoredPhotoCount ?? 0,
      })),
    })
    setProjectSteps(loadedProjectDefaultSteps)
    setConfig((prev) => ({
      ...prev,
      outputDir: normalizeDisplayPath(v2ProjectRoot),
      alignmentEngine: v2Project.reconstruction.backend,
      metaAlignmentMode: alignmentMode,
      framesPerSecond: firstVideo?.extraction.framesPerSecond ?? prev.framesPerSecond,
      frameLimit: firstVideo?.extraction.frameLimit ?? prev.frameLimit,
    }))
    toast.success(`已恢复 xPano v2 工程：${loadedTracks.length} 条素材轨`)
  }, [toast, v2Project, v2ProjectRoot])

  const addTrack = async () => {
    const selected = await openDialog({ multiple: true })
    if (!selected) return
    await openImportSession(Array.isArray(selected) ? selected : [selected])
  }

  const confirmImport = async (drafts: ImportDraft[]) => {
    const accepted = drafts.filter((draft) => isImportValid(draft.info))
    if (!accepted.length) {
      toast.warning('没有可导入的素材')
      return
    }

    const nextTracks: MaterialTrack[] = accepted.map((draft) => ({
      id: crypto.randomUUID(),
      type: draft.type,
      label: draft.label || labelFromPath(draft.info.path, trackMeta[draft.type].label),
      path: draft.info.path,
      ...(isVideoTrack(draft.type) ? { trim: draft.trim, extract: draft.extract } : {}),
      ...(draft.type === 'ordinary_video' ? { cameraProfile: draft.cameraProfile ?? 'wide' } : {}),
    }))

    setLoadedProject(null)
    setProjectSteps(defaultProjectSteps)
    setTracks((prev) => [...prev, ...nextTracks])
    setSelectedTrackId(nextTracks.find((track) => isVideoTrack(track.type))?.id ?? null)
    setImportDialogOpen(false)
    setImportDrafts([])
    toast.success(`已导入 ${nextTracks.length} 组素材`)

    if (tracks.length === 0 && !config.outputDir && nextTracks[0]) {
      try {
        const outputDir = await invoke<string>('ensure_default_output_dir', { path: nextTracks[0].path })
        setConfig((prev) => prev.outputDir ? prev : { ...prev, outputDir: normalizeDisplayPath(outputDir) })
      } catch (error) {
        toast.warning(`自动创建输出目录失败：${error}`)
      }
    }
  }

  const removeTrack = (id: string) => {
    const target = tracks.find((track) => track.id === id)
    if (!target) return
    // Gate single-track removal behind a confirm dialog so a stray click can't drop a configured track.
    setConfirmRemove(target)
  }

  const executeRemove = () => {
    const target = confirmRemove
    setConfirmRemove(null)
    if (!target) return
    setLoadedProject(null)
    setProjectSteps(defaultProjectSteps)
    setTracks((prev) => prev.filter((track) => track.id !== target.id))
    toast.info(`已移除：${target.label}`)
  }

  const handleStart = async () => {
    const projectLoadedForOutput = Boolean(
      loadedProject &&
      normalizeDisplayPath(loadedProject.projectDir) === normalizeDisplayPath(config.outputDir) &&
      tracks.length > 0,
    )
    const runOptions: ProjectRunOptions = projectLoadedForOutput
        ? {
          skipExtract: !projectSteps.extract,
          reexportOnly: !projectSteps.align && projectSteps.export,
          manifestPath: !projectSteps.extract ? loadedProject?.manifestPath || undefined : undefined,
        }
      : {}
    startPipeline(tracks, config, runOptions)
    toast.info(`启动高斯对齐（${tracks.length} 组素材），将自动检查可复用抽帧`)
  }

  const handleCancel = () => {
    cancel()
  }

  const selectedTrack = tracks.find((track) => track.id === selectedTrackId) ?? null
  const selectedExtract = {
    framesPerSecond: selectedTrack?.extract?.framesPerSecond ?? defaultExtractConfig.framesPerSecond,
    frameLimit: selectedTrack?.extract?.frameLimit ?? defaultExtractConfig.frameLimit,
  }

  const updateTrim = (id: string, trim: { start: number; end: number }) => {
    setLoadedProject(null)
    setProjectSteps(defaultProjectSteps)
    setTracks((prev) => prev.map((track) => (track.id === id ? { ...track, trim } : track)))
  }

  const updateExtract = (id: string, extract: { framesPerSecond: number; frameLimit: number }) => {
    setLoadedProject(null)
    setProjectSteps(defaultProjectSteps)
    setTracks((prev) => prev.map((track) => (track.id === id ? { ...track, extract } : track)))
  }

  const updateCameraProfile = (id: string, cameraProfile: CameraProfile) => {
    setLoadedProject(null)
    setProjectSteps(defaultProjectSteps)
    setTracks((prev) => prev.map((track) => (track.id === id ? { ...track, cameraProfile } : track)))
  }

  const browseOutput = async () => {
    const selected = await openDialog({ directory: true })
    if (selected) {
      setLoadedProject(null)
      setProjectSteps(defaultProjectSteps)
      setConfig((prev) => ({ ...prev, outputDir: normalizeDisplayPath(selected) }))
    }
  }

  const openOutput = async () => {
    if (config.outputDir) await invoke('open_output_folder', { path: config.outputDir })
  }

  const browseEngine = async () => {
    const name = config.alignmentEngine === 'metashape' ? 'Metashape' : 'Colmap'
    const selected = await openDialog({ filters: [{ name, extensions: ['exe'] }] })
    if (!selected) return
    setConfig((prev) => (
      prev.alignmentEngine === 'metashape'
        ? { ...prev, metashapePath: normalizeDisplayPath(selected) }
        : { ...prev, colmapPath: normalizeDisplayPath(selected) }
    ))
  }

  const projectLoadedForOutput = Boolean(
    loadedProject &&
    normalizeDisplayPath(loadedProject.projectDir) === normalizeDisplayPath(config.outputDir),
  )
  const projectReexportNeedsPsx = Boolean(projectLoadedForOutput && !projectSteps.align && projectSteps.export)
  const projectReexportEngineReady = !projectReexportNeedsPsx || config.alignmentEngine === 'metashape'
  const projectManifestReady = !projectLoadedForOutput || projectSteps.extract || Boolean(loadedProject?.manifestPath)
  const projectStepReady = !projectLoadedForOutput ||
    ((projectSteps.extract || projectSteps.align || projectSteps.export) &&
      projectReexportEngineReady &&
      projectManifestReady &&
      (!projectReexportNeedsPsx || Boolean(loadedProject?.metashapeProjectPath)))
  const noEngine = config.alignmentEngine === 'metashape' ? !config.metashapePath : !config.colmapPath
  const canStart = tracks.length > 0 && Boolean(config.outputDir) && !noEngine && projectStepReady
  const idle = !running && progress.phase !== 'complete' && progress.phase !== 'error'
  const blockReason = !config.outputDir
    ? '请选择输出目录'
    : !tracks.length
      ? '请先添加至少一组素材'
    : noEngine
      ? `请选择 ${config.alignmentEngine === 'metashape' ? 'Metashape.exe' : 'Colmap.exe'}`
      : !projectStepReady
        ? projectReexportNeedsPsx && !projectReexportEngineReady
          ? '只重导出需使用 Metashape 后端'
          : projectReexportNeedsPsx
            ? '未找到可重导出的 Metashape 工程'
            : '请选择至少一个工程步骤'
        : '准备就绪'

  const trackCounts = {
    panoramic_video: tracks.filter((track) => track.type === 'panoramic_video').length,
    ordinary_video: tracks.filter((track) => track.type === 'ordinary_video').length,
    standard_photos: tracks.filter((track) => track.type === 'standard_photos').length,
    aerial_photos: tracks.filter((track) => track.type === 'aerial_photos').length,
  }

  const inputClass = 'theme-input w-full rounded-comfortable border px-3 py-2 text-[13px] outline-none transition-all'

  const engineReady = !noEngine
  const activeEngineName = config.alignmentEngine === 'metashape' ? 'Metashape' : 'Colmap'
  const activeEnginePath = config.alignmentEngine === 'metashape' ? config.metashapePath : config.colmapPath
  const activeEngineFile = activeEnginePath?.split(/[/\\]/).pop()
  const updateProjectStep = (step: keyof ProjectStepSelection) => {
    setProjectSteps((prev) => {
      if (step === 'extract') {
        return prev.extract ? { ...prev, extract: false } : { extract: true, align: true, export: true }
      }
      if (step === 'align') {
        return prev.align ? { extract: false, align: false, export: true } : { ...prev, align: true, export: true }
      }
      return prev.export ? { extract: false, align: false, export: false } : { ...prev, export: true }
    })
  }

  useEffect(() => {
    if (v2Project || !tracks.length || !config.outputDir) return
    registerLegacySession(config.outputDir, tracks[0].label)
  }, [config.outputDir, registerLegacySession, tracks, v2Project])

  return (
    <div className={`liquid-shell relative z-10 overflow-hidden bg-transparent text-ink ${embedded ? 'h-full' : 'h-screen'}`}>
      <ConfirmDialog
        open={confirmRemove !== null}
        danger
        title="移除素材轨道？"
        message={`将从列表移除「${confirmRemove?.label ?? ''}」，不影响磁盘文件。`}
        confirmText="移除"
        onConfirm={executeRemove}
        onCancel={() => setConfirmRemove(null)}
      />
      <MaterialImportDialog
        open={importDialogOpen}
        drafts={importDrafts}
        onDraftsChange={setImportDrafts}
        onCancel={() => {
          setImportDialogOpen(false)
          setImportDrafts([])
        }}
        onConfirm={confirmImport}
      />

      {/* Top bar */}
      {!embedded && <div className="liquid-topbar fixed left-2 right-2 top-2 z-50 flex h-10 items-center justify-between rounded-[14px] border-0 py-0 pl-3.5 pr-0 drag-region">
        <div className="flex items-center gap-2.5">
          <img src="/icon.png" alt="xPano" className="h-6 w-6 rounded-subtle" />
          <div className="leading-none">
            <span className="text-[13px] font-medium text-ink">xPano</span>
          </div>
        </div>
        <div className="topbar-control-group no-drag flex items-center gap-1">
          <ThemeControls
            themeMode={themeMode}
            onThemeModeChange={onThemeModeChange}
          />
          <button
            onClick={() => setShowRightPanel((v) => !v)}
            className="motion-press grid h-7 w-7 place-items-center rounded-subtle text-ink/45 hover:bg-white/[0.08] hover:text-ink/72 transition-colors xl:hidden"
            title={showRightPanel ? '隐藏状态面板' : '显示状态面板'}
          >
            <PanelRight className="h-4 w-4" />
          </button>
          <span className="topbar-control-divider" />
          <WindowControls />
        </div>
      </div>}

      <div ref={shellRef} className={`grid box-border grid-cols-[220px_minmax(0,1fr)] gap-2 xl:grid-cols-[252px_minmax(0,1fr)_320px] ${embedded ? 'h-full' : 'h-screen px-2 pb-2 pt-14'}`}>
        {/* Left — material library (知天下导航风格) */}
        <aside className="liquid-panel flex min-h-0 flex-col overflow-x-hidden p-4">
          <div data-enter className="theme-segment relative flex p-1 overflow-hidden" style={{ boxShadow: 'inset 0 2px 4px var(--xp-segment-inset), inset 0 1px 2px var(--xp-segment-inset-soft)' }}>
            <span
              className="absolute top-0.5 bottom-0.5 w-[calc(50%-6px)] rounded-subtle"
              style={{
                left: config.alignmentEngine === 'colmap' ? 'calc(50% + 3px)' : '3px',
                background: 'linear-gradient(180deg, var(--xp-brand-soft), var(--xp-brand))',
                border: '1px solid rgba(255,255,255,0.25)',
                boxShadow: `
                  0 8px 20px var(--xp-segment-glow),
                  0 2px 6px var(--xp-segment-glow-soft),
                  inset 0 1px 0 rgba(255,255,255,0.25)
                `,
                transition: 'left 320ms cubic-bezier(0.22, 1, 0.36, 1)',
              }}
            />
            {(['metashape', 'colmap'] as const).map((engine) => (
              <button key={engine} onClick={() => setConfig((prev) => ({ ...prev, alignmentEngine: engine as AlignmentEngine }))}
                className={`motion-press relative z-10 flex-1 rounded-subtle px-3 py-1.5 text-[12px] font-medium transition-colors duration-200 ${
                  config.alignmentEngine === engine ? 'text-white' : 'text-muted hover:text-ink'
                }`}>{engine === 'metashape' ? 'Metashape' : 'Colmap'}</button>
            ))}
          </div>

          <button onClick={browseEngine} data-enter
            className="glass-control mt-2.5 flex w-full items-center gap-2.5 rounded-card px-3 py-2.5 text-left transition-all hover:-translate-y-0.5 hover:text-brand">
            <span className="icon-tile grid h-8 w-8 shrink-0 place-items-center rounded-comfortable">
              <Cpu className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1 leading-tight">
              <span className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-medium text-ink">{activeEngineName} 路径</span>
                <span className={`h-1.5 w-1.5 rounded-full ${activeEnginePath ? 'bg-brand' : 'bg-ink/20'}`} />
              </span>
              <span className="mt-1 block truncate text-[11px] text-muted">
                {activeEngineFile || '选择对齐引擎可执行文件'}
              </span>
            </span>
          </button>

          {/* Add-track shortcuts — empty state surfaces bigger cards in the track list below. */}

          {/* Tracks list */}
          <section className="relative mt-4 flex min-h-0 flex-1 flex-col">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="ui-label text-[11px]">素材轨道</h3>
              {tracks.length > 0 && (
                <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted">
                  <span className={`beacon h-1.5 w-1.5 ${idle ? 'beacon-idle' : ''}`} />
                  {tracks.length}
                </span>
              )}
            </div>

            {!tracks.length ? (
              <button
                data-enter
                onClick={addTrack}
                className={`liquid-card liquid-card-clear group flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center transition-all hover:-translate-y-0.5 hover:border-brand/30 ${
                  dragOver ? 'border-brand/45 bg-brand/[0.08]' : ''
                }`}
              >
                <span className="icon-tile-lg grid h-12 w-12 place-items-center rounded-card transition-transform group-hover:scale-105">
                  <Plus className="h-5 w-5" />
                </span>
                <span className="text-[13px] font-medium text-ink">暂无素材，请点击添加或拖动到此处</span>
              </button>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className={`glass-inset flex-1 overflow-y-auto overflow-x-hidden rounded-card ${dragOver ? 'ring-1 ring-brand/45' : ''}`}>
                  {tracks.map((track) => (
                    <TrackRow
                      key={track.id}
                      track={track}
                      selected={track.id === selectedTrackId}
                      onSelect={(id) => setSelectedTrackId(prev => prev === id ? null : id)}
                      onRemove={removeTrack}
                    />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addTrack}
                  className="mt-2 text-left text-[11px] leading-5 text-muted transition-colors hover:text-brand"
                >
                  点击添加或拖动到此处以继续添加
                </button>
              </div>
            )}
          </section>

          <div className="mt-3 grid grid-cols-3 gap-1.5 border-t border-[var(--xp-line)] pt-3">
            <MiniCount label="视频" value={trackCounts.panoramic_video + trackCounts.ordinary_video} />
            <MiniCount label="照片" value={trackCounts.standard_photos} />
            <MiniCount label="航拍" value={trackCounts.aerial_photos} />
          </div>
          <button
            onClick={() => {
              if (running) return
              if (config.outputDir) setViewerOnlyPath(config.outputDir)
              navigate('/project/results')
            }}
            disabled={running}
            title={running ? '任务运行中暂不可查看点云' : undefined}
            data-enter
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-card py-2 text-[12px] text-muted transition-colors hover:bg-[var(--xp-control-hover)] hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted">
            <Eye className="h-3.5 w-3.5" /> 查看点云
          </button>
        </aside>

        {/* Center — main stage */}
        <main className="liquid-panel stage-grid flex min-h-0 flex-col overflow-y-auto overflow-x-hidden p-4">
          <div data-enter className="mb-3 flex shrink-0 items-start justify-between gap-4">
            <div>
              <h1 className="text-[16px] font-medium text-ink">三维高斯对齐工作台</h1>
            </div>
            <div className="hidden items-center gap-2 lg:flex">
              <span className="rounded-full border border-[var(--xp-line)] bg-brand/8 px-2.5 py-1 text-[11px] font-medium text-brand">{activeEngineName}</span>
              <span className="rounded-full border border-[var(--xp-line)] bg-data/8 px-2.5 py-1 text-[11px] font-medium text-data">COLMAP 输出</span>
            </div>
          </div>

          {/* Output & params toolbar */}
          <div data-enter className="glass-inset mb-3 flex shrink-0 flex-wrap items-end gap-3 rounded-card p-2.5">
            <div className="min-w-0 flex-1">
              <label className="ui-label mb-1.5 block whitespace-nowrap text-[11px]">输出目录</label>
              <div className="flex gap-2">
                <input className={`${inputClass} flex-1 font-mono`} value={config.outputDir} onChange={(event) => {
                  setLoadedProject(null)
                  setProjectSteps(defaultProjectSteps)
                  setConfig({ ...config, outputDir: event.target.value })
                }} placeholder="选择或粘贴输出目录..." spellCheck={false} />
                <IconButton label="选择" onClick={browseOutput}><FolderOpen className="h-4 w-4" /></IconButton>
              </div>
            </div>
          </div>

          {projectLoadedForOutput && loadedProject && (
            <ProjectRestorePanel
              project={loadedProject}
              tracks={tracks}
              steps={projectSteps}
              onToggleStep={updateProjectStep}
            />
          )}

          {/* Advanced parameters — grouped with the output/extract settings above */}
          <section data-enter className="glass-inset relative z-30 mb-3 shrink-0 overflow-visible rounded-card p-3">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="ui-label text-[11px]">高级参数</h3>
              <span className="rounded-full border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-0.5 font-mono text-[11px] text-muted">{activeEngineName}</span>
            </div>
            <div className="flex flex-wrap items-start gap-3">
                <Field label="向上轴">
                  <ThemeSelect className="w-20" value={config.upAxis} onChange={(v) => setConfig({ ...config, upAxis: v })} options={[
                    { value: '+Y', label: '+Y' }, { value: '-Y', label: '-Y' }, { value: '+Z', label: '+Z' }, { value: '-Z', label: '-Z' }, { value: '+X', label: '+X' }, { value: '-X', label: '-X' },
                  ]} />
                </Field>
              {config.alignmentEngine === 'metashape' ? (<>
                <Field label="策略">
                  <div className="glass-control flex h-8 w-24 items-center justify-center rounded-control text-[11px] text-ink/75">稳定分阶段</div>
                </Field>
                <Field label="关键点"><NumberInput value={config.metaKeypointLimit} onChange={(value) => setConfig({ ...config, metaKeypointLimit: value })} /></Field>
                <Field label="连接点"><NumberInput value={config.metaTiepointLimit} onChange={(value) => setConfig({ ...config, metaTiepointLimit: value })} /></Field>
              </>) : (<>
                  <Field label="密度">
                    <ThemeSelect className="w-24" value={config.colmapDensityPreset} onChange={(v) => setConfig({ ...config, colmapDensityPreset: v as ColmapDensityPreset })} options={[
                      { value: 'stable', label: '稳定' }, { value: 'high-density', label: '高密度' }, { value: 'experimental-high-density', label: '实验' },
                    ]} />
                  </Field>
                  <Field label="匹配">
                    <ThemeSelect className="w-20" value={config.colmapMatcher} onChange={(v) => setConfig({ ...config, colmapMatcher: v as ColmapMatcher })} options={[
                      { value: 'sequential', label: '顺序' }, { value: 'exhaustive', label: '穷举' },
                    ]} />
                  </Field>
                  <Field label="图像尺寸"><NumberInput value={config.colmapMaxImageSize} onChange={(value) => setConfig({ ...config, colmapMaxImageSize: value })} /></Field>
                  <Field label="特征点数"><NumberInput value={config.colmapMaxNumFeatures} onChange={(value) => setConfig({ ...config, colmapMaxNumFeatures: value })} /></Field>
                  <div className="ml-auto self-end"><SwitchRow label="GPU 加速" checked={config.colmapUseGpu} onClick={() => setConfig({ ...config, colmapUseGpu: !config.colmapUseGpu })} /></div>
              </>)}
            </div>
          </section>

          {/* Video clip trimmer — shows when a panoramic video track is selected */}
          <section data-enter className="glass-inset mb-3 shrink-0 rounded-card p-2.5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="ui-label flex min-w-0 items-center gap-1.5 text-[11px]">
                <Scissors className="h-3.5 w-3.5 shrink-0" />
                <span className="shrink-0">视频截取</span>
                {selectedTrack && (
                  <span className="truncate font-mono text-[11px] font-normal text-muted">{selectedTrack.label}</span>
                )}
              </h3>
              {selectedTrack?.trim && (
                <button
                  onClick={() => setTracks((prev) => prev.map((t) => (t.id === selectedTrack.id ? { ...t, trim: undefined } : t)))}
                  className="shrink-0 text-[11px] text-muted transition-colors hover:text-danger"
                >
                  清除截取
                </button>
              )}
            </div>
            <div key={selectedTrackId ?? 'empty'} className="motion-fade-up">
            {selectedTrack && isVideoTrack(selectedTrack.type) ? (
              <div className="space-y-2.5">
                <div className="flex flex-wrap items-end gap-2.5 border-b border-[var(--xp-line)] pb-2.5">
                  {selectedTrack.type === 'ordinary_video' && (
                    <Field label="视角">
                      <ThemeSelect
                        className="w-24"
                        value={selectedTrack.cameraProfile ?? 'wide'}
                        onChange={(value) => updateCameraProfile(selectedTrack.id, value as CameraProfile)}
                        options={cameraProfileOptions}
                      />
                    </Field>
                  )}
                  <Field label="帧/秒">
                    <NumberInput
                      value={selectedExtract.framesPerSecond}
                      step={0.1}
                      min={0.01}
                      disabled={selectedExtract.frameLimit > 0}
                      onChange={(value) => updateExtract(selectedTrack.id, {
                        ...selectedExtract,
                        framesPerSecond: value > 0 ? value : 1,
                      })}
                    />
                  </Field>
                  <Field label="帧数上限">
                    <NumberInput
                      value={selectedExtract.frameLimit}
                      onChange={(value) => {
                        const duration = selectedTrack.trim ? Math.max(0, selectedTrack.trim.end - selectedTrack.trim.start) : 0
                        updateExtract(selectedTrack.id, {
                          ...selectedExtract,
                          frameLimit: value,
                          framesPerSecond: framesPerSecondForLimit(duration, value, selectedExtract.framesPerSecond),
                        })
                      }}
                    />
                  </Field>
                </div>
                <VideoTrimmer
                  key={selectedTrack.id}
                  path={selectedTrack.path}
                  trim={selectedTrack.trim}
                  onChange={(trim) => updateTrim(selectedTrack.id, trim)}
                />
              </div>
            ) : (
              <div className="grid place-items-center py-8 text-center text-muted">
                <div>
                  <Scissors className="mx-auto mb-2 h-5 w-5 opacity-50" />
                  <p className="text-[12px]">在左侧选择一个全景视频轨道</p>
                  <p className="mt-0.5 text-[11px] opacity-70">可截取视频片段用于高斯对齐</p>
                </div>
              </div>
            )}
            </div>
          </section>

          {/* Bottom terminal — moved to the right status cell */}

          <div className="mt-auto pt-3">
            <ToastContainer toasts={toasts} onRemove={removeToast} />
          </div>
        </main>

        {/* Right — status cell (3rd column on xl, overlay on smaller) */}
        <div className="hidden xl:contents">
          <StatusCell
            idle={idle}
            canStart={canStart}
            blockReason={blockReason}
            progress={progress}
            running={running}
            outputReady={Boolean(config.outputDir)}
            materialCount={tracks.length}
            engineReady={engineReady}
            logs={logs}
            logRef={logRef}
            onStart={handleStart}
            onCancel={handleCancel}
            onReset={reset}
            onOpenOutput={openOutput}
          />
        </div>
        {/* Right panel overlay for non-xl screens */}
        {!embedded && showRightPanel && (
          <div className="fixed bottom-2 right-2 top-14 z-40 w-[320px] max-w-[calc(100vw-1rem)] xl:hidden animate-in slide-in-from-right-2 duration-200">
            <div className="liquid-panel flex h-full min-h-0 flex-col overflow-y-auto">
            <div className="flex items-center justify-between p-3 pb-0">
              <span className="text-[12px] font-semibold text-ink/60">状态面板</span>
              <button onClick={() => setShowRightPanel(false)} className="motion-press grid h-6 w-6 place-items-center rounded-subtle text-ink/40 hover:text-ink/70">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>
            <StatusCell
              idle={idle}
              canStart={canStart}
              blockReason={blockReason}
              progress={progress}
              running={running}
              outputReady={Boolean(config.outputDir)}
              materialCount={tracks.length}
              engineReady={engineReady}
              logs={logs}
              logRef={logRef}
              onStart={handleStart}
              onCancel={handleCancel}
              onReset={reset}
              onOpenOutput={openOutput}
            />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function MaterialImportDialog({
  open,
  drafts,
  onDraftsChange,
  onCancel,
  onConfirm,
}: {
  open: boolean
  drafts: ImportDraft[]
  onDraftsChange: Dispatch<SetStateAction<ImportDraft[]>>
  onCancel: () => void
  onConfirm: (drafts: ImportDraft[]) => void
}) {
  const [step, setStep] = useState<1 | 2>(1)

  useEffect(() => {
    if (open) setStep(1)
  }, [open])

  if (!open) return null

  const validDrafts = drafts.filter((draft) => isImportValid(draft.info))
  const updateDraft = (id: string, patch: Partial<ImportDraft>) => {
    onDraftsChange((prev) => prev.map((draft) => draft.id === id ? { ...draft, ...patch } : draft))
  }
  const updateDraftExtract = (draft: ImportDraft, extract: ImportDraft['extract']) => {
    updateDraft(draft.id, { extract })
  }
  const changeType = (draft: ImportDraft, type: TrackType) => {
    updateDraft(draft.id, {
      type,
      label: draft.label || labelFromPath(draft.info.path, trackMeta[type].label),
      extract: isVideoTrack(type) ? draft.extract : defaultExtractConfig,
      trim: isVideoTrack(type) ? draft.trim : undefined,
      cameraProfile: defaultCameraProfile(type),
    })
  }
  const typeChoicesFor = (draft: ImportDraft): TrackType[] => {
    if (!isImportValid(draft.info)) return []
    const kind = importKind(draft.info)
    if (kind === 'panoramic_video') return ['panoramic_video']
    if (kind === 'ordinary_video') return ['ordinary_video']
    if (kind === 'standard_photos') return ['standard_photos', 'aerial_photos']
    if (kind === 'aerial_photos') return ['aerial_photos', 'standard_photos']
    return []
  }
  const applyFrameLimit = (draft: ImportDraft, frameLimit: number) => {
    const duration = draft.trim ? Math.max(0, draft.trim.end - draft.trim.start) : Math.max(0, draft.duration ?? 0)
    updateDraftExtract(draft, {
      ...draft.extract,
      frameLimit,
      framesPerSecond: framesPerSecondForLimit(duration, frameLimit, draft.extract.framesPerSecond),
    })
  }

  return createPortal(
    <div className="app-modal-backdrop fixed inset-0 z-[100] grid place-items-center px-4 py-6">
      <div className="app-modal-panel flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden p-0">
        <div className="app-modal-header flex shrink-0 items-center justify-between px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold text-ink">导入素材</h2>
            <p className="mt-1 text-[11px] text-muted">{step === 1 ? '选择每个素材的类型' : '编辑轨道参数和视频片段'}</p>
          </div>
          <button onClick={onCancel} className="motion-press rounded-card px-3 py-2 text-[12px] text-muted transition-colors hover:bg-[var(--xp-control-hover)] hover:text-ink">
            取消
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">
          <div className="flex h-full transition-transform duration-300 ease-out" style={{ transform: step === 1 ? 'translateX(0)' : 'translateX(-100%)' }}>
            <div className="min-w-full overflow-y-auto p-5">
              <div className="space-y-3">
                {drafts.map((draft) => {
                  const choices = typeChoicesFor(draft)
                  return (
            <div key={draft.id} className={`app-modal-card rounded-card p-3 ${isImportValid(draft.info) ? '' : 'opacity-70'}`}>
                      <div className="mb-3 flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-[13px] font-medium text-ink">{draft.info.label || draft.info.name || baseName(draft.info.path)}</p>
                          <p className="mt-1 break-all font-mono text-[11px] text-muted">{draft.info.path}</p>
                        </div>
                        {draft.info.photoCount > 0 && (
                          <span className="app-modal-pill shrink-0 rounded-full px-2 py-1 font-mono text-[11px]">
                            {draft.info.photoCount} photos
                          </span>
                        )}
                      </div>
                      {!isImportValid(draft.info) ? (
                        <p className="rounded-subtle border border-danger/20 bg-danger/10 px-3 py-2 text-[12px] text-danger">{draft.info.message || '不支持的素材'}</p>
                      ) : (
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {choices.map((type) => (
                            <button
                              key={type}
                              type="button"
                              onClick={() => changeType(draft, type)}
                              className={`motion-press flex items-center gap-2 rounded-card border px-3 py-2 text-left transition-all ${
                                draft.type === type
                                  ? 'import-type-option is-selected'
                                  : 'import-type-option'
                              }`}
                            >
                              <span className="icon-tile grid h-8 w-8 place-items-center rounded-comfortable">{trackMeta[type].icon}</span>
                              <span className="min-w-0">
                                <span className="block text-[12px] font-medium">{trackMeta[type].label}</span>
                                <span className="block truncate text-[11px] opacity-75">{trackMeta[type].hint}</span>
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="min-w-full overflow-y-auto p-5">
              <div className="space-y-4">
                {validDrafts.map((draft) => (
                  <div key={draft.id} className="app-modal-card rounded-card p-3">
                    <div className="mb-3 flex flex-wrap items-end gap-3">
                      <Field label="轨道名称">
                        <input
                          className="theme-input w-52 rounded-comfortable border px-3 py-2 text-[13px] outline-none transition-all"
                          value={draft.label}
                          onChange={(event) => updateDraft(draft.id, { label: event.target.value })}
                        />
                      </Field>
                      <span className="app-modal-pill rounded-full px-2.5 py-2 text-[11px]">
                        {trackMeta[draft.type].label}
                      </span>
                    </div>

                    {isVideoTrack(draft.type) ? (
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-end gap-3 border-b border-[var(--xp-line)] pb-3">
                          {draft.type === 'ordinary_video' && (
                            <Field label="视角">
                              <ThemeSelect
                                className="w-24"
                                value={draft.cameraProfile ?? 'wide'}
                                onChange={(value) => updateDraft(draft.id, { cameraProfile: value as CameraProfile })}
                                options={cameraProfileOptions}
                              />
                            </Field>
                          )}
                          <Field label="每秒帧数">
                            <NumberInput
                              value={draft.extract.framesPerSecond}
                              step={0.1}
                              min={0.01}
                              disabled={draft.extract.frameLimit > 0}
                              onChange={(value) => updateDraftExtract(draft, {
                                ...draft.extract,
                                framesPerSecond: value > 0 ? value : 1,
                              })}
                            />
                          </Field>
                          <Field label="最大帧数">
                            <input
                              className="theme-input w-24 rounded-comfortable border px-3 py-2 font-mono text-[13px] outline-none transition-all"
                              inputMode="numeric"
                              placeholder="空"
                              value={draft.extract.frameLimit > 0 ? String(draft.extract.frameLimit) : ''}
                              onChange={(event) => {
                                const raw = event.target.value.trim()
                                applyFrameLimit(draft, raw ? Math.max(0, Number.parseInt(raw, 10) || 0) : 0)
                              }}
                            />
                          </Field>
                          {draft.extract.frameLimit > 0 && (
                            <span className="pb-2 text-[11px] text-muted">
                              已按片段长度计算为 {draft.extract.framesPerSecond.toFixed(3)} 帧/秒
                            </span>
                          )}
                        </div>
                        <VideoTrimmer
                          path={draft.info.path}
                          trim={draft.trim}
                          onDuration={(duration) => updateDraft(draft.id, { duration })}
                          onChange={(trim) => {
                            const duration = Math.max(0, trim.end - trim.start)
                            updateDraft(draft.id, {
                              trim,
                              extract: draft.extract.frameLimit > 0 && duration > 0
                                ? { ...draft.extract, framesPerSecond: framesPerSecondForLimit(duration, draft.extract.frameLimit, draft.extract.framesPerSecond) }
                                : draft.extract,
                            })
                          }}
                        />
                      </div>
                    ) : (
                      <p className="app-modal-path break-all rounded-subtle px-3 py-2 font-mono text-[11px]">
                        {draft.info.path}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="app-modal-footer shrink-0 px-5 py-4">
          <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-ink/10">
            <span className="block h-full rounded-full bg-gradient-to-r from-brand to-data transition-all duration-300" style={{ width: step === 1 ? '50%' : '100%' }} />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="font-mono text-[11px] text-muted">Step {step} / 2</span>
            <div className="flex gap-2">
              {step === 2 && (
                <button onClick={() => setStep(1)} className="glass-control motion-press rounded-card px-4 py-2 text-[13px] text-ink/72 transition-colors hover:text-brand">
                  上一步
                </button>
              )}
              {step === 1 ? (
                <button
                  onClick={() => setStep(2)}
                  disabled={!validDrafts.length}
                  className="theme-action-shadow motion-press rounded-card bg-brand px-4 py-2 text-[13px] font-semibold text-white transition-all hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-[var(--xp-line)] disabled:text-muted disabled:shadow-none"
                >
                  下一步
                </button>
              ) : (
                <button
                  onClick={() => onConfirm(drafts)}
                  disabled={!validDrafts.length}
                  className="theme-action-shadow motion-press rounded-card bg-brand px-4 py-2 text-[13px] font-semibold text-white transition-all hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-[var(--xp-line)] disabled:text-muted disabled:shadow-none"
                >
                  导入
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function MiniCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="glass-inset rounded-card px-2 py-2 text-center">
      <p className="font-mono text-[15px] font-medium text-ink">{value}</p>
      <p className="text-[11px] text-muted">{label}</p>
    </div>
  )
}

function ProjectRestorePanel({
  project,
  tracks,
  steps,
  onToggleStep,
}: {
  project: LoadedProjectState
  tracks: MaterialTrack[]
  steps: ProjectStepSelection
  onToggleStep: (step: keyof ProjectStepSelection) => void
}) {
  const frameCount = tracks.reduce((sum, track) => sum + (track.restoredFrameCount ?? 0), 0)
  const photoCount = tracks.reduce((sum, track) => sum + (track.restoredPhotoCount ?? 0), 0)
  const psxReady = Boolean(project.metashapeProjectPath)
  return (
    <section data-enter className="glass-inset mb-3 shrink-0 rounded-card p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="ui-label text-[11px]">已载入工程</h3>
        <span className="rounded-full border border-brand/20 bg-brand/[0.08] px-2 py-0.5 text-[11px] font-medium text-brand">
          {project.backend || 'metashape'}
        </span>
      </div>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0 space-y-1.5">
          <ProjectPathRow label="manifest" value={project.manifestPath} />
          <ProjectPathRow label="psx" value={project.metashapeProjectPath || '未找到'} muted={!psxReady} />
          <div className="flex flex-wrap gap-1.5 pt-1">
            <span className="rounded-subtle border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-1 font-mono text-[11px] text-muted">{tracks.length} tracks</span>
            <span className="rounded-subtle border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-1 font-mono text-[11px] text-muted">{frameCount} frames</span>
            <span className="rounded-subtle border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-1 font-mono text-[11px] text-muted">{photoCount} photos</span>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <SwitchRow label="抽帧" checked={steps.extract} onClick={() => onToggleStep('extract')} />
          <SwitchRow label="对齐" checked={steps.align} onClick={() => onToggleStep('align')} />
          <SwitchRow label="导出" checked={steps.export} onClick={() => onToggleStep('export')} />
        </div>
      </div>
    </section>
  )
}

function ProjectPathRow({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="w-14 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted">{label}</span>
      <span className={`min-w-0 truncate font-mono text-[11px] ${muted ? 'text-muted' : 'text-ink/70'}`} title={value}>{value}</span>
    </div>
  )
}

function IconButton({ children, label, onClick }: { children: ReactNode; label: string; onClick: () => void }) {
  return (
    <button aria-label={label} title={label} onClick={onClick} className="glass-control motion-press grid h-10 w-11 place-items-center rounded-card text-ink/72 transition-all hover:-translate-y-0.5 hover:text-brand">
      {children}
    </button>
  )
}

function TrackRow({
  track,
  selected,
  onSelect,
  onRemove,
}: {
  track: MaterialTrack
  selected: boolean
  onSelect: (id: string) => void
  onRemove: (id: string) => void
}) {
  const editable = isVideoTrack(track.type)
  return (
    <div
      className={`group flex flex-col border-b border-ink/[0.075] px-2.5 py-2 last:border-b-0 transition-colors animate-in fade-in slide-in-from-bottom-4 duration-300 ${
        selected ? 'bg-brand/[0.06]' : 'hover:bg-ink/[0.04]'
      } ${editable ? 'cursor-pointer' : ''}`}
      onClick={editable ? () => onSelect(track.id) : undefined}
    >
      <span className="flex items-center gap-2">
        <span className={`icon-tile grid h-7 w-7 shrink-0 place-items-center rounded-comfortable ${selected ? 'ring-1 ring-brand/40' : ''}`}>{trackMeta[track.type].icon}</span>
        <span className="shrink-0 rounded border border-ink/10 px-1.5 py-px text-[11px] text-muted">{trackMeta[track.type].label}</span>
        {track.type === 'ordinary_video' && (
          <span className="shrink-0 rounded border border-brand/15 bg-brand/[0.06] px-1.5 py-px text-[11px] text-brand">
            {cameraProfileLabel(track.cameraProfile)}
          </span>
        )}
        {track.trim && <Scissors className="h-3 w-3 shrink-0 text-brand" />}
        <span className="flex-1" />
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(track.id) }}
          className="motion-press grid h-6 w-6 shrink-0 place-items-center rounded-comfortable text-ink/25 transition-all hover:bg-danger/10 hover:text-danger"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </span>
      <span className="mt-1 ml-9 break-all text-[11px] font-medium leading-snug text-ink">
        {track.label}
        {track.type === 'panoramic_video' && <span className="text-muted">{track.path.match(/\.([^.]+)$/)?.[0]}</span>}
      </span>
      <span className="mt-0.5 ml-9 break-all font-mono text-[10px] leading-snug text-muted">{track.path}</span>
      {((track.restoredFrameCount ?? 0) > 0 || (track.restoredPhotoCount ?? 0) > 0) && (
        <span className="mt-1 ml-9 font-mono text-[10px] text-brand/80">
          {track.restoredFrameCount ?? 0} frames / {track.restoredPhotoCount ?? 0} photos
        </span>
      )}
    </div>
  )
}

function ThemeSelect({ value, onChange, options, className }: { value: string; onChange: (value: string) => void; options: { value: string; label: string }[]; className?: string }) {
  const [open, setOpen] = useState(false)
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!open) return

    const updateMenuPosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return

      const padding = 8
      const gap = 6
      const desiredHeight = options.length * 34 + 8
      const spaceBelow = window.innerHeight - rect.bottom - gap - padding
      const spaceAbove = rect.top - gap - padding
      const opensUp = spaceBelow < Math.min(desiredHeight, 220) && spaceAbove > spaceBelow
      const availableSpace = Math.max(48, opensUp ? spaceAbove : spaceBelow)
      const maxHeight = Math.min(260, availableSpace)
      const visibleHeight = Math.min(desiredHeight, maxHeight)
      const width = Math.min(rect.width, window.innerWidth - padding * 2)
      const left = Math.min(Math.max(padding, rect.left), window.innerWidth - width - padding)
      const rawTop = opensUp ? rect.top - gap - visibleHeight : rect.bottom + gap
      const top = Math.min(Math.max(padding, rawTop), window.innerHeight - visibleHeight - padding)

      setMenuPosition({ left, top, width, maxHeight })
    }

    updateMenuPosition()
    window.addEventListener('resize', updateMenuPosition)
    window.addEventListener('scroll', updateMenuPosition, true)
    return () => {
      window.removeEventListener('resize', updateMenuPosition)
      window.removeEventListener('scroll', updateMenuPosition, true)
    }
  }, [open, options.length])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (ref.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }

    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])
  const selected = options.find((o) => o.value === value)
  return (
    <div ref={ref} className={className || ''}>
      <button ref={triggerRef} type="button" onClick={() => setOpen(!open)}
        className={`theme-select-trigger motion-press flex w-full items-center gap-1.5 rounded-comfortable px-2.5 py-2 text-[13px] ${open ? 'is-open' : ''}`}>
        <span className="flex-1 text-left">{selected?.label ?? value}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && menuPosition && createPortal(
        <div
          ref={menuRef}
          className="theme-select-menu fixed animate-in fade-in zoom-in-95 slide-in-from-top-1 duration-150"
          style={{
            left: menuPosition.left,
            top: menuPosition.top,
            width: menuPosition.width,
            maxHeight: menuPosition.maxHeight,
          }}
        >
          {options.map((opt) => (
            <button key={opt.value} type="button" onClick={() => { onChange(opt.value); setOpen(false) }}
              className={`theme-select-option ${opt.value === value ? 'is-selected' : ''}`}>
              {opt.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="block">
      <span className="ui-label mb-1.5 block whitespace-nowrap text-[11px]">{label}</span>
      {children}
    </div>
  )
}

function NumberInput({ value, onChange, step, min = 0, placeholder, disabled = false }: { value: number; onChange: (value: number) => void; step?: number; min?: number; placeholder?: string; disabled?: boolean }) {
  const [edit, setEdit] = useState(String(value))
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { setEdit(String(value)) }, [value])
  const stepSize = step ?? 1
  const decimals = Math.max(0, `${stepSize}`.split('.')[1]?.length ?? 0)
  const format = (next: number) => String(Number(next.toFixed(decimals)))
  const commit = (raw: string) => {
    const n = Number(raw)
    if (!Number.isNaN(n) && n >= min) onChange(n)
    else setEdit(String(value))
  }
  const stepValue = (direction: 1 | -1) => {
    if (disabled) return
    const current = Number(edit)
    const base = Number.isNaN(current) ? value : current
    const next = Math.max(min, Number((base + direction * stepSize).toFixed(decimals)))
    const formatted = format(next)
    setEdit(formatted)
    onChange(next)
    inputRef.current?.focus()
  }
  return (
    <div className="number-input-shell relative w-20">
      <input ref={inputRef} className="theme-input number-input-field w-full rounded-comfortable border px-3 py-2 font-mono text-[13px] outline-none transition-all disabled:cursor-not-allowed disabled:opacity-50"
        type="text" inputMode="decimal" value={edit} placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => { if (disabled) return; setEdit(e.target.value); const n = Number(e.target.value); if (!Number.isNaN(n) && n >= min) onChange(n) }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { inputRef.current?.blur(); commit(edit) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); stepValue(1) }
          else if (e.key === 'ArrowDown') { e.preventDefault(); stepValue(-1) }
        }}
        onBlur={() => commit(edit)}
      />
      <div className="number-stepper">
        <button type="button" className="number-stepper-button disabled:cursor-not-allowed disabled:opacity-35" disabled={disabled} aria-label="增加数值" onMouseDown={(event) => event.preventDefault()} onClick={() => stepValue(1)}>
          <ChevronUp className="h-3 w-3" />
        </button>
        <button type="button" className="number-stepper-button disabled:cursor-not-allowed disabled:opacity-35" disabled={disabled} aria-label="减少数值" onMouseDown={(event) => event.preventDefault()} onClick={() => stepValue(-1)}>
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>
    </div>
  )
}

function SwitchRow({ label, checked, onClick }: { label: string; checked: boolean; onClick: () => void }) {
  return (
    <button role="switch" aria-checked={checked} onClick={onClick} className="motion-press flex items-center justify-between gap-3 whitespace-nowrap rounded-subtle border border-[var(--xp-line)] px-3 py-2 text-[13px] text-ink/65 transition-colors hover:bg-[var(--xp-surface-soft)]">
      <span>{label}</span>
      <span className="relative h-4 w-8 rounded-full transition-colors" style={{ background: checked ? 'var(--xp-brand)' : 'var(--xp-line-strong)' }}>
        <span className="absolute top-0.5 h-3 w-3 rounded-full bg-[var(--xp-surface)] shadow-sm transition-all" style={{ left: checked ? 18 : 2 }} />
      </span>
    </button>
  )
}

interface StatusCellProps {
  idle: boolean
  canStart: boolean
  blockReason: string
  progress: PipelineProgress
  running: boolean
  outputReady: boolean
  materialCount: number
  engineReady: boolean
  logs: string[]
  logRef: React.RefObject<HTMLDivElement | null>
  onStart: () => void
  onCancel: () => void
  onReset: () => void
  onOpenOutput: () => void
}

function logTone(line: string) {
  if (line.includes('错误') || line.includes('失败') || line.includes('中断')) return 'is-danger'
  if (line.includes('完成') || line.includes('已导出')) return 'is-success'
  return ''
}

function StatusCell(props: StatusCellProps) {
  const { idle } = props
  // Smooth the displayed percentage with GSAP so it never snaps.
  const percentRef = useRef<HTMLSpanElement>(null)
  const displayPct = useRef(props.progress.percent)
  useEffect(() => {
    let raf = 0
    const target = Math.min(100, Math.max(0, props.progress.percent))
    if (props.progress.phase === 'idle' && target < displayPct.current) {
      displayPct.current = target
      if (percentRef.current) percentRef.current.textContent = String(Math.round(target))
      return () => cancelAnimationFrame(raf)
    }
    const start = displayPct.current
    const startedAt = performance.now()
    const dur = Math.min(1200, 420 + Math.abs(target - start) * 14)
    const tick = (now: number) => {
      const t = Math.min((now - startedAt) / dur, 1)
      const eased = 1 - Math.pow(1 - t, 3)
      displayPct.current = start + (target - start) * eased
      if (percentRef.current) percentRef.current.textContent = String(Math.round(displayPct.current))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [props.progress.percent, props.progress.phase])

  // Morph between ready / progress content when the idle flag flips.
  const stageRef = useMorphSwap<HTMLDivElement>({ trigger: idle })

  const isError = props.progress.phase === 'error'
  const isDone = props.progress.phase === 'complete'
  const isCanceled = isError && props.progress.message.includes('取消')
  const isRunningView = props.running && !idle
  const bloomClass = isDone ? 'success-bloom' : ''
  const stagePanelSizing = idle ? 'flex-1' : 'flex-none'
  const logPanelSpacing = idle ? 'mt-4 mb-3 shrink-0' : isRunningView ? 'mt-3 mb-3 min-h-44 flex-1' : isDone ? 'mt-4 mb-3 min-h-0 flex-1' : 'mt-4 mb-3 min-h-0 flex-1'
  const logBoxSize = idle ? 'h-28 p-3' : isRunningView ? 'min-h-0 flex-1 p-3' : isDone ? 'min-h-0 flex-1 p-3' : 'min-h-0 flex-1 p-3'
  const readiness = [
    { label: '输出目录', ready: props.outputReady },
    { label: '素材轨道', ready: props.materialCount > 0 },
    { label: '引擎路径', ready: props.engineReady },
  ]

  return (
    <aside className={`liquid-panel stage-grid flex min-h-0 min-w-0 flex-col overflow-x-hidden p-4 ${bloomClass}`}>
      <div className="mb-3 flex items-center justify-between" data-enter>
        <div>
          <h2 className="text-[12px] font-medium text-ink">状态仪表</h2>
        </div>
        <span className="rounded-full border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-1 font-mono text-[11px] text-muted">{idle ? '待命' : props.running ? '运行中' : isDone ? '完成' : isCanceled ? '已取消' : '错误'}</span>
      </div>

      {idle && (
        <div className="glass-inset mb-3 rounded-card p-3" data-enter>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] font-medium text-ink/80">配置完整度</span>
            <span className="font-mono text-[11px] text-muted">{readiness.filter((item) => item.ready).length}/3</span>
          </div>
          <div className="space-y-1.5">
            {readiness.map((item) => (
              <div key={item.label} className="flex items-center justify-between text-[12px]">
                <span className="text-muted">{item.label}</span>
                <span className={`inline-flex items-center gap-1.5 font-mono text-[11px] ${item.ready ? 'text-brand' : 'text-muted'}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${item.ready ? 'bg-brand' : 'bg-ink/18'}`} />
                  {item.ready ? 'OK' : 'WAIT'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div ref={stageRef} className={`relative flex min-h-0 flex-col transition-[flex-basis,height] duration-300 ${stagePanelSizing}`}>
        {idle ? (
          <ReadyContent canStart={props.canStart} blockReason={props.blockReason} readiness={readiness} />
        ) : (
          <ProgressContent
            progress={props.progress}
            running={props.running}
            percentRef={percentRef}
            isError={isError}
            isCanceled={isCanceled}
          />
        )}
      </div>

      {/* Log panel — always visible so the right column is never empty. */}
      <div className={`status-log-panel flex min-h-0 flex-col transition-[flex,margin,height] duration-300 ${logPanelSpacing}`}>
        <div className="mb-1.5 flex shrink-0 items-center justify-between">
          <span className="ui-label flex items-center gap-1.5 text-[11px]">
            <Terminal className="h-3.5 w-3.5" /> 运行日志
          </span>
        </div>
        <div ref={props.logRef} className={`terminal status-log-box min-w-0 max-w-full overflow-y-auto overflow-x-hidden select-text ${logBoxSize}`}>
          {!props.logs.length ? (
            <p className="px-1 py-1 text-[12px] leading-5 text-muted">
              等待任务启动…<span className="terminal-cursor" />
            </p>
          ) : (
            props.logs.map((lineText, index) => (
              <p key={`${lineText}-${index}`} className={`log-line min-w-0 max-w-full ${logTone(lineText)}`}>
                <span className="log-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="log-text">{lineText}</span>
              </p>
            ))
          )}
        </div>
      </div>

      {/* Action buttons — always pinned to the bottom of the cell */}
      <div className="mt-4 space-y-2 shrink-0">
        {idle ? (
          <>
            <button onClick={props.onStart} disabled={!props.canStart} title={!props.canStart ? props.blockReason : undefined} className="theme-action-shadow motion-press inline-flex w-full items-center justify-center gap-2 rounded-card bg-brand px-4 py-3 text-[13px] font-semibold text-white transition-all hover:-translate-y-0.5 hover:bg-brand-hover disabled:cursor-not-allowed disabled:translate-y-0 disabled:bg-[var(--xp-line)] disabled:text-muted disabled:shadow-none">
              <Play className="h-4 w-4" fill="currentColor" /> 开始高斯对齐
            </button>
            <button onClick={props.onOpenOutput} disabled={!props.outputReady} title={!props.outputReady ? '请先选择输出目录' : undefined} className="glass-control motion-press inline-flex w-full items-center justify-center gap-2 rounded-card px-4 py-2.5 text-[13px] font-semibold text-ink/72 transition-all hover:text-brand disabled:cursor-not-allowed disabled:opacity-45">
              <FolderOpen className="h-4 w-4" /> 打开输出目录
            </button>
          </>
        ) : props.running ? (
          <button onClick={props.onCancel} className="motion-press inline-flex w-full items-center justify-center gap-2 rounded-card border border-danger/30 bg-danger/10 px-4 py-2.5 text-[13px] font-medium text-danger transition-colors hover:bg-danger/15">
            <Square className="h-4 w-4" /> 停止任务
          </button>
        ) : isDone ? (
          <>
            <button onClick={props.onOpenOutput} className="theme-action-shadow motion-press inline-flex w-full items-center justify-center gap-2 rounded-card bg-brand px-4 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-brand-hover">
              <FolderOpen className="h-4 w-4" /> 打开输出目录
            </button>
            <button onClick={props.onReset} className="glass-control motion-press inline-flex w-full items-center justify-center gap-2 rounded-card px-4 py-2.5 text-[13px] font-semibold text-ink/72 transition-all hover:text-brand">返回界面</button>
          </>
        ) : isCanceled ? (
          <>
            <button onClick={props.onStart} disabled={!props.canStart} className="theme-action-shadow motion-press inline-flex w-full items-center justify-center gap-2 rounded-card bg-brand px-4 py-2.5 text-[13px] font-semibold text-white transition-all hover:-translate-y-0.5 hover:bg-brand-hover disabled:cursor-not-allowed disabled:translate-y-0 disabled:bg-[var(--xp-line)] disabled:text-muted disabled:shadow-none">
              <Play className="h-4 w-4" fill="currentColor" /> 重新开始
            </button>
            <button onClick={props.onReset} className="glass-control motion-press inline-flex w-full items-center justify-center gap-2 rounded-card px-4 py-2.5 text-[13px] font-semibold text-ink/72 transition-all hover:text-brand">返回界面</button>
          </>
        ) : (
          <>
            <div className="max-w-full overflow-hidden break-all rounded-card border border-danger/20 bg-danger/10 p-3 text-[13px] text-danger">
              {props.progress.message}
            </div>
            <button onClick={props.onStart} disabled={!props.canStart} className="theme-action-shadow motion-press inline-flex w-full items-center justify-center gap-2 rounded-card bg-brand px-4 py-2.5 text-[13px] font-semibold text-white transition-all hover:-translate-y-0.5 hover:bg-brand-hover disabled:cursor-not-allowed disabled:translate-y-0 disabled:bg-[var(--xp-line)] disabled:text-muted disabled:shadow-none">
              <Play className="h-4 w-4" fill="currentColor" /> 重新开始
            </button>
            <button onClick={props.onReset} className="glass-control motion-press inline-flex w-full items-center justify-center gap-2 rounded-card px-4 py-2.5 text-[13px] font-semibold text-ink/72 transition-all hover:text-brand">返回界面</button>
          </>
        )}
      </div>
    </aside>
  )
}

function ReadyContent({
  canStart,
  blockReason,
  readiness,
}: {
  canStart: boolean
  blockReason: string
  readiness: Array<{ label: string; ready: boolean }>
}) {
  const readyCount = readiness.filter((item) => item.ready).length
  const readyPct = Math.round((readyCount / Math.max(readiness.length, 1)) * 100)
  return (
    <div className="glass-inset relative flex flex-1 flex-col justify-between overflow-hidden rounded-card px-4 py-4">
      <div className="pointer-events-none absolute inset-x-8 top-5 h-px bg-gradient-to-r from-transparent via-brand/35 to-transparent" />
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <div className="relative mb-3 grid h-20 w-20 place-items-center">
          <div className={`absolute inset-0 rounded-full border ${canStart ? 'border-brand/35 bg-brand/8' : 'border-[var(--xp-line)] bg-[var(--xp-control)]'}`} />
          <div className={`absolute h-12 w-12 rounded-full ${canStart ? 'bg-brand/15 shadow-[0_0_28px_rgba(var(--xp-brand-rgb),0.22)]' : 'bg-ink/8'}`} />
          {canStart ? <CheckCircle2 className="relative h-7 w-7 text-brand" /> : <Gauge className="relative h-7 w-7 text-muted" />}
        </div>
        <p className="text-[13px] font-semibold text-ink">{canStart ? '准备就绪' : '等待配置'}</p>
        <p className="mt-1 max-w-[210px] text-[11px] leading-5 text-muted">
          {canStart ? '配置已满足，可以开始对齐。' : blockReason}
        </p>
        <div className="mt-3 h-1.5 w-full max-w-[190px] overflow-hidden rounded-full bg-ink/10">
          <span className="block h-full rounded-full bg-gradient-to-r from-brand to-data transition-all duration-500" style={{ width: `${readyPct}%` }} />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {readiness.map((item) => (
          <div key={item.label} className="rounded-subtle border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-2 text-center">
            <span className={`mx-auto mb-1 block h-1.5 w-1.5 rounded-full ${item.ready ? 'bg-brand shadow-[0_0_10px_rgba(var(--xp-brand-rgb),0.45)]' : 'bg-ink/18'}`} />
            <p className="truncate text-[10px] font-medium text-ink/65">{item.label}</p>
            <p className={`mt-0.5 font-mono text-[10px] ${item.ready ? 'text-brand' : 'text-muted'}`}>{item.ready ? 'OK' : 'WAIT'}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function formatEtaDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds || 0))
  const hours = Math.floor(safe / 3600)
  const minutes = Math.floor((safe % 3600) / 60)
  const secs = safe % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

function ProgressContent({
  progress,
  running,
  percentRef,
  isError,
  isCanceled,
}: {
  progress: StatusCellProps['progress']
  running: boolean
  percentRef: React.RefObject<HTMLSpanElement | null>
  isError: boolean
  isCanceled: boolean
}) {
  const elapsed = progress.elapsed
  const time = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`
  const percent = Math.min(100, Math.max(0, progress.percent))
  const circumference = 2 * Math.PI * 44
  const dashOffset = circumference * (1 - percent / 100)
  // Orbit dot position on the ring (starts at 12 o'clock, goes clockwise)
  const orbitAngle = -Math.PI / 2 + (percent / 100) * 2 * Math.PI
  const orbitX = 60 + 44 * Math.cos(orbitAngle)
  const orbitY = 60 + 44 * Math.sin(orbitAngle)
  const etaInScope = running && (progress.phase === 'extract' || progress.phase === 'export')
  const etaSeconds = progress.etaSeconds
  const etaText = typeof etaSeconds === 'number' && Number.isFinite(etaSeconds)
    ? formatEtaDuration(etaSeconds)
    : '\u6b63\u5728\u4f30\u7b97'
  const alignmentRate = typeof progress.alignmentRate === 'number' && Number.isFinite(progress.alignmentRate)
    ? Math.max(0, Math.min(100, progress.alignmentRate))
    : null
  const hasAlignmentRate = alignmentRate !== null && typeof progress.alignedCameras === 'number' && typeof progress.totalCameras === 'number'
  const phaseText = progress.phase === 'idle' && running ? '启动中' : (phaseLabels[progress.phase] || progress.message)

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className="liquid-card progress-art relative overflow-hidden p-4">
        {running && <div className="scan-line" />}
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-ink/78">
            <Gauge className="h-3.5 w-3.5 text-brand" /> 总进度
          </span>
          <div className="flex flex-wrap justify-end gap-1.5">
            {etaInScope && (
              <span className="rounded-full border border-brand/20 bg-brand/[0.08] px-2 py-1 font-mono text-[11px] text-brand">
                <span className="mr-1 text-ink/45">ETA</span>{etaText}
              </span>
            )}
            <span className="rounded-full border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-1 font-mono text-[11px] text-muted">{time}</span>
          </div>
        </div>

        <div className="relative mx-auto mt-3 grid h-40 w-40 place-items-center">
          <svg viewBox="0 0 120 120" className="absolute inset-0 h-full w-full overflow-visible">
            <defs>
              <linearGradient id="progress-ring-gradient" x1="16" y1="18" x2="104" y2="104" gradientUnits="userSpaceOnUse">
                <stop stopColor="var(--xp-brand)" />
                <stop offset="0.55" stopColor="var(--xp-data)" />
                <stop offset="1" stopColor="var(--xp-brand-hover)" />
              </linearGradient>
              <filter id="progress-ring-glow" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="3.4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <circle cx="60" cy="60" r="48" fill="none" stroke="color-mix(in srgb, var(--xp-ink) 9%, transparent)" strokeWidth="1" />
            <circle cx="60" cy="60" r="39" fill="none" stroke="color-mix(in srgb, var(--xp-data) 32%, transparent)" strokeDasharray="5 10" strokeWidth="1.5" />
            <circle cx="60" cy="60" r="44" fill="none" stroke="color-mix(in srgb, var(--xp-ink) 11%, transparent)" strokeWidth="8" />
            <circle
              cx="60"
              cy="60"
              r="44"
              fill="none"
              stroke="url(#progress-ring-gradient)"
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
              strokeLinecap="round"
              strokeWidth="7"
              filter="url(#progress-ring-glow)"
              style={{ transform: 'rotate(-90deg)', transformOrigin: '60px 60px', transition: 'stroke-dashoffset 1000ms cubic-bezier(0.22, 1, 0.36, 1)' }}
            />
            {/* Progress dot tracks completion; orbit marker shows that the task is actively running. */}
            <circle cx={orbitX} cy={orbitY} r="3" fill="var(--xp-data)" filter="url(#progress-ring-glow)" className="transition-all duration-1000 ease-out" />
            <g className={`progress-orbit ${running ? 'is-running' : ''}`}>
              <circle cx="60" cy="16" r="5.5" fill="color-mix(in srgb, var(--xp-surface) 78%, transparent)" stroke="var(--xp-brand)" strokeWidth="1.4" />
              <circle cx="60" cy="16" r="3" fill="url(#progress-ring-gradient)" />
            </g>
          </svg>

          <div className="relative z-10 text-center">
            <p className="font-mono text-[32px] font-semibold leading-none text-ink">
              <span ref={percentRef} className="digit-glow">{Math.round(percent)}</span>
              <span className="ml-0.5 text-[16px] text-muted">%</span>
            </p>
            <p className="mt-1.5 font-mono text-[11px] text-muted">
              {running ? phaseText : isCanceled ? '已取消' : isError ? '已中断' : '处理完成'}
            </p>
          </div>
        </div>

        <div className={`progress-bar-shimmer mt-3 h-1.5 overflow-hidden rounded-full bg-ink/10 ${running ? '' : 'opacity-60'}`}>
          <span className="block h-full rounded-full bg-gradient-to-r from-brand via-data to-brand-hover transition-all duration-1000 ease-out" style={{ width: `${percent}%` }} />
        </div>
        <p className="mt-3 text-center text-[13px] font-medium" style={{ color: isError ? 'rgb(var(--xp-danger-rgb))' : 'var(--xp-muted)' }}>
          {phaseText}
        </p>
      </div>

      {hasAlignmentRate && (
        <div className="glass-inset flex items-center justify-between rounded-card px-3 py-2">
          <span className="inline-flex items-center gap-2 text-[11px] font-medium text-ink/65">
            <Crosshair className="h-3.5 w-3.5 text-brand" />
            {'\u5bf9\u9f50\u7387'}
          </span>
          <span className="font-mono text-[12px] text-ink">
            {Math.round(alignmentRate)}%
            <span className="ml-2 text-[11px] text-muted">
              {progress.alignedCameras}/{progress.totalCameras}
            </span>
          </span>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        {(['extract', 'align', 'export'] as const).map((phase) => (
          <PhasePill key={phase} phaseKey={phase} label={stageLabels[phase]} percent={progress.phasePercents[phase]} active={progress.phase === phase} />
        ))}
      </div>
    </div>
  )
}

const phaseIcons: Record<string, ReactNode> = {
  extract: <Camera className="h-4 w-4" />,
  align: <Crosshair className="h-4 w-4" />,
  export: <FolderOpen className="h-4 w-4" />,
}

function PhasePill({ phaseKey, label, percent, active }: { phaseKey: string; label: string; percent: number; active: boolean }) {
  const [displayPercent, setDisplayPercent] = useState(percent)
  const displayPercentRef = useRef(displayPercent)
  useEffect(() => {
    displayPercentRef.current = displayPercent
  }, [displayPercent])
  useEffect(() => {
    let raf = 0
    const start = displayPercentRef.current
    const rawTarget = Math.min(100, Math.max(0, percent))
    if (rawTarget <= 0 && !active) {
      setDisplayPercent(0)
      return () => {}
    }
    const target = Math.max(start, rawTarget)
    const startedAt = performance.now()
    const dur = Math.min(1000, 360 + Math.abs(target - start) * 12)
    const tick = (now: number) => {
      const t = Math.min((now - startedAt) / dur, 1)
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplayPercent(Math.min(100, Math.max(0, start + (target - start) * eased)))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [active, percent])

  const done = displayPercent >= 99.5
  return (
    <div className={`glass-inset relative overflow-hidden rounded-card px-2 py-2.5 text-center transition-colors ${active ? 'ring-1 ring-brand/25' : ''}`}>
      {done ? <CheckCircle2 className="mx-auto h-4 w-4 text-brand" /> : <div className={`flex justify-center ${active ? 'text-brand' : 'text-muted/50'}`}>{phaseIcons[phaseKey]}</div>}
      <p className="mt-1.5 text-[11px] font-medium text-ink/65">{label}</p>
      <p className="mt-0.5 font-mono text-[11px] text-muted">{Math.round(displayPercent)}%</p>
    </div>
  )
}
