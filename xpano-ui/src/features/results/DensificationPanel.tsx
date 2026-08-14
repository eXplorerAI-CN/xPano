import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CheckCircle2, ChevronDown, Layers, RefreshCw, RotateCcw, ScanSearch, Terminal, WandSparkles, Wrench, X, XCircle } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { PointCloudData } from '../../lib/types'

export type ResultNotice = { text: string; tone: 'pending' | 'success' | 'error' }

type DensifyMode = 'turbo' | 'fast' | 'base' | 'high' | 'precise'
type DensifyPhase = 'idle' | 'checking' | 'installing' | 'running' | 'refreshing'
type DensifyImageFilter = 'front_plus_hd' | 'front' | 'hd' | 'cube_all' | 'all'

interface DensifyEnvStatus {
  pluginOk: boolean
  pythonOk: boolean
  depsOk: boolean
  runnerOk: boolean
  message: string
}

interface DensifyRunResult {
  originalPoints: number
  densePoints: number
  mergedPoints: number
  outputPointsPath: string
}

interface DensifyPersistedState {
  status: 'running' | 'completed_unconfirmed' | 'applied' | 'discarded' | 'failed' | 'stopped' | string
  message: string
  result?: DensifyRunResult | null
  logPath?: string
}

interface DensifyTaskEvent {
  task: 'install' | 'run'
  kind: 'start' | 'stdout' | 'stderr' | 'progress' | 'done' | 'error' | 'stopped'
  message: string
  progress?: number | null
}

type DensifyLogEntry = { text: string; kind: DensifyTaskEvent['kind'] }

interface DensificationPanelProps {
  open: boolean
  dataPath: string
  isDark: boolean
  cloudSwitching: boolean
  resetToken: number
  onClose: () => void
  onNotice: (notice: ResultNotice) => void
  loadPointCloudData: (pointsPath?: string | null, useCache?: boolean) => Promise<PointCloudData | null>
  clearPointCloudCache: (pointsPath: string) => void
  replacePointCloud: (data: PointCloudData) => Promise<void>
  finishCloudTransition: () => void
  cancelCloudTransition: () => void
}

const modes: Array<{ value: DensifyMode; label: string; hint: string }> = [
  { value: 'turbo', label: 'Turbo', hint: '最快，适合快速试跑' },
  { value: 'fast', label: 'Fast', hint: '推荐，速度和质量均衡' },
  { value: 'base', label: 'Base', hint: '更稳，耗时略长' },
  { value: 'high', label: 'High', hint: '更高质量，耗时更长' },
  { value: 'precise', label: 'Precise', hint: '最高精度，适合最终输出' },
]

const imageFilters: Array<{ value: DensifyImageFilter; label: string; hint: string }> = [
  { value: 'front_plus_hd', label: 'Front+补拍', hint: '全景只用 front 切图，同时加入高清补拍' },
  { value: 'front', label: '仅 Front', hint: '只使用全景相机 front 切图' },
  { value: 'hd', label: '仅补拍', hint: '只使用高清补拍图片' },
  { value: 'cube_all', label: '全景全部', hint: '使用全景相机所有 cube 切图' },
  { value: 'all', label: '全部', hint: '使用所有可用图片' },
]

const LOG_LIMIT = 5000
const LOG_TRIM_TO = 4500
const isTauriRuntime = () => typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
const compactErrorText = (error: unknown) => String(error).replace(/\r/g, '').trim().split('\n')[0]?.trim() || String(error)
const formatPointCount = (value: number) => value.toLocaleString()
const trimLogs = (logs: DensifyLogEntry[]) => logs.length > LOG_LIMIT ? logs.slice(-LOG_TRIM_TO) : logs

function logTone(entry: DensifyLogEntry) {
  const text = entry.text.toLowerCase()
  if (entry.kind === 'error' || entry.kind === 'stderr' || text.includes('error') || text.includes('failed')) return 'is-danger'
  if (entry.kind === 'done' || text.includes('done') || text.includes('finished')) return 'is-success'
  return ''
}

