import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Images } from 'lucide-react'
import { useProject } from '../../app/useProject'
import { useBatch } from '../../app/useBatch'
import { useBatchTaskLock } from '../batch/useBatchTaskLock'
import { useJob } from '../../app/useJob'
import { ToastContainer } from '../../components/shared/Toast'
import { useToast } from '../../hooks/useToast'
import type { ExecutionPlan, ProjectTrack } from '../../lib/contracts'
import type { MaterialTrack, PipelineConfig } from '../../lib/types'
import { evaluateMediaReadiness } from '../../lib/mediaReadiness'
import { joinDisplayPath, normalizeDisplayPath } from '../../lib/paths'
import { commandErrorMessage } from '../../lib/commandError'
import { BackendSettings } from './BackendSettings'
import { ComponentSelectionDialog } from './ComponentSelectionDialog'
import { ExecutionGraph } from './ExecutionGraph'
import { ReconstructionMonitor } from './ReconstructionMonitor'
import { ReconstructionSetupDialog } from './ReconstructionSetupDialog'
import { shouldAutoOpenResults } from './reconstructionCompletion'
import { psxReexportAvailability } from './reconstructionReexport'
import { prepareComponentSelection, type ComponentInspection, type ComponentSelectionDecision } from './reconstructionComponents'
import { configFromProject, defaultReconstructionConfig, normalizeExecutablePath, persistedReconstructionConfig, type BackendProbe, type ReconstructionConfigDraft } from './reconstructionTypes'

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

function projectTracksToPipeline(tracks: ProjectTrack[]): MaterialTrack[] {
  return tracks.map((track) => ({
    id: track.id,
    type: track.type,
    label: track.label,
    path: track.sourcePath,
    trim: track.trim ?? undefined,
    extract: track.extraction,
    cameraProfile: track.cameraProfile ?? undefined,
    restoredFrameCount: track.type === 'panoramic_video' ? track.items.length : undefined,
    restoredPhotoCount: track.type !== 'panoramic_video' ? track.items.length : undefined,
  }))
}

interface AlignmentComponentSummary {
  componentKey: string
  alignedCameraCount: number
  totalCameraCount: number
}

interface AlignmentReportSummary {
  alignedCameras?: number
  totalCameras?: number
  alignmentRate?: number
  components?: AlignmentComponentSummary[]
  selectedComponentKey?: string | null
  warnings?: string[]
}

function pipelineConfig(config: ReconstructionConfigDraft, projectRoot: string, probes: BackendProbe[], componentKey?: string): PipelineConfig {
  return {
    outputDir: projectRoot,
    metashapePath: normalizeExecutablePath(config.metashapePath) || probes.find((probe) => probe.backend === 'metashape')?.path || 'metashape.exe',
    colmapPath: probes.find((probe) => probe.backend === 'colmap')?.path || 'colmap',
    framesPerSecond: 1,
    frameLimit: 0,
    alignmentEngine: config.backend,
    metaAlignmentMode: config.alignmentMode,
    metaKeypointLimit: config.metashapeKeypointLimit,
    metaTiepointLimit: config.metashapeTiepointLimit,
    metaComponentKey: componentKey,
    upAxis: config.upAxis,
    colmapDensityPreset: config.colmapDensityPreset,
    colmapUseGpu: config.colmapUseGpu,
    colmapMatcher: config.colmapMatcher,
    colmapMaxImageSize: config.colmapMaxImageSize,
    colmapMaxNumFeatures: config.colmapMaxNumFeatures,
  }
}

