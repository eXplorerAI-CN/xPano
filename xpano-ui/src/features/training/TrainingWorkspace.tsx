import { useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { FolderOpen } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useJob } from '../../app/useJob'
import { useProject } from '../../app/useProject'
import { useBatch } from '../../app/useBatch'
import { useBatchTaskLock } from '../batch/useBatchTaskLock'
import { joinDisplayPath } from '../../lib/paths'
import { TrainingSetupView } from './TrainingSetupView'
import { TrainingTaskView } from './TrainingTaskView'
import {
  applyTrainingPreset,
  DEFAULT_TRAINING_CONFIG,
  deriveTrainingPreset,
  trainingDisplayPercent,
  trainingStartBlocker,
  trainingWorkspaceMode,
  type TrainingConfig,
  type TrainingPreset,
  type TrainingReadiness,
  type TrainingRecoveryAction,
} from './trainingConfig'

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function TrainingWorkspace() {
  const { queue: batchQueue } = useBatch()
  const batchActive = batchQueue.state === 'running' || batchQueue.state === 'stopping'
  const { locked: taskInputLocked, reason: taskInputLockReason } = useBatchTaskLock()
  const navigate = useNavigate()
  const { project, projectRoot } = useProject()
  const { progress, running, logs, startTraining, cancel } = useJob()
  const [config, setConfig] = useState<TrainingConfig>(DEFAULT_TRAINING_CONFIG)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [configureNext, setConfigureNext] = useState(false)
  const [readiness, setReadiness] = useState<TrainingReadiness>({ runtimeAvailable: false, datasetAvailable: false, geometryAvailable: false, outputAvailable: false })
  const [checking, setChecking] = useState(true)
  const [readinessRevision, setReadinessRevision] = useState(0)

  useEffect(() => {
    const saved = project?.training.config as Partial<TrainingConfig> | undefined
    if (saved && Object.keys(saved).length > 0) setConfig({ ...DEFAULT_TRAINING_CONFIG, ...saved, gui: true })
  }, [project?.training.config])

  useEffect(() => {
    if (!projectRoot || !isTauriRuntime()) {
      const available = Boolean(project)
      setReadiness({ runtimeAvailable: available, datasetAvailable: available, geometryAvailable: available, outputAvailable: available, cudaAvailable: available, vulkanAvailable: available })
      setChecking(false)
      return
    }
    let disposed = false
    setChecking(true)
    invoke<TrainingReadiness>('get_training_readiness', { projectRoot })
      .then((value) => { if (!disposed) setReadiness(value) })
      .catch(() => { if (!disposed) setReadiness({ runtimeAvailable: false, datasetAvailable: false, geometryAvailable: false, outputAvailable: false }) })
      .finally(() => { if (!disposed) setChecking(false) })
    return () => { disposed = true }
  }, [project, projectRoot, readinessRevision])

  const trainingRunning = running && progress.phase === 'train'
  const trainingStatus = project?.training.status ?? 'idle'
  const persistedMode = trainingWorkspaceMode(trainingStatus, trainingRunning)
  const mode = configureNext && !trainingRunning ? 'setup' : persistedMode
  const preset = deriveTrainingPreset(config)
  const blocker = batchActive
    ? { reason: '批量队列正在运行，请先停止队列', action: null }
    : taskInputLocked
      ? { reason: taskInputLockReason, action: null }
    : trainingStartBlocker(Boolean(project && projectRoot), readiness, running)
  const percent = trainingDisplayPercent(trainingStatus, project?.training.lastIteration ?? 0, project?.training.totalIterations ?? 0, trainingRunning, progress.percent)
  const iteration = progress.current ?? project?.training.lastIteration ?? 0
  const total = progress.total ?? project?.training.totalIterations ?? config.iterations
  const loss = progress.loss ?? project?.training.lastLoss ?? undefined
  const splats = progress.splatCount ?? project?.training.splatCount ?? 0
  const submittedConfig = useMemo(() => ({
    ...DEFAULT_TRAINING_CONFIG,
    ...(project?.training.config as Partial<TrainingConfig> | undefined),
    gui: true,
  }), [project?.training.config])

  useEffect(() => {
    if (trainingRunning) setConfigureNext(false)
  }, [trainingRunning])

  const selectPreset = (next: TrainingPreset) => {
    setConfig((current) => applyTrainingPreset(current, next))
  }

  const update = <Key extends keyof TrainingConfig>(key: Key, value: TrainingConfig[Key]) => {
    setConfig((current) => ({ ...current, [key]: value }))
  }

  const begin = async () => {
    if (!project || !projectRoot || blocker) return
    const started = await startTraining(projectRoot, project.revision, { ...config, gui: true })
    if (started) setConfigureNext(false)
  }

  const recover = (action: TrainingRecoveryAction) => {
    if (action === 'recheck') {
      setReadinessRevision((value) => value + 1)
      return
    }
    navigate(`/project/${action}`)
  }

  const openOutput = async () => {
    if (!projectRoot || !project?.training.outputPath) return
    await invoke('open_output_folder', { path: joinDisplayPath(projectRoot, project.training.outputPath) })
  }

  if (!project) {
    return (
      <section className="liquid-panel flex h-full min-h-0 flex-col items-center justify-center p-8 text-center">
        <span className="grid h-12 w-12 place-items-center rounded-full bg-brand/10 text-brand"><FolderOpen className="h-5 w-5" /></span>
        <h1 className="mt-4 text-[18px] font-semibold text-ink">尚无可训练工程</h1>
        <p className="mt-2 max-w-md text-[12px] leading-6 text-muted">先导入素材并完成训练数据准备，再开始高斯训练。</p>
        <button type="button" onClick={() => navigate('/project/media')} className="motion-press mt-5 flex h-10 items-center gap-2 rounded-comfortable bg-brand px-5 text-[12px] font-semibold text-white"><FolderOpen className="h-4 w-4" />前往素材与处理</button>
      </section>
    )
  }

  return (
    <div className="h-full min-h-0 p-3">
      {mode === 'setup' ? (
        <TrainingSetupView
          config={config}
          preset={preset}
          readiness={readiness}
          checking={checking}
          advancedOpen={advancedOpen}
          blocker={blocker}
          inputsDisabled={taskInputLocked}
          onAdvancedOpenChange={setAdvancedOpen}
          onSelectPreset={selectPreset}
          onChange={update}
          onStart={begin}
          onRecover={recover}
        />
      ) : (
        <TrainingTaskView
          mode={mode}
          training={project.training}
          config={submittedConfig}
          progress={progress}
          percent={percent}
          iteration={iteration}
          total={total}
          loss={loss}
          splats={splats}
          logs={logs}
          onCancel={cancel}
          onConfigure={() => setConfigureNext(true)}
          onOpenOutput={openOutput}
        />
      )}
    </div>
  )
}