function restoredMessage(state: DensifyPersistedState) {
  if (state.status === 'failed') return `上次致密化失败：${compactErrorText(state.message)}`
  if (state.status === 'stopped') return '上次致密化任务已停止'
  if (state.status === 'completed_unconfirmed') return '发现未确认的致密化结果，可继续查看、应用或丢弃'
  return state.message
}

export function DensificationPanel({
  open,
  dataPath,
  isDark,
  cloudSwitching,
  resetToken,
  onClose,
  onNotice,
  loadPointCloudData,
  clearPointCloudCache,
  replacePointCloud,
  finishCloudTransition,
  cancelCloudTransition,
}: DensificationPanelProps) {
  const logRef = useRef<HTMLDivElement>(null)
  const userScrolledUpRef = useRef(false)
  const cancelRef = useRef(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [env, setEnv] = useState<DensifyEnvStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [running, setRunning] = useState(false)
  const [applying, setApplying] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [useCuda, setUseCuda] = useState(true)
  const [mode, setMode] = useState<DensifyMode>('fast')
  const [imageFilter, setImageFilter] = useState<DensifyImageFilter>('front_plus_hd')
  const [matchesPerRef, setMatchesPerRef] = useState(10000)
  const [steps, setSteps] = useState(50)
  const [referenceFraction, setReferenceFraction] = useState(0.75)
  const [neighborsPerRef, setNeighborsPerRef] = useState(3)
  const [minCertainty, setMinCertainty] = useState(0.2)
  const [phase, setPhase] = useState<DensifyPhase>('idle')
  const [lastResult, setLastResult] = useState<DensifyRunResult | null>(null)
  const [previewPointsPath, setPreviewPointsPath] = useState<string | null>(null)
  const [previewActive, setPreviewActive] = useState(false)
  const [logs, setLogs] = useState<DensifyLogEntry[]>([])
  const [progress, setProgress] = useState<number | null>(null)

  const ready = Boolean(env?.pluginOk && env?.pythonOk && env?.depsOk && env?.runnerOk)
  const busy = checking || installing || running
  const taskActive = installing || running
  const activeMode = modes.find((item) => item.value === mode) ?? modes[1]
  const activeImageFilter = imageFilters.find((item) => item.value === imageFilter) ?? imageFilters[0]
  const phaseText: Record<DensifyPhase, string> = {
    idle: ready ? '环境就绪' : env ? '需要配置' : '未检查',
    checking: '正在检查',
    installing: '正在配置',
    running: '正在致密化',
    refreshing: '正在刷新',
  }

  const checkEnvironment = useCallback(async (force = false) => {
    setChecking(true)
    setPhase('checking')
    try {
      const status = await invoke<DensifyEnvStatus>('check_lfs_densify_env', { pythonExe: '', force })
      setEnv(status)
      onNotice({ text: status.pluginOk && status.pythonOk && status.depsOk && status.runnerOk ? '致密化环境可用' : status.message, tone: status.pluginOk && status.pythonOk && status.depsOk && status.runnerOk ? 'success' : 'error' })
      return status
    } catch (error) {
      onNotice({ text: `环境检查失败：${compactErrorText(error)}`, tone: 'error' })
      return null
    } finally {
      setChecking(false)
      setPhase('idle')
    }
  }, [onNotice])

  useEffect(() => {
    if (open && !env && !checking && isTauriRuntime()) void checkEnvironment(false)
  }, [checking, checkEnvironment, env, open])

  useEffect(() => {
    setLastResult(null)
    setPreviewPointsPath(null)
    setPreviewActive(false)
    setLogs([])
    setProgress(null)
    onClose()
    if (!dataPath || !isTauriRuntime()) return
    let cancelled = false
    ;(async () => {
      const state = await invoke<DensifyPersistedState | null>('get_lfs_densify_state', { outputDir: dataPath }).catch(() => null)
      if (cancelled) return
      if (state?.logPath) {
        const tail = await invoke<string[]>('read_lfs_densify_log_tail', { outputDir: dataPath, logPath: state.logPath, maxLines: 180 }).catch(() => [])
        if (!cancelled && tail.length) setLogs(tail.map((text) => ({ text, kind: text.toLowerCase().includes('error') ? 'stderr' : 'stdout' })))
      }
      if (!cancelled && state && ['failed', 'stopped', 'completed_unconfirmed'].includes(state.status)) {
        onNotice({ text: restoredMessage(state), tone: state.status === 'failed' ? 'error' : 'success' })
      }
      const result = await invoke<DensifyRunResult | null>('get_lfs_densify_pending_result', { outputDir: dataPath }).catch(() => null)
      if (cancelled || !result?.outputPointsPath) return
      setLastResult(result)
      setPreviewPointsPath(result.outputPointsPath)
      onNotice({ text: `发现未确认的致密化结果：新增 ${formatPointCount(result.densePoints)} 点，可继续查看或丢弃`, tone: 'success' })
    })().catch(() => {})
    return () => { cancelled = true }
  }, [dataPath, onClose, onNotice, resetToken])

  useEffect(() => {
    if (!isTauriRuntime()) return
    let cleanup: (() => void) | undefined
    let cancelled = false
    listen<DensifyTaskEvent>('densify:task', (event) => {
      const payload = event.payload
      if (!payload) return
      if (payload.kind === 'start') {
        setProgress(0)
        userScrolledUpRef.current = false
        setLogs([{ text: payload.task === 'install' ? '开始配置致密化环境' : '开始运行 LichtFeld 致密化', kind: payload.kind }])
        return
      }
      if (payload.kind === 'progress') {
        const value = Number(payload.progress)
        if (Number.isFinite(value)) setProgress((current) => Math.max(current ?? 0, Math.min(100, Math.max(0, value))))
        if (payload.message && payload.message !== String(payload.progress ?? '')) {
          setLogs((lines) => lines[lines.length - 1]?.text === payload.message ? lines : trimLogs([...lines, { text: payload.message, kind: payload.kind }]))
        }
        return
      }
      if (payload.kind === 'done') setProgress(100)
      if (payload.kind === 'stopped') setProgress(null)
      if (payload.message) setLogs((lines) => trimLogs([...lines, { text: payload.message, kind: payload.kind }]))
    }).then((unlisten) => {
      if (cancelled) unlisten()
      else cleanup = unlisten
    })
    return () => {
      cancelled = true
      cleanup?.()
    }
  }, [])

  useLayoutEffect(() => {
    const element = logRef.current
    if (element && !userScrolledUpRef.current) element.scrollTop = element.scrollHeight
  }, [logs])

  const installEnvironment = async () => {
    setInstalling(true)
    setStopping(false)
    cancelRef.current = false
    userScrolledUpRef.current = false
    setLogs([])
    setProgress(0)
    setPhase('installing')
    try {
      await invoke<string>('install_lfs_densify_env', { useCuda })
      const status = await invoke<DensifyEnvStatus>('check_lfs_densify_env', { pythonExe: '', force: true })
      setEnv(status)
      onNotice({ text: status.pluginOk && status.pythonOk && status.depsOk && status.runnerOk ? '致密化环境配置完成' : status.message, tone: status.pluginOk && status.pythonOk && status.depsOk && status.runnerOk ? 'success' : 'error' })
    } catch (error) {
      onNotice({ text: cancelRef.current ? '已停止环境配置' : `环境配置失败：${compactErrorText(error)}`, tone: cancelRef.current ? 'success' : 'error' })
    } finally {
      setInstalling(false)
      setStopping(false)
      cancelRef.current = false
      setPhase('idle')
    }
  }

  const run = async () => {
    if (!dataPath || running || cloudSwitching || previewPointsPath) return
    setRunning(true)
    setStopping(false)
    cancelRef.current = false
    userScrolledUpRef.current = false
    setLogs([])
    setProgress(0)
    setPhase('running')
    setLastResult(null)
    onNotice({ text: '正在运行 LichtFeld 致密化...', tone: 'pending' })
    try {
      const result = await invoke<DensifyRunResult>('run_lfs_densify', {
        outputDir: dataPath,
        roma: mode,
        maxPoints: 0,
        numRefs: referenceFraction,
        nnsPerRef: neighborsPerRef,
        matchesPerRef,
        steps,
        certaintyThresh: minCertainty,
        imageFilter,
        roiStart: 0,
        roiEnd: 1,
      })
      setPhase('refreshing')
      onNotice({ text: '正在后台加载致密化点云...', tone: 'pending' })
      clearPointCloudCache(result.outputPointsPath)
      const denseData = await loadPointCloudData(result.outputPointsPath, false)
      if (!denseData) throw new Error('致密化点云读取失败')
      await replacePointCloud(denseData)
      setPreviewPointsPath(result.outputPointsPath)
      setPreviewActive(true)
      setLastResult(result)
      onNotice({ text: `致密化预览已生成：新增 ${formatPointCount(result.densePoints)} 点，总计 ${formatPointCount(result.mergedPoints)} 点`, tone: 'success' })
    } catch (error) {
      cancelCloudTransition()
      onNotice({ text: cancelRef.current ? '已停止致密化任务' : `致密化失败：${compactErrorText(error)}`, tone: cancelRef.current ? 'success' : 'error' })
    } finally {
      setRunning(false)
      setStopping(false)
      cancelRef.current = false
      setPhase('idle')
    }
  }

  const showBase = async () => {
    if (!previewActive || cloudSwitching) return
    const data = await loadPointCloudData(null)
    if (!data) return
    await replacePointCloud(data)
    setPreviewActive(false)
    finishCloudTransition()
    onNotice({ text: '已回退到原始点云预览', tone: 'success' })
  }

  const showPreview = async () => {
    if (!previewPointsPath || previewActive || cloudSwitching) return
    clearPointCloudCache(previewPointsPath)
    const data = await loadPointCloudData(previewPointsPath, false)
    if (!data) return
    await replacePointCloud(data)
    setPreviewActive(true)
    finishCloudTransition()
    onNotice({ text: '已切换到致密化预览', tone: 'success' })
  }

  const saveVersion = async () => {
    if (!dataPath || !previewPointsPath || applying || cloudSwitching) return
    setApplying(true)
    try {
      await invoke('apply_lfs_densify_result', { outputDir: dataPath, densePointsPath: previewPointsPath })
      const data = await loadPointCloudData(null, false)
      if (!data) throw new Error('活动训练点云读取失败')
      await replacePointCloud(data)
      setPreviewActive(false)
      setPreviewPointsPath(null)
      setLastResult(null)
      finishCloudTransition()
      onNotice({ text: '致密化结果已保存为永久版本，当前训练点云未改变', tone: 'success' })
    } catch (error) {
      cancelCloudTransition()
      onNotice({ text: `应用致密化结果失败：${compactErrorText(error)}`, tone: 'error' })
    } finally {
      setApplying(false)
    }
  }

  const closePreview = async () => {
    if (!dataPath || !previewPointsPath || applying || cloudSwitching) return
    setApplying(true)
    try {
      const data = previewActive ? await loadPointCloudData(null) : null
      await invoke('discard_lfs_densify_result', { outputDir: dataPath, densePointsPath: previewPointsPath })
      clearPointCloudCache(previewPointsPath)
      if (previewActive && data) {
        await replacePointCloud(data)
        setPreviewActive(false)
      }
      setPreviewPointsPath(null)
      setLastResult(null)
      finishCloudTransition()
      onNotice({ text: '已关闭致密化预览，候选结果仍保留，可稍后恢复', tone: 'success' })
    } catch (error) {
      cancelCloudTransition()
      onNotice({ text: `丢弃致密化结果失败：${compactErrorText(error)}`, tone: 'error' })
    } finally {
      setApplying(false)
    }
  }

  const stop = async () => {
    if (!busy || stopping) return
    cancelRef.current = true
    setStopping(true)
    try {
      const stopped = await invoke<boolean>('stop_lfs_densify_task')
      if (!stopped) setStopping(false)
    } catch (error) {
      onNotice({ text: `停止失败：${compactErrorText(error)}`, tone: 'error' })
      setStopping(false)
    }
  }

  if (!open) return null

  return (
    <div className="liquid-card-clear absolute bottom-[4.25rem] left-4 z-10 max-h-[calc(100%-5.5rem)] w-[min(390px,calc(100%-2rem))] overflow-y-auto overscroll-contain rounded-card p-3 text-[12px] animate-in fade-in slide-in-from-bottom-2 duration-200">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-subtle ${isDark ? 'bg-white/[0.06]' : 'bg-ink/[0.05]'}`}><WandSparkles className="h-4 w-4 text-brand" /></span>
          <div className="min-w-0"><p className="truncate text-[13px] font-semibold text-ink">LichtFeld 致密化</p><p className="truncate text-[10px] text-muted">当前点云补密并刷新预览</p></div>
        </div>
        <button type="button" onClick={onClose} className={`motion-press grid h-7 w-7 shrink-0 place-items-center rounded-subtle transition-colors ${isDark ? 'text-white/45 hover:bg-white/[0.06] hover:text-white/72' : 'text-ink/45 hover:bg-ink/[0.06] hover:text-ink/72'}`}><X className="h-3.5 w-3.5" /></button>
      </div>

      <div className="glass-inset mt-2 flex min-h-10 shrink-0 items-center justify-between gap-3 rounded-comfortable px-2.5 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {busy ? <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" /> : ready ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-brand" /> : <Wrench className="h-3.5 w-3.5 shrink-0 text-muted" />}
          <span className={`truncate text-[11px] font-semibold ${ready ? 'text-brand' : 'text-ink/62'}`}>{phaseText[phase]}</span>
        </div>
        <button type="button" disabled={busy} onClick={() => void checkEnvironment(true)} className={`motion-press shrink-0 rounded-subtle px-2 py-1 text-[10px] font-semibold transition-colors disabled:opacity-45 ${isDark ? 'text-white/42 hover:bg-white/[0.06] hover:text-white/68' : 'text-ink/42 hover:bg-ink/[0.06] hover:text-ink/68'}`}>检查</button>
      </div>

      <div className="mt-2">
        <span className="ui-label mb-1 block text-[10px]">RoMa</span>
        <div className="theme-segment grid grid-cols-5 overflow-hidden rounded-comfortable p-0.5">
          {modes.map((item) => <button key={item.value} type="button" disabled={busy} onClick={() => setMode(item.value)} title={item.hint} className={`motion-press h-7 min-w-0 rounded-subtle border px-1 text-[10px] font-semibold transition-colors disabled:opacity-45 ${mode === item.value ? 'border border-brand/40 bg-brand text-white shadow-[0_0_14px_rgba(var(--xp-brand-rgb),0.22)]' : 'border-transparent text-ink/42 hover:bg-ink/[0.04] hover:text-ink/72'}`}><span className="block truncate">{item.label}</span></button>)}
        </div>
        <span className="mt-1 block truncate text-[10px] text-muted">{activeMode.hint}</span>
      </div>

      <div className="mt-3">
        <span className="ui-label mb-1 block text-[10px]">图片范围</span>
        <div className="theme-segment grid grid-cols-5 overflow-hidden rounded-comfortable p-0.5">
          {imageFilters.map((item) => <button key={item.value} type="button" disabled={busy} onClick={() => setImageFilter(item.value)} title={item.hint} className={`motion-press h-7 min-w-0 rounded-subtle border px-1 text-[10px] font-semibold transition-colors disabled:opacity-45 ${imageFilter === item.value ? 'border border-brand/40 bg-brand text-white shadow-[0_0_14px_rgba(var(--xp-brand-rgb),0.22)]' : 'border-transparent text-ink/42 hover:bg-ink/[0.04] hover:text-ink/72'}`}><span className="block truncate">{item.label}</span></button>)}
        </div>
        <span className="mt-1 block truncate text-[10px] text-muted">{activeImageFilter.hint}</span>
      </div>

      <div className="mt-3">
        <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="motion-press flex w-full items-center justify-between gap-3 rounded-subtle border border-[var(--xp-line)] px-3 py-2 text-[12px] text-ink/65 transition-colors hover:bg-[var(--xp-surface-soft)]">
          <span className="ui-label text-[10px]">高级参数</span>
          <span className="min-w-0 flex-1 truncate text-right font-mono text-[10px] text-muted">{matchesPerRef.toLocaleString()} / {neighborsPerRef} / {steps}步 / {referenceFraction.toFixed(2)} / {minCertainty.toFixed(2)} / {useCuda ? 'CUDA' : 'CPU'}</span>
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
        </button>
        <div className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${advancedOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}>
          <div className="min-h-0 overflow-hidden">
            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className="block"><span className="ui-label mb-1 block text-[10px]">匹配点 / Ref</span><input className="theme-input h-9 w-full rounded-comfortable border px-3 py-2 font-mono text-[12px]" disabled={busy} value={matchesPerRef} onChange={(event) => setMatchesPerRef(Math.max(100, Number(event.target.value.replace(/[^\d]/g, '')) || 100))} /></label>
              <label className="block"><span className="ui-label mb-1 block text-[10px]">邻居 / Ref</span><input className="theme-input h-9 w-full rounded-comfortable border px-3 py-2 font-mono text-[12px]" disabled={busy} value={neighborsPerRef} onChange={(event) => setNeighborsPerRef(Math.max(1, Number(event.target.value.replace(/[^\d]/g, '')) || 1))} /></label>
              <label className="block"><span className="ui-label mb-1 block text-[10px]">步数</span><input className="theme-input h-9 w-full rounded-comfortable border px-3 py-2 font-mono text-[12px]" disabled={busy} value={steps} onChange={(event) => setSteps(Math.min(500, Math.max(1, Number(event.target.value.replace(/[^\d]/g, '')) || 1)))} /></label>
              <label className="block"><span className="ui-label mb-1 block text-[10px]">参考比例</span><input type="range" min={0.1} max={1} step={0.05} value={referenceFraction} disabled={busy} onChange={(event) => setReferenceFraction(Number(event.target.value))} className="h-9 w-full accent-[var(--xp-brand)]" /><span className="block text-center font-mono text-[10px] text-muted">{referenceFraction.toFixed(2)}</span></label>
              <label className="block"><span className="ui-label mb-1 block text-[10px]">最小置信度</span><input type="range" min={0} max={1} step={0.05} value={minCertainty} disabled={busy} onChange={(event) => setMinCertainty(Number(event.target.value))} className="h-9 w-full accent-[var(--xp-brand)]" /><span className="block text-center font-mono text-[10px] text-muted">{minCertainty.toFixed(2)}</span></label>
            </div>
            <button type="button" role="switch" aria-checked={useCuda} disabled={busy} onClick={() => setUseCuda((value) => !value)} className="motion-press mt-2 flex w-full items-center justify-between gap-3 rounded-subtle border border-[var(--xp-line)] px-3 py-2 text-[12px] text-ink/65 transition-colors hover:bg-[var(--xp-surface-soft)] disabled:opacity-45">
              <span className="text-[11px] text-muted">一键配置使用 CUDA</span>
              <span className="relative h-4 w-8 rounded-full transition-colors" style={{ background: useCuda ? 'var(--xp-brand)' : 'var(--xp-line-strong)' }}><span className="absolute top-0.5 h-3 w-3 rounded-full bg-[var(--xp-surface)] shadow-sm transition-all" style={{ left: useCuda ? 18 : 2 }} /></span>
            </button>
          </div>
        </div>
      </div>

      {lastResult && <div className="glass-inset mt-3 grid grid-cols-3 gap-1.5 rounded-comfortable p-1.5">{[['原始', lastResult.originalPoints], ['新增', lastResult.densePoints], ['总计', lastResult.mergedPoints]].map(([label, value]) => <div key={String(label)} className="min-w-0 px-1 py-1 text-center"><p className="truncate text-[10px] text-muted">{label}</p><p className="truncate font-mono text-[11px] font-semibold">{formatPointCount(Number(value))}</p></div>)}</div>}

      {previewPointsPath && <div className="mt-2 grid grid-cols-2 gap-2">
        <button type="button" disabled={busy || applying || cloudSwitching} onClick={previewActive ? showBase : showPreview} className="glass-control motion-press inline-flex h-8 items-center justify-center gap-1.5 rounded-subtle px-2 text-[11px] font-semibold disabled:opacity-45">{previewActive ? <RotateCcw className="h-3.5 w-3.5" /> : <Layers className="h-3.5 w-3.5" />}{previewActive ? '回退预览' : '查看致密化'}</button>
        <button type="button" disabled={busy || applying || cloudSwitching} onClick={saveVersion} className="theme-action-shadow motion-press inline-flex h-8 items-center justify-center gap-1.5 rounded-subtle bg-brand px-2 text-[11px] font-semibold text-white disabled:opacity-45">{applying ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}保存为版本</button>
        <button type="button" disabled={busy || applying || cloudSwitching} onClick={closePreview} className="glass-control motion-press col-span-2 inline-flex h-8 items-center justify-center gap-1.5 rounded-subtle px-2 text-[11px] font-semibold text-muted disabled:opacity-45">{applying ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <XCircle className="h-3.5 w-3.5" />}关闭预览（保留候选）</button>
      </div>}

      {env?.message && !ready && <p className="mt-2 max-h-16 overflow-y-auto break-words rounded-subtle bg-danger/8 px-2 py-1.5 text-[10px] leading-4 text-danger">{env.message}</p>}

      {(taskActive || logs.length > 0) && <div className="mt-3 shrink-0">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="ui-label flex items-center gap-1.5 text-[10px]"><Terminal className="h-3.5 w-3.5" />任务日志</span>
          <span className="font-mono text-[10px] text-muted">{progress === null ? (taskActive ? '运行中' : '待命') : `${Math.round(progress)}%`}</span>
          {taskActive && <button type="button" disabled={stopping} onClick={stop} className="motion-press rounded-subtle border border-danger/30 bg-danger/10 px-2 py-1 text-[10px] font-semibold text-danger disabled:opacity-45">{stopping ? '停止中' : '停止'}</button>}
        </div>
        <div className="mb-1.5 h-1.5 overflow-hidden rounded-full bg-ink/10"><div className="h-full rounded-full bg-brand transition-all duration-300" style={{ width: `${Math.min(100, Math.max(0, progress ?? 0))}%` }} /></div>
        <div ref={logRef} onScroll={() => { const element = logRef.current; if (element) userScrolledUpRef.current = element.scrollHeight - element.scrollTop - element.clientHeight >= 8 }} className="terminal h-28 max-h-28 overflow-y-auto overflow-x-hidden select-text px-1 py-1">
          {logs.length > 0 ? logs.map((line, index) => <p key={`${index}-${line.text.slice(0, 12)}`} className={`log-line min-w-0 max-w-full ${logTone(line)}`}><span className="log-index">{String(index + 1).padStart(2, '0')}</span><span className="log-text">{line.text}</span></p>) : <p className="px-1 py-1 text-[11px] leading-5 text-muted">等待任务输出<span className="terminal-cursor" /></p>}
        </div>
      </div>}

      <div className="mt-3 grid shrink-0 grid-cols-[1fr_1.35fr] gap-2">
        <button type="button" disabled={busy} onClick={installEnvironment} className="glass-control motion-press inline-flex h-8 items-center justify-center gap-1.5 rounded-subtle px-2 text-[11px] font-semibold disabled:opacity-45">{installing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}一键配置</button>
        <button type="button" disabled={busy || !ready || cloudSwitching || Boolean(previewPointsPath)} onClick={run} className="theme-action-shadow motion-press inline-flex h-8 items-center justify-center gap-1.5 rounded-subtle bg-brand px-2 text-[11px] font-semibold text-white disabled:opacity-45">{running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ScanSearch className="h-3.5 w-3.5" />}运行致密化</button>
      </div>
    </div>
  )
}