function devPreviewPlan(projectId: string, inputRevision: number): ExecutionPlan {
  const stages: Array<[string, string, 'counted' | 'indeterminate', boolean]> = [
    ['input.validate', '校验输入', 'counted', false],
    ['metashape.project.create', '创建 Metashape 工程', 'indeterminate', false],
    ['metashape.pano.import', '导入全景双鱼眼与站点', 'counted', false],
    ['metashape.pano.station', '设置全景站点', 'counted', false],
    ['metashape.pano.match', '匹配全景素材', 'indeterminate', true],
    ['metashape.pano.align', '求解全景骨架', 'indeterminate', true],
    ['metashape.pano.release', '释放全景站点以优化外参', 'counted', false],
    ['metashape.pano.optimize', '优化全景骨架', 'indeterminate', true],
    ['metashape.frame.import', '导入普通帧与照片', 'counted', false],
    ['metashape.frame.match', '匹配新增普通素材', 'indeterminate', true],
    ['metashape.frame.align', '增量接入普通相机', 'indeterminate', true],
    ['metashape.all.optimize', '全局相机优化', 'indeterminate', true],
    ['metashape.project.save', '保存 Metashape 工程', 'indeterminate', false],
    ['metashape.component.select', '检查并选择主 Component', 'counted', false],
    ['coordinate.auto_level', '自动校正地面方向', 'indeterminate', false],
    ['export.images', '导出训练图像', 'counted', false],
    ['export.colmap', '写出 COLMAP 模型', 'counted', false],
    ['output.validate', '验证输出完整性', 'counted', false],
  ]
  return {
    schemaVersion: 1,
    planId: 'dev-phase3-plan',
    projectId,
    inputRevision,
    backend: 'metashape',
    createdAt: '2026-07-10T08:20:00Z',
    nodes: stages.map(([stageId, label, progressMode, slowHint], index) => ({
      stageId,
      label,
      dependsOn: index ? [stages[index - 1][0]] : [],
      weight: 1 / stages.length,
      progressMode,
      slowHint,
      skipReason: null,
    })),
  }
}

