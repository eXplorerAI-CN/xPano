import { useCallback, useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { ArrowLeft, Check, FilePlus2, FolderOpen, FolderPlus, Images, ScanLine, Sparkles } from 'lucide-react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useBatch } from '../../app/useBatch'
import type { OpenProjectResult } from '../../app/projectContext'
import { useProject } from '../../app/useProject'
import { ThemeControls } from '../../components/layout/ThemeControls'
import { WindowControls } from '../../components/layout/WindowControls'
import { RuntimeReadinessBadge } from '../../components/layout/RuntimeReadinessBadge'
import { BrandAboutButton } from '../../components/layout/BrandAboutButton'
import { ConfirmDialog } from '../../components/shared/ConfirmDialog'
import type { ProjectTrack, XpanoProjectV2 } from '../../lib/contracts'
import { commandErrorMessage } from '../../lib/commandError'
import { normalizeDisplayPath } from '../../lib/paths'
import type { ThemeMode } from '../../lib/types'
import { MaterialImportDialog } from '../media/MaterialImportDialog'
import { createMediaImportDrafts, isDraftValid, type ImportPathInfo, type MediaImportDraft } from '../media/mediaTypes'
import {
  configFromProject,
  defaultReconstructionConfig,
  persistedReconstructionConfig,
  type ReconstructionConfigDraft,
} from '../reconstruction/reconstructionTypes'
import { DEFAULT_TRAINING_CONFIG, type TrainingConfig } from '../training/trainingConfig'
import { BatchTrackSettings } from './BatchTrackSettings'
import { batchEditorProjectRoot, emptyBatchTask, setBatchStage, validateStagePrefix, type BatchTask } from './batchTypes'

interface Props {
  themeMode: ThemeMode
  onThemeModeChange: (mode: ThemeMode) => void
}

const inputClass = 'theme-input batch-form-control mt-1 w-full'

function trainingConfigFromProject(project: XpanoProjectV2): TrainingConfig {
  return {
    ...DEFAULT_TRAINING_CONFIG,
    ...(project.training.config as Partial<TrainingConfig>),
    gui: true,
  }
}

function duplicateBatchTask(source: BatchTask): BatchTask {
  const draft = emptyBatchTask()
  return {
    ...draft,
    projectId: source.projectId,
    projectRoot: source.projectRoot,
    label: `${source.label} - 副本`,
    configuredRevision: source.configuredRevision,
    stages: { ...source.stages },
    stageStatus: {
      media: source.stages.media ? 'pending' : 'disabled',
      reconstruction: source.stages.reconstruction ? 'pending' : 'disabled',
      training: source.stages.training ? 'pending' : 'disabled',
    },
    pipeline: { mediaTrackIds: [...source.pipeline.mediaTrackIds] },
  }
}

export function BatchTaskEditor({ themeMode, onThemeModeChange }: Props) {
  const navigate = useNavigate()
  const location = useLocation()
  const { taskId } = useParams()
  const { queue, saveAndEnqueueTask } = useBatch()
  const { project, projectRoot, openProject, removeTrack, updateTrackSettings } = useProject()
  const duplicate = new URLSearchParams(location.search).get('duplicate') === '1'
  const duplicateSource = taskId ? queue.tasks.find((item) => item.taskId === taskId) : undefined
  const existing = duplicate ? undefined : duplicateSource
  const requestedRoot = useMemo(() => new URLSearchParams(location.search).get('projectRoot'), [location.search])
  const [task, setTask] = useState<BatchTask>(() =>
    duplicateSource ? (duplicate ? duplicateBatchTask(duplicateSource) : structuredClone(duplicateSource)) : emptyBatchTask(),
  )
  const [editorRoot, setEditorRoot] = useState(() => batchEditorProjectRoot(existing?.projectRoot, requestedRoot))
  const [reconstruction, setReconstruction] = useState<ReconstructionConfigDraft>(defaultReconstructionConfig)
  const [training, setTraining] = useState<TrainingConfig>(DEFAULT_TRAINING_CONFIG)
  const [drafts, setDrafts] = useState<MediaImportDraft[]>([])
  const [importing, setImporting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [removeTarget, setRemoveTarget] = useState<ProjectTrack | null>(null)
  const locked = existing?.state === 'queued' || existing?.state === 'running'
  const editorProjectMatches = Boolean(editorRoot) && normalizeDisplayPath(editorRoot) === normalizeDisplayPath(projectRoot)
  const editorProject = editorProjectMatches ? project : null

  useEffect(() => {
    if (duplicateSource) {
      setTask(duplicate ? duplicateBatchTask(duplicateSource) : structuredClone(duplicateSource))
      setEditorRoot(duplicateSource.projectRoot)
    }
  }, [duplicate, duplicateSource])

  useEffect(() => {
    if (!existing && requestedRoot) setEditorRoot(requestedRoot)
  }, [existing, requestedRoot])

  useEffect(() => {
    if (editorRoot && normalizeDisplayPath(editorRoot) !== normalizeDisplayPath(projectRoot)) {
      void openProject(editorRoot)
    }
  }, [editorRoot, openProject, projectRoot])

  useEffect(() => {
    if (!editorProject || !editorRoot) return
    setTask((current) => ({
      ...current,
      label: current.label || editorProject.name,
      projectId: editorProject.projectId,
      projectRoot: editorRoot,
      configuredRevision: editorProject.revision,
      pipeline: {
        ...current.pipeline,
        mediaTrackIds: current.pipeline.mediaTrackIds.length
          ? current.pipeline.mediaTrackIds.filter((id) => editorProject.tracks.some((track) => track.id === id))
          : editorProject.tracks.map((track) => track.id),
      },
    }))
    setReconstruction(configFromProject(editorProject.reconstruction.backend, editorProject.reconstruction.config))
    setTraining(trainingConfigFromProject(editorProject))
  }, [editorProject, editorRoot])

  const selectedTracks = useMemo(() => new Set(task.pipeline.mediaTrackIds), [task.pipeline.mediaTrackIds])

  const setStages = (key: keyof BatchTask['stages'], value: boolean) => {
    if (locked) return
    setTask((current) => {
      const stages = setBatchStage(current.stages, key, value)
      return {
        ...current,
        stages,
        stageStatus: {
          media: stages.media ? 'pending' : 'disabled',
          reconstruction: stages.reconstruction ? 'pending' : 'disabled',
          training: stages.training ? 'pending' : 'disabled',
        },
      }
    })
  }

  const browseProject = async () => {
    const selected = await openDialog({ directory: true })
    if (selected && !Array.isArray(selected)) {
      const root = normalizeDisplayPath(selected)
      setEditorRoot(root)
      await openProject(root)
    }
  }

  const analyzePaths = useCallback(async (paths: string[]) => {
    const clean = Array.from(new Set(paths.map(normalizeDisplayPath).filter(Boolean)))
    if (!clean.length) return
    try {
      const infos = await invoke<ImportPathInfo[]>('analyze_import_paths', {
        paths: clean,
      })
      setDrafts(createMediaImportDrafts(infos))
      setError(null)
    } catch (reason) {
      setError(`素材分析失败：${commandErrorMessage(reason)}`)
    }
  }, [])

  const addFiles = async () => {
    const selected = await openDialog({ multiple: true })
    if (selected) await analyzePaths(Array.isArray(selected) ? selected : [selected])
  }

  const addFolder = async () => {
    const selected = await openDialog({ directory: true })
    if (selected && !Array.isArray(selected)) await analyzePaths([selected])
  }

  const confirmImport = async () => {
    const accepted = drafts.filter(isDraftValid).map(({ id: _id, info: _info, duration: _duration, ...draft }) => draft)
    if (!accepted.length || importing) return
    setImporting(true)
    try {
      let root = editorRoot
      let revision = editorProject?.revision
      if (!root || revision === undefined) {
        const created = await invoke<OpenProjectResult>('create_project', {
          name: accepted[0].label,
          firstSource: accepted[0].sourcePath,
          optionalRoot: null,
        })
        root = created.projectRoot
        revision = created.project.revision
        setEditorRoot(root)
      }
      await invoke<XpanoProjectV2>('commit_import', {
        projectRoot: root,
        expectedRevision: revision,
        drafts: accepted,
      })
      if (!(await openProject(root))) throw new Error('素材已导入，但工程刷新失败')
      setDrafts([])
      setError(null)
    } catch (reason) {
      setError(`素材导入失败：${commandErrorMessage(reason)}`)
    } finally {
      setImporting(false)
    }
  }

  const toggleTrack = (trackId: string) => {
    setTask((current) => ({
      ...current,
      pipeline: {
        ...current.pipeline,
        mediaTrackIds: current.pipeline.mediaTrackIds.includes(trackId)
          ? current.pipeline.mediaTrackIds.filter((id) => id !== trackId)
          : [...current.pipeline.mediaTrackIds, trackId],
      },
    }))
  }

  const confirmRemoveTrack = async () => {
    const target = removeTarget
    setRemoveTarget(null)
    if (!target) return
    const removed = await removeTrack(target.id)
    if (!removed) {
      setError(`无法删除素材轨道：${target.label}`)
      return
    }
    setTask((current) => ({
      ...current,
      pipeline: {
        ...current.pipeline,
        mediaTrackIds: current.pipeline.mediaTrackIds.filter((id) => id !== target.id),
      },
    }))
  }

  const save = async () => {
    if (locked) return
    const prefixError = validateStagePrefix(task.stages)
    if (prefixError) return setError(prefixError)
    if (!editorProject || !editorRoot) return setError('请先选择或创建一个 xPano 工程')
    if (!task.label.trim()) return setError('请填写任务名称')
    if (!task.pipeline.mediaTrackIds.length) return setError('请至少选择一条素材轨道')
    setSaving(true)
    setError(null)
    try {
      await saveAndEnqueueTask({
        task: {
          ...task,
          projectId: editorProject.projectId,
          projectRoot: editorRoot,
          label: task.label.trim(),
          configuredRevision: editorProject.revision,
          pipeline: task.pipeline,
        },
        reconstructionBackend: task.stages.reconstruction ? reconstruction.backend : null,
        reconstructionConfig: task.stages.reconstruction ? persistedReconstructionConfig(reconstruction) : null,
        reconstructionPlanConfig: task.stages.reconstruction
          ? {
              backend: reconstruction.backend,
              alignmentMode: reconstruction.alignmentMode,
              metashapePath: reconstruction.backend === 'metashape' ? reconstruction.metashapePath || null : null,
            }
          : null,
        trainingConfig: task.stages.training ? { ...training, gui: true } : null,
      })
      navigate('/batch')
    } catch (reason) {
      setError(commandErrorMessage(reason))
    } finally {
      setSaving(false)
    }
  }

  const stageOptions = [
    {
      key: 'media' as const,
      label: '素材准备',
      hint: '抽帧与预处理',
      icon: Images,
    },
    {
      key: 'reconstruction' as const,
      label: '对齐重建',
      hint: '生成训练数据',
      icon: ScanLine,
    },
    {
      key: 'training' as const,
      label: '高斯训练',
      hint: '启动 LichtFeld',
      icon: Sparkles,
    },
  ]

  return (
    <div className="app-shell relative z-10 h-screen min-h-[720px] min-w-[1024px] overflow-hidden text-ink">
      <header className="liquid-topbar app-titlebar drag-region flex items-center justify-between px-3.5">
        <div className="flex items-center gap-2.5">
          <BrandAboutButton />
          <span className="titlebar-section-divider" />
          <span className="text-[11px] text-muted">批量任务设置</span>
        </div>
        <div className="topbar-control-group no-drag flex items-center gap-1">
          <RuntimeReadinessBadge />
          <ThemeControls themeMode={themeMode} onThemeModeChange={onThemeModeChange} />
          <span className="topbar-control-divider" />
          <WindowControls />
        </div>
      </header>
      <main className="app-workspace batch-editor-workspace min-h-0 overflow-auto p-4 md:p-6">
        <section className="liquid-panel batch-task-editor-panel mx-auto max-w-6xl p-5 md:p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <button type="button" onClick={() => navigate('/batch')} className="mb-3 flex items-center gap-1 text-[11px] text-muted hover:text-brand">
                <ArrowLeft className="h-3.5 w-3.5" />
                返回任务列表
              </button>
              <h1 className="text-[17px] font-semibold">{duplicate ? '复制任务' : taskId ? '编辑任务' : '新增任务'}</h1>
              <p className="mt-1 text-[11px] text-muted">在一个页面完成素材、对齐和训练设置</p>
            </div>
            <button
              type="button"
              onClick={save}
              disabled={saving || locked}
              className="motion-press flex h-9 items-center gap-1.5 rounded-comfortable bg-brand px-4 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45"
            >
              <Check className="h-3.5 w-3.5" />
              {locked ? '任务已锁定' : saving ? '保存中…' : '保存并入队'}
            </button>
          </div>
          {error && <div className="mt-4 rounded-comfortable border border-danger/20 bg-danger/8 px-3 py-2 text-[11px] text-danger">{error}</div>}

          <div className="mt-5 grid gap-4 md:grid-cols-[minmax(260px,0.9fr)_minmax(420px,1.4fr)]">
            <label>
              <span className="text-[11px] font-medium text-muted">任务名称</span>
              <input
                disabled={locked}
                value={task.label}
                onChange={(event) => setTask({ ...task, label: event.target.value })}
                className={inputClass}
                placeholder="例如：南区夜间处理"
              />
            </label>
            <label>
              <span className="text-[11px] font-medium text-muted">工程目录</span>
              <div className="mt-1 flex gap-2">
                <input readOnly value={editorRoot} className="theme-input batch-form-control min-w-0 flex-1" placeholder="选择现有工程，或直接添加素材创建工程" />
                <button
                  type="button"
                  disabled={locked}
                  onClick={browseProject}
                  className="glass-control flex h-9 items-center gap-1.5 rounded-comfortable px-3 text-[11px]"
                >
                  <FolderOpen className="h-3.5 w-3.5" />
                  选择
                </button>
              </div>
            </label>
          </div>

          <div className="mt-5 grid gap-2 md:grid-cols-3">
            {stageOptions.map((stage) => {
              const Icon = stage.icon
              const enabled = task.stages[stage.key]
              const dependentLocked = stage.key === 'reconstruction' ? !task.stages.media : stage.key === 'training' ? !task.stages.reconstruction : false
              return (
                <button
                  type="button"
                  key={stage.key}
                  disabled={locked || dependentLocked}
                  onClick={() => setStages(stage.key, !enabled)}
                  className={`batch-stage-toggle grid min-h-[72px] grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 rounded-comfortable border px-3 text-left transition-colors ${enabled ? 'border-brand/30 bg-brand/8' : 'border-ink/[0.08] bg-ink/[0.02]'} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  <span className={`grid h-8 w-8 place-items-center rounded-full ${enabled ? 'bg-brand text-white' : 'bg-ink/[0.06] text-muted'}`}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[12px] font-semibold">{stage.label}</span>
                    <span className="mt-0.5 block text-[10px] text-muted">{dependentLocked ? '需先开启前一阶段' : stage.hint}</span>
                  </span>
                  <span className="whitespace-nowrap text-[10px] text-muted">{enabled ? '已开启' : '未开启'}</span>
                </button>
              )
            })}
          </div>

          <div className="mt-5 grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.9fr)]">
            <section className="glass-inset rounded-comfortable p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-[12px] font-semibold">素材与抽帧</h2>
                  <p className="mt-1 text-[10px] text-muted">选择本任务要处理的轨道；导入窗口中可设置 FPS、上限、裁剪和 LUT。</p>
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    disabled={locked}
                    onClick={addFiles}
                    className="glass-control flex h-8 items-center gap-1 rounded-subtle px-2 text-[10px]"
                  >
                    <FilePlus2 className="h-3 w-3" />
                    文件
                  </button>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={addFolder}
                    className="glass-control flex h-8 items-center gap-1 rounded-subtle px-2 text-[10px]"
                  >
                    <FolderPlus className="h-3 w-3" />
                    文件夹
                  </button>
                </div>
              </div>
              <div className="mt-3 max-h-64 space-y-1.5 overflow-auto pr-1">
                {editorProject?.tracks.length ? (
                  editorProject.tracks.map((track) => (
                    <BatchTrackSettings
                      key={track.id}
                      track={track}
                      selected={selectedTracks.has(track.id)}
                      disabled={locked}
                      onSelectedChange={() => toggleTrack(track.id)}
                      onSave={updateTrackSettings}
                      onRemove={setRemoveTarget}
                    />
                  ))
                ) : (
                  <div className="grid min-h-32 place-items-center rounded-subtle border border-dashed border-ink/[0.12] text-[11px] text-muted">
                    选择工程或添加素材
                  </div>
                )}
              </div>
            </section>

            <div className="space-y-4">
              <section className={`glass-inset rounded-comfortable p-4 ${task.stages.reconstruction ? '' : 'opacity-45'}`}>
                <h2 className="text-[12px] font-semibold">对齐参数</h2>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <label>
                    <span className="text-[10px] text-muted">后端</span>
                    <select
                      disabled={locked || !task.stages.reconstruction}
                      value={reconstruction.backend}
                      onChange={(event) =>
                        setReconstruction({
                          ...reconstruction,
                          backend: event.target.value as ReconstructionConfigDraft['backend'],
                        })
                      }
                      className={inputClass}
                    >
                      <option value="metashape">Metashape</option>
                      <option value="colmap">COLMAP</option>
                    </select>
                  </label>
                  {reconstruction.backend === 'metashape' ? (
                    <label>
                      <span className="text-[10px] text-muted">对齐流程</span>
                      <select
                        disabled={locked || !task.stages.reconstruction}
                        value={reconstruction.alignmentMode}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            alignmentMode: event.target.value as ReconstructionConfigDraft['alignmentMode'],
                          })
                        }
                        className={inputClass}
                      >
                        <option value="backbone">全景骨架优先</option>
                        <option value="mixed">混合素材</option>
                      </select>
                    </label>
                  ) : (
                    <label>
                      <span className="text-[10px] text-muted">匹配方式</span>
                      <select
                        disabled={locked || !task.stages.reconstruction}
                        value={reconstruction.colmapMatcher}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            colmapMatcher: event.target.value as ReconstructionConfigDraft['colmapMatcher'],
                          })
                        }
                        className={inputClass}
                      >
                        <option value="sequential">顺序匹配</option>
                        <option value="exhaustive">穷举匹配</option>
                      </select>
                    </label>
                  )}
                </div>
                {reconstruction.backend === 'metashape' ? (
                  <>
                    <div className="mt-3 grid grid-cols-2 gap-3">
                      <label>
                        <span className="text-[10px] text-muted">特征点上限</span>
                        <input
                          disabled={locked || !task.stages.reconstruction}
                          type="number"
                          min={1000}
                          value={reconstruction.metashapeKeypointLimit}
                          onChange={(event) =>
                            setReconstruction({
                              ...reconstruction,
                              metashapeKeypointLimit: Math.max(1000, Number(event.target.value) || 40000),
                            })
                          }
                          className={inputClass}
                        />
                      </label>
                      <label>
                        <span className="text-[10px] text-muted">连接点上限</span>
                        <input
                          disabled={locked || !task.stages.reconstruction}
                          type="number"
                          min={0}
                          value={reconstruction.metashapeTiepointLimit}
                          onChange={(event) =>
                            setReconstruction({
                              ...reconstruction,
                              metashapeTiepointLimit: Math.max(0, Number(event.target.value) || 0),
                            })
                          }
                          className={inputClass}
                        />
                      </label>
                    </div>
                    <label className="mt-3 block">
                      <span className="text-[10px] text-muted">Metashape 路径</span>
                      <input
                        disabled={locked || !task.stages.reconstruction}
                        value={reconstruction.metashapePath}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            metashapePath: event.target.value,
                          })
                        }
                        className={inputClass}
                        placeholder="留空时自动检测"
                      />
                    </label>
                  </>
                ) : (
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <label>
                      <span className="text-[10px] text-muted">最大图像尺寸</span>
                      <input
                        disabled={locked || !task.stages.reconstruction}
                        type="number"
                        min={1}
                        value={reconstruction.colmapMaxImageSize}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            colmapMaxImageSize: Math.max(1, Number(event.target.value) || 1600),
                          })
                        }
                        className={inputClass}
                      />
                    </label>
                    <label>
                      <span className="text-[10px] text-muted">最大特征数</span>
                      <input
                        disabled={locked || !task.stages.reconstruction}
                        type="number"
                        min={1}
                        value={reconstruction.colmapMaxNumFeatures}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            colmapMaxNumFeatures: Math.max(1, Number(event.target.value) || 4096),
                          })
                        }
                        className={inputClass}
                      />
                    </label>
                  </div>
                )}
                <details className="mt-3 border-t border-ink/[0.07] pt-2 text-[10px] text-muted">
                  <summary className="cursor-pointer select-none hover:text-brand">高级参数</summary>
                  <div className="mt-2 grid grid-cols-2 gap-3">
                    <label>
                      <span>向上轴</span>
                      <select
                        disabled={locked || !task.stages.reconstruction}
                        value={reconstruction.upAxis}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            upAxis: event.target.value,
                          })
                        }
                        className={inputClass}
                      >
                        <option value="+Y">+Y</option>
                        <option value="+Z">+Z</option>
                        <option value="-Y">-Y</option>
                        <option value="-Z">-Z</option>
                      </select>
                    </label>
                    {reconstruction.backend === 'colmap' && (
                      <label>
                        <span>密度预设</span>
                        <select
                          disabled={locked || !task.stages.reconstruction}
                          value={reconstruction.colmapDensityPreset}
                          onChange={(event) =>
                            setReconstruction({
                              ...reconstruction,
                              colmapDensityPreset: event.target.value as ReconstructionConfigDraft['colmapDensityPreset'],
                            })
                          }
                          className={inputClass}
                        >
                          <option value="stable">稳定</option>
                          <option value="high-density">高密度</option>
                          <option value="experimental-high-density">实验性高密度</option>
                        </select>
                      </label>
                    )}
                  </div>
                  {reconstruction.backend === 'colmap' && (
                    <label className="mt-2 flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        disabled={locked || !task.stages.reconstruction}
                        checked={reconstruction.colmapUseGpu}
                        onChange={(event) =>
                          setReconstruction({
                            ...reconstruction,
                            colmapUseGpu: event.target.checked,
                          })
                        }
                        className="accent-brand"
                      />
                      使用 GPU
                    </label>
                  )}
                </details>
              </section>

              <section className={`glass-inset rounded-comfortable p-4 ${task.stages.training ? '' : 'opacity-45'}`}>
                <div className="flex items-center justify-between">
                  <h2 className="text-[12px] font-semibold">训练参数</h2>
                  <label className="flex items-center gap-1.5 text-[10px] text-muted">
                    <input
                      type="checkbox"
                      disabled={locked || !task.stages.training}
                      checked={training.bilateralGrid}
                      onChange={(event) =>
                        setTraining({
                          ...training,
                          bilateralGrid: event.target.checked,
                        })
                      }
                      className="accent-brand"
                    />
                    双边网格
                  </label>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <label>
                    <span className="text-[10px] text-muted">迭代步数</span>
                    <input
                      disabled={locked || !task.stages.training}
                      type="number"
                      min={1}
                      value={training.iterations}
                      onChange={(event) =>
                        setTraining({
                          ...training,
                          iterations: Math.max(1, Number(event.target.value) || 30000),
                        })
                      }
                      className={inputClass}
                    />
                  </label>
                  <label>
                    <span className="text-[10px] text-muted">最大图像宽度</span>
                    <input
                      disabled={locked || !task.stages.training}
                      type="number"
                      min={0}
                      value={training.maxWidth}
                      onChange={(event) =>
                        setTraining({
                          ...training,
                          maxWidth: Math.max(0, Number(event.target.value) || 0),
                        })
                      }
                      className={inputClass}
                    />
                  </label>
                </div>
                <details className="mt-3 border-t border-ink/[0.07] pt-2 text-[10px] text-muted">
                  <summary className="cursor-pointer select-none hover:text-brand">高级参数</summary>
                  <div className="mt-2 grid grid-cols-2 gap-3">
                    <label>
                      <span>训练策略</span>
                      <select
                        disabled={locked || !task.stages.training}
                        value={training.strategy}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            strategy: event.target.value as TrainingConfig['strategy'],
                          })
                        }
                        className={inputClass}
                      >
                        <option value="mrnf">MRNF</option>
                        <option value="mcmc">MCMC</option>
                        <option value="igs+">IGS+</option>
                      </select>
                    </label>
                    <label>
                      <span>SH 阶数</span>
                      <select
                        disabled={locked || !task.stages.training}
                        value={training.shDegree}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            shDegree: Number(event.target.value) as TrainingConfig['shDegree'],
                          })
                        }
                        className={inputClass}
                      >
                        <option value={0}>0</option>
                        <option value={1}>1</option>
                        <option value={2}>2</option>
                        <option value={3}>3</option>
                      </select>
                    </label>
                    <label>
                      <span>高斯数量上限</span>
                      <input
                        disabled={locked || !task.stages.training}
                        type="number"
                        min={1}
                        value={training.maxGaussians}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            maxGaussians: Math.max(1, Number(event.target.value) || 1_000_000),
                          })
                        }
                        className={inputClass}
                      />
                    </label>
                    <label>
                      <span>缩放倍率</span>
                      <select
                        disabled={locked || !task.stages.training}
                        value={training.resizeFactor}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            resizeFactor: event.target.value as TrainingConfig['resizeFactor'],
                          })
                        }
                        className={inputClass}
                      >
                        <option value="auto">自动</option>
                        <option value="1">1×</option>
                        <option value="2">2×</option>
                        <option value="4">4×</option>
                        <option value="8">8×</option>
                      </select>
                    </label>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        disabled={locked || !task.stages.training}
                        checked={training.useCpuCache}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            useCpuCache: event.target.checked,
                          })
                        }
                        className="accent-brand"
                      />
                      CPU 缓存
                    </label>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        disabled={locked || !task.stages.training}
                        checked={training.useFsCache}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            useFsCache: event.target.checked,
                          })
                        }
                        className="accent-brand"
                      />
                      文件缓存
                    </label>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        disabled={locked || !task.stages.training}
                        checked={training.enableMip}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            enableMip: event.target.checked,
                          })
                        }
                        className="accent-brand"
                      />
                      Mip
                    </label>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        disabled={locked || !task.stages.training}
                        checked={training.undistort}
                        onChange={(event) =>
                          setTraining({
                            ...training,
                            undistort: event.target.checked,
                          })
                        }
                        className="accent-brand"
                      />
                      训练前去畸变
                    </label>
                  </div>
                </details>
              </section>
            </div>
          </div>
        </section>
      </main>
      {drafts.length > 0 && (
        <MaterialImportDialog
          drafts={drafts}
          onChange={setDrafts}
          onCancel={() => setDrafts([])}
          onConfirm={confirmImport}
          busy={importing}
          error={error || ''}
        />
      )}
      <ConfirmDialog
        open={Boolean(removeTarget)}
        title="删除素材轨道？"
        message={`将从当前工程移除“${removeTarget?.label || ''}”，不会删除源文件。`}
        confirmText="删除轨道"
        danger
        onConfirm={() => void confirmRemoveTrack()}
        onCancel={() => setRemoveTarget(null)}
      />
    </div>
  )
}
