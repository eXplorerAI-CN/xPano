import { useCallback, useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { JobEvent, JobRecovery, JobSnapshot, ProjectMediaItem, XpanoProjectV2 } from '../lib/contracts'
import { recoverJobView } from '../lib/jobRecovery'
import { appendBoundedLog, mergeMediaItemBatch } from '../lib/pipelineBuffers'
import { pipelineInputTracks, pipelineStartCommand } from '../lib/pipelineStartCommand'
import { sanitizeProgress } from '../lib/pipelineProgress'
import type { MaterialTrack, PipelineComplete, PipelineConfig, PipelineError, PipelineProgress, ProjectRunOptions } from '../lib/types'
import type { TrainingConfig } from '../features/training/trainingConfig'
import { commandErrorMessage } from '../lib/commandError'
import { jobIdentityMatchesProject } from '../lib/jobIdentity'

function detectPython(): string {
  // Empty string lets the backend resolve bundled Python first
  return ''
}

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

const defaultTrackExtract = { framesPerSecond: 1.0, frameLimit: 0 }
const MAX_LOG_LINES = 500
const MAX_LIVE_MEDIA_ITEMS_PER_TRACK = 240
const MEDIA_ITEM_FLUSH_MS = 80

const initialProgress: PipelineProgress = {
  phase: 'idle',
  percent: 0,
  message: '等待开始',
  elapsed: 0,
  phasePercents: { extract: 0, align: 0, export: 0 },
}

const devQuery = import.meta.env.DEV && typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null
const devTrainingPreview = devQuery?.get('preview') === 'training-running'
const devRunningPreview = devQuery?.get('running') === '1' || devTrainingPreview
const devProgress: PipelineProgress = devTrainingPreview ? {
  phase: 'train',
  stage: 'training.optimize',
  percent: 41.6,
  message: '正在优化高斯参数',
  elapsed: 2178,
  phasePercents: { extract: 100, align: 100, export: 100 },
  heartbeat: true,
  current: 12480,
  total: 30000,
  etaSeconds: 1104,
  loss: 0.031284,
  splatCount: 824512,
} : {
  phase: 'align',
  stage: 'metashape.frame.match',
  percent: 82,
  message: '正在匹配新增普通素材',
  elapsed: 512,
  phasePercents: { extract: 100, align: 72, export: 0 },
  heartbeat: true,
}
const devLogs = devTrainingPreview ? [
  '00:00 · 训练 · 已创建训练任务',
  '00:04 · 训练 · LichtFeld 训练窗口已启动',
  '04:18 · 训练 · 已完成 3000 / 30000 次迭代',
  '18:47 · 训练 · 已完成 9000 / 30000 次迭代',
  '36:18 · 训练 · 正在优化高斯参数',
] : [
  '00:00 · 准备 · 已锁定 alignment revision 7',
  '00:02 · 对齐 · 已导入 100 组全景帧',
  '02:18 · 对齐 · 全景骨架求解完成',
  '04:03 · 对齐 · 已导入 40 张普通视频帧',
  '08:32 · 对齐 · 正在按视觉内容匹配平面素材',
]

const phaseLabels: Record<PipelineProgress['phase'], string> = {
  idle: '准备',
  extract: '抽帧',
  align: '对齐',
  export: '导出',
  train: '训练',
  complete: '完成',
  error: '错误',
}

function clampPercent(value: number | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.min(100, Math.max(0, value))
}

function formatElapsed(seconds: number | undefined): string {
  const safe = Math.max(0, Math.floor(seconds || 0))
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`
}

let lastExtractProgress = ''
let lastLoggedExtractBucket = -1

function friendlyProgressMessage(phase: PipelineProgress['phase'], percent: number): string {
  if (phase === 'extract') return lastExtractProgress || '正在抽取影像帧'
  if (phase === 'align') return percent < 36 ? '正在初始化对齐引擎' : '正在匹配特征并对齐相机'
  if (phase === 'export') return '正在导出 COLMAP 数据和点云索引'
  if (phase === 'train') return '正在训练三维高斯模型'
  if (phase === 'complete') return '重建任务已完成'
  if (phase === 'error') return '任务已中断'
  return '任务正在启动'
}

function friendlyRawMessage(raw: string): string {
  const text = raw.trim()
  if (!text) return ''

  const extractMatch = text.match(/^extract progress\s+(\d+)\/(\d+)/i)
  if (extractMatch) return `已抽取 ${extractMatch[1]}/${extractMatch[2]} 帧`

  const colmapDone = text.match(/^COLMAP_STAGE_DONE:\s*(.+)$/i)
  if (colmapDone) return `COLMAP 阶段完成：${colmapDone[1]}`

  const colmapCommand = text.match(/^COLMAP\s+([^:]+):/i)
  if (colmapCommand) return `COLMAP 正在执行：${colmapCommand[1]}`

  if (text.includes('开始抽帧')) return '开始抽取视频帧'
  if (text.includes('开始 Metashape')) return '启动 Metashape 自动对齐'
  if (text.includes('开始 COLMAP')) return '启动 COLMAP 自动处理'
  if (text.includes('应用向上轴')) return '正在应用向上轴设置'
  if (text.includes('导出 COLMAP')) return '正在导出 COLMAP 文件'
  if (text === '完成' || text.includes('job complete')) return '任务已完成'
  if (text.startsWith('>>>')) return text.replace(/^>>>\s*/, '')
  if (text.startsWith('WARN:')) return `注意：${text.slice(5).trim()}`
  return text
}

function formatLogLine(message: string, phase?: PipelineProgress['phase'], elapsed = 0, percent?: number): string {
  const safeMessage = friendlyRawMessage(message)
  const pct = typeof percent === 'number' ? ` · ${Math.round(clampPercent(percent))}%` : ''
  if (phase) return `${formatElapsed(elapsed)} · ${phaseLabels[phase]}${pct} · ${safeMessage}`
  return `${formatElapsed(elapsed)}${pct} · ${safeMessage}`
}

function shouldLogProgressEvent(event: PipelineProgress): boolean {
  if (event.heartbeat) return false
  const stage = event.stage || event.phase
  if (event.phase === 'extract' && stage === 'extract.frames') {
    const bucket = Math.floor(clampPercent(event.phasePercents?.extract) / 10)
    if (bucket <= lastLoggedExtractBucket && bucket < 10) return false
    lastLoggedExtractBucket = bucket
    return true
  }
  if (event.phase === 'extract' && /^已抽取\s+\d+\/\d+\s+帧$/.test(event.message || '')) return false
  return true
}

function logPercentForEvent(event: PipelineProgress): number {
  if (event.phase === 'extract') return clampPercent(event.phasePercents?.extract)
  if (event.phase === 'align') return clampPercent(event.phasePercents?.align)
  if (event.phase === 'export') return clampPercent(event.phasePercents?.export)
  return clampPercent(event.percent)
}

function appendLog(prev: string[], line: string): string[] {
  return appendBoundedLog(prev, line, MAX_LOG_LINES)
}

export function usePipeline(projectRoot = '') {
  const [progress, setProgress] = useState<PipelineProgress>(devRunningPreview ? devProgress : initialProgress)
  const [running, setRunning] = useState(devRunningPreview)
  const [logs, setLogs] = useState<string[]>(devRunningPreview ? devLogs : [])
  const [preview, setPreview] = useState<{ left: string; right: string } | null>(null)
  const [mediaItems, setMediaItems] = useState<Record<string, ProjectMediaItem[]>>({})
  const unlisteners = useRef<Array<() => void>>([])
  const runningRef = useRef(devRunningPreview)
  const startTimeRef = useRef(devRunningPreview ? Date.now() - devProgress.elapsed * 1000 : 0)
  const mediaItemBufferRef = useRef<Record<string, ProjectMediaItem[]>>({})
  const mediaItemFlushTimerRef = useRef<number | null>(null)
  const activeJobIdRef = useRef<string | null>(null)
  const activeJobSequenceRef = useRef(0)

  const flushMediaItems = useCallback(() => {
    mediaItemFlushTimerRef.current = null
    const pending = mediaItemBufferRef.current
    mediaItemBufferRef.current = {}
    if (Object.keys(pending).length === 0) return
    setMediaItems((previous) => {
      const next = { ...previous }
      Object.entries(pending).forEach(([trackId, items]) => {
        next[trackId] = mergeMediaItemBatch(previous[trackId] ?? [], items, MAX_LIVE_MEDIA_ITEMS_PER_TRACK)
      })
      return next
    })
  }, [])

  const enqueueMediaItem = useCallback((trackId: string, item: ProjectMediaItem) => {
    const pending = mediaItemBufferRef.current[trackId] ?? []
    pending.push(item)
    mediaItemBufferRef.current[trackId] = pending
    if (mediaItemFlushTimerRef.current === null) {
      mediaItemFlushTimerRef.current = window.setTimeout(flushMediaItems, MEDIA_ITEM_FLUSH_MS)
    }
  }, [flushMediaItems])

  const clearMediaItems = useCallback(() => {
    if (mediaItemFlushTimerRef.current !== null) window.clearTimeout(mediaItemFlushTimerRef.current)
    mediaItemFlushTimerRef.current = null
    mediaItemBufferRef.current = {}
    setMediaItems({})
  }, [])

  const syncElapsed = useCallback((elapsed: number) => {
    if (!runningRef.current) return
    const safeElapsed = Math.max(0, Math.floor(elapsed || 0))
    setProgress((prev) => {
      if (prev.phase === 'complete' || prev.phase === 'error') return prev
      if (safeElapsed <= prev.elapsed) return prev
      return { ...prev, elapsed: safeElapsed }
    })
  }, [])

  const setPipelineRunning = useCallback((value: boolean) => {
    runningRef.current = value
    setRunning(value)
  }, [])

  useEffect(() => {
    if (!isTauriRuntime()) return
    let disposed = false

    const setup = async () => {
      const listeners: Array<() => void> = []
      const track = (unlisten: () => void) => {
        if (disposed) {
          unlisten()
          return false
        }
        listeners.push(unlisten)
        unlisteners.current = listeners
        return true
      }

      const progressUnlisten = await listen<PipelineProgress>('pipeline:progress', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        // Only update progress for genuine progress events (with phase); log lines have empty phase
        if (event.payload.phase) {
          if (!runningRef.current) return
          const eventMessage = event.payload.message?.trim()
          const displayMessage = eventMessage && !/^进度\s+\d+%$/.test(eventMessage)
            ? eventMessage
            : friendlyProgressMessage(event.payload.phase, event.payload.percent)
          let sanitizedEvent: PipelineProgress | null = null
          setProgress((prev) => {
            const next = sanitizeProgress({ ...event.payload, message: displayMessage }, prev)
            sanitizedEvent = next
            // Clear frame preview once extraction finishes
            if (next.phase !== 'extract' && prev.phase === 'extract') setPreview(null)
            return next
          })
          if (shouldLogProgressEvent({ ...event.payload, message: displayMessage })) {
            const logEvent = sanitizedEvent ?? { ...event.payload, message: displayMessage }
            setLogs((prev) => appendLog(prev, formatLogLine(
              displayMessage,
              event.payload.phase,
              event.payload.elapsed,
              logPercentForEvent(logEvent),
            )))
          }
        } else {
          const msg = event.payload.message
          syncElapsed(event.payload.elapsed)
          // Skip raw "extract progress" lines — the progress event already covers this
          if (!/^extract progress/i.test(msg)) {
            setLogs((prev) => appendLog(prev, formatLogLine(msg, undefined, event.payload.elapsed)))
          }
          const m = msg.match(/extract progress (\d+)\/(\d+)/i)
          if (m) lastExtractProgress = `已抽取 ${m[1]}/${m[2]} 帧`
        }
      })

      if (!track(progressUnlisten)) return

      const completeUnlisten = await listen<PipelineComplete>('pipeline:complete', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        if (!runningRef.current) return
        flushMediaItems()
        const finalElapsed = startTimeRef.current ? Math.floor((Date.now() - startTimeRef.current) / 1000) : 0
        setPipelineRunning(false)
        setProgress((prev) => ({ ...prev, phase: 'complete', percent: 100, elapsed: Math.max(prev.elapsed, finalElapsed), message: '处理完成', phasePercents: { extract: 100, align: 100, export: 100 } }))
        setLogs((prev) => appendLog(prev, formatLogLine(event.payload.outputPath ? '任务已完成，输出目录已就绪' : '任务已完成', 'complete', finalElapsed, 100)))
        setPreview(null)
      })

      if (!track(completeUnlisten)) return

      const errorUnlisten = await listen<PipelineError>('pipeline:error', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        if (!runningRef.current && event.payload.error !== '任务已取消') return
        flushMediaItems()
        const finalElapsed = startTimeRef.current ? Math.floor((Date.now() - startTimeRef.current) / 1000) : 0
        setPipelineRunning(false)
        setProgress((prev) => ({ ...prev, phase: 'error', elapsed: Math.max(prev.elapsed, finalElapsed), message: event.payload.error }))
        setLogs((prev) => appendLog(prev, formatLogLine(`错误：${event.payload.error}`, 'error', finalElapsed)))
        setPreview(null)
      })

      if (!track(errorUnlisten)) return

      const previewUnlisten = await listen<{ left: string; right: string; projectRoot?: string | null }>('pipeline:preview', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        setPreview(event.payload)
      })
      if (!track(previewUnlisten)) return

      const mediaItemUnlisten = await listen<{ trackId: string; item: ProjectMediaItem; projectRoot?: string | null }>('pipeline:media-item', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        const { trackId, item } = event.payload
        if (!trackId || !item?.id) return
        enqueueMediaItem(trackId, item)
      })
      if (!track(mediaItemUnlisten)) return

      const jobEventUnlisten = await listen<JobEvent>('job:event', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        if (event.payload.sequence <= activeJobSequenceRef.current) return
        activeJobSequenceRef.current = event.payload.sequence
        if (event.payload.state === 'running' || event.payload.state === 'queued' || event.payload.state === 'cancelling') {
          activeJobIdRef.current = event.payload.jobId
        }
      })
      if (!track(jobEventUnlisten)) return

      const jobSnapshotUnlisten = await listen<JobSnapshot>('job:snapshot', (event) => {
        if (!jobIdentityMatchesProject(event.payload.projectRoot, projectRoot)) return
        if (activeJobIdRef.current && event.payload.jobId !== activeJobIdRef.current) return
        if (event.payload.sequence < activeJobSequenceRef.current) return
        activeJobSequenceRef.current = event.payload.sequence
        activeJobIdRef.current = ['queued', 'running', 'cancelling'].includes(event.payload.state)
          ? event.payload.jobId
          : null
      })
      if (!track(jobSnapshotUnlisten)) return

      const backendRunning = await invoke<boolean>('is_pipeline_running').catch(() => false)
      if (projectRoot) {
        const recovery = await invoke<JobRecovery>(backendRunning ? 'get_job_recovery' : 'recover_job_state', {
          projectRoot,
          afterSequence: 0,
        }).catch(() => null)
        if (!disposed && recovery) {
          const recovered = recoverJobView(recovery.snapshots, recovery.events)
          if (recovered && recovered.sequence >= activeJobSequenceRef.current) {
            activeJobSequenceRef.current = recovered.sequence
            activeJobIdRef.current = recovered.activeJobId
            startTimeRef.current = Date.now() - recovered.progress.elapsed * 1000
            setPipelineRunning(recovered.running)
            setProgress(recovered.progress)
            setLogs(recovered.logs)
          }
        }
      }

      if (!disposed && backendRunning && !runningRef.current && (!projectRoot || Boolean(activeJobIdRef.current))) {
        startTimeRef.current = Date.now()
        setPipelineRunning(true)
        setProgress((prev) => ({ ...prev, phase: 'extract', message: '正在恢复后台任务状态' }))
      }
    }

    setup().catch((error) => {
      setLogs((prev) => appendLog(prev, formatLogLine(`事件监听初始化失败：${error}`, 'error')))
    })
    return () => {
      disposed = true
      // WARN: React cleanup also runs during HMR and route refreshes; only explicit cancel or window shutdown may stop the backend job.
      unlisteners.current.forEach((unlisten) => unlisten())
      unlisteners.current = []
      if (mediaItemFlushTimerRef.current !== null) window.clearTimeout(mediaItemFlushTimerRef.current)
      mediaItemFlushTimerRef.current = null
      mediaItemBufferRef.current = {}
    }
  }, [enqueueMediaItem, flushMediaItems, projectRoot, setPipelineRunning, syncElapsed])

  useEffect(() => {
    if (!running) return
    const tick = () => {
      if (!startTimeRef.current) return
      syncElapsed((Date.now() - startTimeRef.current) / 1000)
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [running, syncElapsed])

  const buildArgs = useCallback((tracks: MaterialTrack[], config: PipelineConfig): string[] => {
    const args: string[] = []
    args.push('--output', config.outputDir)

    for (const track of tracks.filter((item) => item.type === 'panoramic_video')) {
      if (track.path) args.push('--pano', track.path)
      // Trim window pairs with --pano by position (see run_xpano_tracks_job.py).
      args.push('--pano-start', String(track.trim?.start ?? 0))
      args.push('--pano-end', String(track.trim?.end ?? 0))
      args.push('--pano-frames-per-second', String(track.extract?.framesPerSecond ?? defaultTrackExtract.framesPerSecond))
      args.push('--pano-max-frames', String(track.extract?.frameLimit ?? defaultTrackExtract.frameLimit))
    }
    for (const track of tracks.filter((item) => item.type === 'ordinary_video')) {
      if (track.path) {
        args.push('--ordinary-video', track.path)
        args.push('--ordinary-view', track.cameraProfile ?? 'wide')
      }
      // Trim window pairs with --ordinary-video by position (see run_xpano_tracks_job.py).
      args.push('--ordinary-start', String(track.trim?.start ?? 0))
      args.push('--ordinary-end', String(track.trim?.end ?? 0))
      args.push('--ordinary-frames-per-second', String(track.extract?.framesPerSecond ?? defaultTrackExtract.framesPerSecond))
      args.push('--ordinary-max-frames', String(track.extract?.frameLimit ?? defaultTrackExtract.frameLimit))
    }
    for (const track of tracks.filter((item) => item.type === 'standard_photos')) {
      if (track.path) args.push('--standard-track', track.label, track.path)
    }
    for (const track of tracks.filter((item) => item.type === 'aerial_photos')) {
      if (track.path) args.push('--aerial-track', track.label, track.path)
    }

    args.push('--frames-per-second', String(config.framesPerSecond))
    if (config.frameLimit > 0) args.push('--max-frames', String(config.frameLimit))

    if (config.alignmentEngine === 'metashape') {
      if (config.metashapePath && config.metashapePath !== 'metashape.exe') {
        args.push('--metashape', config.metashapePath)
      }
      args.push('--metashape-alignment-mode', config.metaAlignmentMode)
      args.push('--metashape-keypoint-limit', String(config.metaKeypointLimit))
      args.push('--metashape-tiepoint-limit', String(config.metaTiepointLimit))
      if (config.metaComponentKey) args.push('--component-key', config.metaComponentKey)
    } else {
      args.push('--backend', 'colmap')
      if (config.colmapPath) args.push('--colmap', config.colmapPath)
      args.push('--colmap-density-preset', config.colmapDensityPreset)
      args.push('--colmap-matcher', config.colmapMatcher)
      if (config.colmapUseGpu) args.push('--colmap-use-gpu')
      args.push('--colmap-max-image-size', String(config.colmapMaxImageSize))
      args.push('--colmap-max-num-features', String(config.colmapMaxNumFeatures))
    }

    args.push('--up-axis', config.upAxis)

    return args
  }, [])

  const start = useCallback(async (tracks: MaterialTrack[], config: PipelineConfig, options: ProjectRunOptions = {}) => {
    if (!isTauriRuntime()) {
      setLogs((prev) => appendLog(prev, formatLogLine('浏览器预览模式下不能启动高斯对齐，请在 Tauri 桌面应用中运行。', 'error')))
      return false
    }

    setPipelineRunning(true)
    setLogs([])
    setPreview(null)
    clearMediaItems()
    lastExtractProgress = ''
    lastLoggedExtractBucket = -1
    startTimeRef.current = Date.now()
    setProgress({
      phase: 'idle',
      percent: 0,
      message: '启动中...',
      elapsed: 0,
      phasePercents: { extract: 0, align: 0, export: 0 },
    })
    setLogs((prev) => appendLog(prev, formatLogLine('正在创建任务并检查参数', 'idle', 0, 0)))

    try {
      const args = buildArgs(pipelineInputTracks(tracks, options), config)
      if (options.manifestPath) args.push('--manifest', options.manifestPath)
      if (options.skipExtract) args.push('--skip-extract')
      if (options.reexportOnly) args.push('--reexport-existing-project')
      if (options.existingProjectPath) args.push('--existing-project', options.existingProjectPath)
      const command = pipelineStartCommand(options)
      const result = await invoke<XpanoProjectV2 | string>(command, {
        ...(options.reconstruction ?? {}),
        pythonExe: detectPython(),
        script: 'scripts/run_xpano_tracks_job.py',
        args,
      })
      if (command === 'start_reconstruction_job' && typeof result === 'object') {
        const active = [...result.jobs].reverse().find((job) => (
          job.workspace === 'reconstruction' && ['queued', 'running', 'cancelling'].includes(job.state)
        ))
        activeJobIdRef.current = active?.jobId ?? null
        activeJobSequenceRef.current = active?.sequence ?? 0
      }
      return true
    } catch (error) {
      const message = commandErrorMessage(error)
      setPipelineRunning(false)
      setProgress((prev) => ({ ...prev, phase: 'error', message }))
      setLogs((prev) => appendLog(prev, formatLogLine(`启动失败：${message}`, 'error')))
      return false
    }
  }, [buildArgs, clearMediaItems, setPipelineRunning])

  const startMedia = useCallback(async (projectRoot: string, expectedRevision: number, targetTrackIds: string[]) => {
    if (!isTauriRuntime()) {
      setLogs((prev) => appendLog(prev, formatLogLine('浏览器预览模式下不能启动素材准备，请在 Tauri 桌面应用中运行。', 'error')))
      return false
    }
    setPipelineRunning(true)
    setLogs([])
    setPreview(null)
    clearMediaItems()
    lastExtractProgress = ''
    lastLoggedExtractBucket = -1
    startTimeRef.current = Date.now()
    setProgress({
      phase: 'extract',
      stage: 'media.probe',
      percent: 0,
      message: '正在检查素材',
      elapsed: 0,
      phasePercents: { extract: 0, align: 0, export: 0 },
    })
    setLogs((prev) => appendLog(prev, formatLogLine('正在创建素材准备任务', 'extract', 0, 0)))
    try {
      await invoke('start_media_job', { projectRoot, expectedRevision, targetTrackIds })
      return true
    } catch (error) {
      const message = commandErrorMessage(error)
      setPipelineRunning(false)
      setProgress((prev) => ({ ...prev, phase: 'error', message }))
      setLogs((prev) => appendLog(prev, formatLogLine(`启动失败：${message}`, 'error')))
      return false
    }
  }, [clearMediaItems, setPipelineRunning])

  const startTraining = useCallback(async (trainingProjectRoot: string, expectedRevision: number, config: TrainingConfig) => {
    if (!isTauriRuntime()) {
      setLogs((prev) => appendLog(prev, formatLogLine('浏览器预览模式下不能启动高斯训练，请在 Tauri 桌面应用中运行。', 'error')))
      return false
    }
    setPipelineRunning(true)
    setLogs([])
    setPreview(null)
    clearMediaItems()
    startTimeRef.current = Date.now()
    setProgress({
      phase: 'train',
      stage: 'training.launch',
      percent: 0,
      message: '正在启动 LichtFeld Studio GUI',
      elapsed: 0,
      phasePercents: { extract: 0, align: 0, export: 0 },
      current: 0,
      total: config.iterations,
    })
    setLogs((prev) => appendLog(prev, formatLogLine('正在创建高斯训练任务', 'train', 0, 0)))
    try {
      const project = await invoke<XpanoProjectV2>('start_training_job', {
        projectRoot: trainingProjectRoot,
        expectedRevision,
        config,
      })
      const active = [...project.jobs].reverse().find((job) => (
        job.workspace === 'training' && ['queued', 'running', 'cancelling'].includes(job.state)
      ))
      activeJobIdRef.current = active?.jobId ?? null
      activeJobSequenceRef.current = active?.sequence ?? 0
      return true
    } catch (error) {
      const message = commandErrorMessage(error)
      setPipelineRunning(false)
      setProgress((prev) => ({ ...prev, phase: 'error', message }))
      setLogs((prev) => appendLog(prev, formatLogLine(`启动失败：${message}`, 'error')))
      return false
    }
  }, [clearMediaItems, setPipelineRunning])

  const cancel = useCallback(async () => {
    if (!isTauriRuntime() || !runningRef.current) return
    const realElapsed = Math.floor((Date.now() - startTimeRef.current) / 1000)
    setProgress((prev) => ({ ...prev, message: '正在取消任务', elapsed: prev.elapsed || realElapsed }))
    setLogs((prev) => appendLog(prev, formatLogLine('正在取消任务', undefined, realElapsed)))
    try {
      if (projectRoot && activeJobIdRef.current) {
        const snapshot = await invoke<JobSnapshot>('cancel_job', {
          projectRoot,
          jobId: activeJobIdRef.current,
        })
        activeJobSequenceRef.current = snapshot.sequence
      } else {
        await invoke('cancel_pipeline')
      }
    } catch (error) {
      setProgress((prev) => ({ ...prev, phase: 'error', message: `停止失败：${error}` }))
      setLogs((prev) => appendLog(prev, formatLogLine(`停止失败：${error}`, 'error')))
    }
  }, [projectRoot])

  const reset = useCallback(() => {
    setProgress(initialProgress)
    setLogs([])
    setPreview(null)
    clearMediaItems()
    lastExtractProgress = ''
    lastLoggedExtractBucket = -1
  }, [clearMediaItems])

  return { progress, running, logs, preview, mediaItems, start, startMedia, startTraining, cancel, reset }
}