export function ReconstructionWorkspace() {
  const { queue: batchQueue } = useBatch()
  const batchActive = batchQueue.state === 'running' || batchQueue.state === 'stopping'
  const { locked: taskInputLocked, reason: taskInputLockReason } = useBatchTaskLock()
  const navigate = useNavigate()
  const { project, projectRoot, saveReconstructionConfig } = useProject()
  const { running, progress, logs, start, cancel } = useJob()
  const { toast, toasts, removeToast } = useToast()
  const [config, setConfig] = useState<ReconstructionConfigDraft>(defaultReconstructionConfig)
  const [plan, setPlan] = useState<ExecutionPlan | null>(null)
  const [planError, setPlanError] = useState('')
  const [probes, setProbes] = useState<BackendProbe[]>([])
  const [showMonitor, setShowMonitor] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [inspectingComponents, setInspectingComponents] = useState(false)
  const [componentSelection, setComponentSelection] = useState<ComponentSelectionDecision | null>(null)
  const projectIdRef = useRef('')
  const startedReconstructionRef = useRef(false)

  const appliedConfig = useMemo(() => project ? configFromProject(project.reconstruction.backend, project.reconstruction.config) : defaultReconstructionConfig, [project])
  const dirty = project ? JSON.stringify(config) !== JSON.stringify(appliedConfig) : false
  const hasFlatMedia = project?.tracks.some((track) => track.type !== 'panoramic_video') ?? false
  const activeManifest = typeof project?.reconstruction.config.alignmentManifestPath === 'string' ? project.reconstruction.config.alignmentManifestPath : ''
  const readiness = useMemo(() => evaluateMediaReadiness(project?.tracks ?? [], activeManifest), [activeManifest, project])
  const selectedProbe = probes.find((probe) => probe.backend === config.backend)
  const effectiveMetashapePath = normalizeExecutablePath(config.metashapePath) || probes.find((probe) => probe.backend === 'metashape')?.path || ''
  const alignedProjectComplete = Boolean(project && project.reconstruction.status === 'complete' && project.reconstruction.inputRevision === project.revisions.alignmentInput)
  const currentPsx = Boolean(project && project.reconstruction.inputRevision === project.revisions.alignmentInput && project.reconstruction.projectPath)
  const alignmentReport = (project?.reconstruction.config.alignmentReport ?? null) as AlignmentReportSummary | null
  const alignmentComponents = Array.isArray(alignmentReport?.components) ? alignmentReport.components : []
  const exportedComponentKey = typeof alignmentReport?.selectedComponentKey === 'string'
    ? alignmentReport.selectedComponentKey
    : typeof project?.reconstruction.config.selectedComponentKey === 'string'
      ? project.reconstruction.config.selectedComponentKey
      : ''
  const projectComplete = alignedProjectComplete && !dirty
  const reexportAvailability = psxReexportAvailability({
    backend: project?.reconstruction.backend ?? config.backend,
    projectCurrent: currentPsx,
    projectPath: project?.reconstruction.projectPath ?? null,
    manifestPath: activeManifest || null,
    dirty,
    running,
    backendAvailable: selectedProbe?.available === true,
  })

  useEffect(() => {
    if (!project || projectIdRef.current === project.projectId) return
    projectIdRef.current = project.projectId
    const initial = configFromProject(project.reconstruction.backend, project.reconstruction.config)
    setConfig(initial)
    setComponentSelection(null)
    const previewWizard = import.meta.env.DEV && new URLSearchParams(window.location.search).get('wizard') === '1'
    setWizardOpen(previewWizard || typeof project.reconstruction.config.alignmentMode !== 'string')
  }, [project])

  useEffect(() => {
    if (!project || running) return
    if (progress.phase === 'error') {
      startedReconstructionRef.current = false
      return
    }
    if (!shouldAutoOpenResults(
      startedReconstructionRef.current,
      running,
      project.reconstruction.status,
      project.reconstruction.inputRevision,
      project.revisions.alignmentInput,
    )) return
    startedReconstructionRef.current = false
    navigate('/project/results', { replace: true })
  }, [navigate, progress.phase, project, running])

  useEffect(() => {
    if (!isTauriRuntime()) {
      if (import.meta.env.DEV && project) {
        setProbes([
          { backend: 'metashape', available: true, path: 'E:/FastProgram/Metashape/metashape.exe', cudaAvailable: null, detail: 'Preview fixture' },
          { backend: 'colmap', available: true, path: 'D:/xPano/tools/colmap/bin/colmap.exe', cudaAvailable: true, detail: 'Preview fixture' },
        ])
        setPlan(devPreviewPlan(project.projectId, project.revisions.alignmentInput))
      }
      return
    }
    invoke<BackendProbe[]>('probe_reconstruction_backends', {
      metashapePath: config.metashapePath || null,
    }).then(setProbes).catch((error) => {
      setPlanError(`后端检测失败：${commandErrorMessage(error)}`)
    })
  }, [config.metashapePath, project])

  useEffect(() => {
    if (!project || !projectRoot || running || !isTauriRuntime()) return
    if (!readiness.canContinue) {
      setPlan(null)
      setPlanError('')
      return
    }
    let disposed = false
    const timer = window.setTimeout(() => {
      setPlanError('')
      invoke<ExecutionPlan>('build_execution_plan', {
        projectRoot,
        expectedRevision: project.revision,
        config: { backend: config.backend, alignmentMode: config.alignmentMode, metashapePath: config.backend === 'metashape' ? effectiveMetashapePath || null : null },
      }).then((next) => {
        if (!disposed) setPlan(next)
      }).catch((error) => {
        if (!disposed) {
          setPlan(null)
          setPlanError(commandErrorMessage(error))
        }
      })
    }, 140)
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [config.alignmentMode, config.backend, effectiveMetashapePath, project, projectRoot, readiness.canContinue, running])

  const browseMetashape = useCallback(async () => {
    const selected = await openDialog({ filters: [{ name: 'Metashape', extensions: ['exe'] }] })
    if (!selected) return
    setConfig((current) => ({ ...current, metashapePath: normalizeDisplayPath(selected) }))
  }, [])

  const startReconstruction = useCallback(async () => {
    if (!project || !projectRoot || !activeManifest || !plan) return
    const savedProject = await saveReconstructionConfig(config.backend, persistedReconstructionConfig(config))
    if (!savedProject) {
      toast.error('保存对齐参数失败，请检查工程状态')
      return
    }
    let startPlan: ExecutionPlan
    try {
      startPlan = await invoke<ExecutionPlan>('build_execution_plan', {
        projectRoot,
        expectedRevision: savedProject.revision,
        config: { backend: config.backend, alignmentMode: config.alignmentMode, metashapePath: config.backend === 'metashape' ? effectiveMetashapePath || null : null },
      })
      setPlan(startPlan)
    } catch (error) {
      toast.error(`刷新执行计划失败：${commandErrorMessage(error)}`)
      return
    }
    setWizardOpen(false)
    startedReconstructionRef.current = true
    const started = await start(
      projectTracksToPipeline(project.tracks),
      pipelineConfig(config, projectRoot, probes),
      {
        skipExtract: true,
        manifestPath: joinDisplayPath(projectRoot, activeManifest),
        reconstruction: {
          projectRoot,
          expectedRevision: savedProject.revision,
          planId: startPlan.planId,
        },
      },
    )
    if (started === false) {
      startedReconstructionRef.current = false
    } else {
      toast.info(`已启动 ${config.backend === 'metashape' ? 'Metashape' : 'COLMAP'} 对齐任务`)
    }
  }, [activeManifest, config, effectiveMetashapePath, plan, probes, project, projectRoot, saveReconstructionConfig, start, toast])

  const startPsxReexport = useCallback(async (componentKey: string) => {
    if (!project || !projectRoot || !activeManifest || !project.reconstruction.projectPath) return
    if (!reexportAvailability.allowed) {
      toast.error(reexportAvailability.reason)
      return
    }
    let reexportPlan: ExecutionPlan
    try {
      reexportPlan = await invoke<ExecutionPlan>('build_reexport_plan', {
        projectRoot,
        expectedRevision: project.revision,
        config: {
          backend: 'metashape',
          alignmentMode: config.alignmentMode,
          metashapePath: effectiveMetashapePath || null,
        },
      })
      setPlan(reexportPlan)
    } catch (error) {
      toast.error(`无法创建 PSX 重新导出任务：${commandErrorMessage(error)}`)
      return
    }
    startedReconstructionRef.current = true
    const started = await start(
      projectTracksToPipeline(project.tracks),
      pipelineConfig(config, projectRoot, probes, componentKey),
      {
        skipExtract: true,
        reexportOnly: true,
        existingProjectPath: joinDisplayPath(projectRoot, project.reconstruction.projectPath),
        manifestPath: joinDisplayPath(projectRoot, activeManifest),
        reconstruction: {
          projectRoot,
          expectedRevision: project.revision,
          planId: reexportPlan.planId,
        },
      },
    )
    if (started === false) {
      startedReconstructionRef.current = false
    } else {
      toast.info(`已启动 Component #${componentKey} 重新导出，不会重新匹配或对齐相机`)
    }
  }, [activeManifest, config, effectiveMetashapePath, probes, project, projectRoot, reexportAvailability, start, toast])

  const reexportFromPsx = useCallback(async () => {
    if (!project || !projectRoot || !activeManifest || !project.reconstruction.projectPath) return
    if (!reexportAvailability.allowed) {
      toast.error(reexportAvailability.reason)
      return
    }
    setInspectingComponents(true)
    try {
      const inspection = await invoke<ComponentInspection>('inspect_metashape_components', {
        projectRoot,
        expectedRevision: project.revision,
        metashapePath: effectiveMetashapePath,
      })
      const decision = prepareComponentSelection(inspection, exportedComponentKey)
      if (decision.mode === 'direct') {
        await startPsxReexport(decision.selectedComponentKey)
      } else {
        setComponentSelection(decision)
      }
    } catch (error) {
      toast.error(`无法读取 PSX Component：${commandErrorMessage(error)}`)
    } finally {
      setInspectingComponents(false)
    }
  }, [activeManifest, effectiveMetashapePath, exportedComponentKey, project, projectRoot, reexportAvailability, startPsxReexport, toast])

  const confirmComponentReexport = useCallback(async () => {
    if (!componentSelection) return
    const key = componentSelection.selectedComponentKey
    setComponentSelection(null)
    await startPsxReexport(key)
  }, [componentSelection, startPsxReexport])

  const canStart = Boolean(project && projectRoot && plan && readiness.canContinue && selectedProbe?.available && !(config.backend === 'colmap' && hasFlatMedia) && !planError && !batchActive && !taskInputLocked)
  let blockReason = planError || '正在生成执行流程'
  if (batchActive) blockReason = '批量队列正在运行，请先停止队列'
  else if (taskInputLocked) blockReason = taskInputLockReason
  else if (!project) blockReason = '请先创建或打开 xPano 工程'
  else if (!readiness.canContinue) blockReason = readiness.blockReason
  else if (config.backend === 'colmap' && hasFlatMedia) blockReason = 'COLMAP 混合素材流程尚未通过回归验证'
  else if (selectedProbe?.available === false) blockReason = `${config.backend === 'metashape' ? 'Metashape' : 'COLMAP'} 当前不可用`

  const openOutput = async () => {
    if (projectRoot) await invoke('open_output_folder', { path: projectRoot })
  }
  const openMetashapeProject = async () => {
    if (!project?.reconstruction.projectPath || !projectRoot) return
    await invoke('open_path_external', { path: joinDisplayPath(projectRoot, project.reconstruction.projectPath) }).catch(() => openOutput())
  }

  if (!project) {
    return <section className="liquid-panel flex h-full min-h-0 flex-col items-center justify-center p-8 text-center"><span className="icon-tile-lg grid h-14 w-14 place-items-center rounded-card"><Images className="h-6 w-6" /></span><h1 className="mt-4 text-[16px] font-semibold text-ink">尚未打开工程</h1><p className="mt-1 max-w-md text-[12px] leading-5 text-muted">先在素材工作区导入并准备素材，xPano 才能生成真实对齐流程。</p><button type="button" onClick={() => navigate('/project/media')} className="motion-press mt-5 flex h-9 items-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-medium text-white"><FolderOpen className="h-4 w-4" /> 前往素材工作区</button></section>
  }

  return (
    <>
      <div className="flex h-full min-h-0 flex-col gap-2">
        {taskInputLocked && <div className="shrink-0 rounded-comfortable border border-warning/20 bg-warning/8 px-3 py-2 text-[10px] text-warning">{taskInputLockReason}</div>}
        <div className="reconstruction-workspace-grid grid min-h-0 flex-1 gap-2">
        <BackendSettings config={config} probes={probes} tracks={project.tracks} running={running || taskInputLocked} dirty={dirty} onChange={setConfig} onBrowseMetashape={browseMetashape} onReconfigure={() => setWizardOpen(true)} onReset={() => setConfig(appliedConfig)} />
        <ExecutionGraph plan={plan} error={planError} running={running} progress={progress} projectComplete={projectComplete} onToggleMonitor={() => setShowMonitor((value) => !value)} />
        <ReconstructionMonitor plan={plan} progress={progress} logs={logs} running={running} canStart={canStart} blockReason={blockReason} showReexport={currentPsx && project.reconstruction.backend === 'metashape'} canReexport={reexportAvailability.allowed} reexportBusy={inspectingComponents} reexportReason={reexportAvailability.reason} onStart={startReconstruction} onReexport={reexportFromPsx} onStop={cancel} onOpenOutput={openOutput} onOpenProject={openMetashapeProject} onViewResults={() => navigate('/project/results')} alignmentReport={alignmentReport} components={alignmentComponents} exportedComponentKey={exportedComponentKey} />
        </div>
      </div>

      {showMonitor && <div className="reconstruction-monitor-backdrop fixed inset-0 z-[115] bg-black/20 xl:hidden" onClick={() => setShowMonitor(false)}><div className="absolute bottom-[60px] right-2 top-[132px] w-[320px] max-w-[calc(100vw-16px)]" onClick={(event) => event.stopPropagation()}><ReconstructionMonitor overlay onClose={() => setShowMonitor(false)} plan={plan} progress={progress} logs={logs} running={running} canStart={canStart} blockReason={blockReason} showReexport={currentPsx && project.reconstruction.backend === 'metashape'} canReexport={reexportAvailability.allowed} reexportBusy={inspectingComponents} reexportReason={reexportAvailability.reason} onStart={startReconstruction} onReexport={reexportFromPsx} onStop={cancel} onOpenOutput={openOutput} onOpenProject={openMetashapeProject} onViewResults={() => navigate('/project/results')} alignmentReport={alignmentReport} components={alignmentComponents} exportedComponentKey={exportedComponentKey} /></div></div>}

      <ReconstructionSetupDialog open={wizardOpen && !taskInputLocked} config={config} probes={probes} tracks={project.tracks} projectRoot={projectRoot} onChange={setConfig} onBrowseMetashape={browseMetashape} onClose={() => setWizardOpen(false)} onStart={startReconstruction} />
      {componentSelection && <ComponentSelectionDialog inspection={componentSelection.inspection} currentExportedComponentKey={componentSelection.currentExportedComponentKey} selectedComponentKey={componentSelection.selectedComponentKey} onSelect={(selectedComponentKey) => setComponentSelection((current) => current ? { ...current, selectedComponentKey } : current)} onCancel={() => setComponentSelection(null)} onConfirm={confirmComponentReexport} />}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </>
  )
}
