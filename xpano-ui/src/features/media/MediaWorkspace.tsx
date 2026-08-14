import { useCallback, useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { ChevronRight, Clock3, FilePlus2, FolderPlus, Gauge, Images, Play } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useProject } from '../../app/useProject'
import { useBatch } from '../../app/useBatch'
import { useJob } from '../../app/useJob'
import { ConfirmDialog } from '../../components/shared/ConfirmDialog'
import { ToastContainer } from '../../components/shared/Toast'
import { useToast } from '../../hooks/useToast'
import type { ProjectTrack } from '../../lib/contracts'
import { assetSource } from '../../lib/assetSource'
import { evaluateMediaReadiness } from '../../lib/mediaReadiness'
import { normalizeDisplayPath } from '../../lib/paths'
import { MaterialImportDialog } from './MaterialImportDialog'
import { allowedTrackTypes, isDraftValid, type ImportPathInfo, type MediaImportDraft } from './mediaTypes'
import { TrackEditor } from './TrackEditor'
import { TrackList } from './TrackList'
import { useBatchTaskLock } from '../batch/useBatchTaskLock'

function createDrafts(infos: ImportPathInfo[]): MediaImportDraft[] {
  return infos.map((info) => {
    const allowed = allowedTrackTypes(info)
    const suggested = info.suggestedType === 'unsupported' ? null : info.suggestedType
    const trackType = suggested && allowed.includes(suggested)
      ? suggested
      : allowed[0] ?? 'standard_photos'
    return {
      id: crypto.randomUUID(),
      info,
      trackType,
      label: info.label || info.name || '素材',
      sourcePath: normalizeDisplayPath(info.path),
      cameraProfile: trackType === 'ordinary_video' ? 'wide' : null,
      trim: null,
      extraction: { framesPerSecond: 1, frameLimit: 0, styleLutPath: null, colorLutPreset: null },
      duration: 0,
    }
  })
}

function formatEta(seconds?: number) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '正在估算'
  const safe = Math.max(0, Math.round(seconds))
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`
}

function previewSource(path?: string) {
  if (!path) return ''
  return assetSource(path)
}

export function MediaWorkspace() {
  const { queue: batchQueue } = useBatch()
  const batchActive = batchQueue.state === 'running' || batchQueue.state === 'stopping'
  const { locked: taskInputLocked, reason: taskInputLockReason } = useBatchTaskLock()
  const navigate = useNavigate()
  const {
    project,
    projectRoot,
    validation,
    pendingDropPaths,
    consumeDropPaths,
    commitImport,
    updateTrackSettings,
    removeTrack,
    setItemSelection,
    setWorkspace,
  } = useProject()
  const { running, progress, preview, mediaItems, startMedia } = useJob()
  const { toast, toasts, removeToast } = useToast()
  const [selectedId, setSelectedId] = useState('')
  const [drafts, setDrafts] = useState<MediaImportDraft[]>([])
  const [removeCandidate, setRemoveCandidate] = useState<ProjectTrack | null>(null)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')

  useEffect(() => {
    if (!project?.tracks.length) {
      setSelectedId('')
      return
    }
    if (!project.tracks.some((track) => track.id === selectedId)) {
      setSelectedId(project.tracks[0].id)
    }
  }, [project, selectedId])

  const analyzePaths = useCallback(async (paths: string[]) => {
    if (taskInputLocked) {
      toast.info(taskInputLockReason)
      return
    }
    const clean = Array.from(new Set(paths.map(normalizeDisplayPath).filter(Boolean)))
    if (!clean.length) return
    setImportError('')
    try {
      const infos = await invoke<ImportPathInfo[]>('analyze_import_paths', { paths: clean })
      setDrafts(createDrafts(infos))
    } catch (error) {
      toast.error(`素材分析失败：${String(error)}`)
    }
  }, [taskInputLockReason, taskInputLocked, toast])

  useEffect(() => {
    if (!pendingDropPaths.length) return
    const paths = pendingDropPaths
    consumeDropPaths()
    void analyzePaths(paths)
  }, [analyzePaths, consumeDropPaths, pendingDropPaths])

  const addFiles = async () => {
    const selected = await openDialog({ multiple: true })
    if (!selected) return
    await analyzePaths(Array.isArray(selected) ? selected : [selected])
  }

  const addFolder = async () => {
    const selected = await openDialog({ directory: true })
    if (!selected || Array.isArray(selected)) return
    await analyzePaths([selected])
  }

  const confirmImport = async () => {
    if (importing) return
    const accepted = drafts.filter(isDraftValid).map(({ id: _id, info: _info, duration: _duration, ...draft }) => draft)
    if (!accepted.length) return
    setImporting(true)
    setImportError('')
    try {
      const success = await commitImport(accepted)
      if (!success) throw new Error('当前环境无法提交素材导入')
      setDrafts([])
      toast.success(`已导入 ${accepted.length} 条素材轨道`)
    } catch (error) {
      const message = error && typeof error === 'object' && 'message' in error ? String(error.message) : String(error)
      setImportError(message)
      toast.error(`素材导入失败：${message}`)
    } finally {
      setImporting(false)
    }
  }

  const executeRemove = async () => {
    const target = removeCandidate
    setRemoveCandidate(null)
    if (!target) return
    const success = await removeTrack(target.id)
    if (success) toast.info(`已从工程移除：${target.label}`)
    else toast.error('移除轨道失败')
  }

  const displayTracks = useMemo(() => project?.tracks.map((track) => {
    const transient = mediaItems[track.id] ?? []
    return transient.length > 0 && (running || track.items.length === 0)
      ? { ...track, items: transient }
      : track
  }) ?? [], [mediaItems, project, running])
  const selectedTrack = useMemo(() => displayTracks.find((track) => track.id === selectedId) ?? null, [displayTracks, selectedId])
  const { selectedItems, totalItems } = useMemo(() => displayTracks.reduce((totals, track) => ({
    selectedItems: totals.selectedItems + track.items.filter((item) => item.selected).length,
    totalItems: totals.totalItems + track.items.length,
  }), { selectedItems: 0, totalItems: 0 }), [displayTracks])
  const readiness = useMemo(() => evaluateMediaReadiness(
    project?.tracks ?? [],
    project?.reconstruction.config.alignmentManifestPath,
  ), [project])
  const pendingTrackIds = useMemo(() => project?.tracks
    .filter((track) => !['ready', 'prepared', 'running'].includes(track.status))
    .map((track) => track.id) ?? [], [project])
  const activeTrack = project?.tracks.find((track) => track.id === progress.trackId) ?? null
  const activeTrackPercent = progress.current !== undefined && progress.total
    ? Math.max(0, Math.min(100, progress.current / progress.total * 100))
    : progress.trackId
      ? progress.stage === 'media.ready' ? 100 : 0
      : progress.percent
  const activeCount = progress.current !== undefined && progress.total !== undefined
    ? `${progress.current}/${progress.total}`
    : ''

  const startPreparation = async () => {
    if (!project || !projectRoot || !pendingTrackIds.length) return
    const started = await startMedia(projectRoot, project.revision, pendingTrackIds)
    if (started) toast.info(`开始准备 ${pendingTrackIds.length} 条素材轨道`)
  }

  const continueToReconstruction = async () => {
    if (!readiness.canContinue) return
    const saved = await setWorkspace('reconstruction')
    if (!saved) {
      toast.error('无法进入对齐页，请检查工程保存状态')
      return
    }
    navigate('/project/reconstruction')
  }

  if (!project) {
    return (
      <>
        <section className="liquid-panel flex h-full min-h-0 flex-col items-center justify-center p-8 text-center">
          <span className="icon-tile-lg grid h-14 w-14 place-items-center rounded-card"><Images className="h-6 w-6" /></span>
          <h1 className="mt-4 text-[16px] font-semibold text-ink">创建素材工程</h1>
          <p className="mt-1 max-w-md text-[12px] leading-5 text-muted">添加第一组素材后，将在素材旁自动创建 xPano 工程目录。</p>
          <div className="mt-5 flex items-center gap-2">
            <button onClick={addFiles} className="motion-press flex h-9 items-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-medium text-white shadow-sm shadow-brand/20"><FilePlus2 className="h-4 w-4" /> 添加文件</button>
            <button onClick={addFolder} className="glass-control motion-press flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/70 hover:text-brand"><FolderPlus className="h-4 w-4" /> 添加照片文件夹</button>
          </div>
        </section>
        {drafts.length > 0 && <MaterialImportDialog drafts={drafts} onChange={setDrafts} onCancel={() => setDrafts([])} onConfirm={confirmImport} busy={importing} error={importError} />}
        <ToastContainer toasts={toasts} onRemove={removeToast} />
      </>
    )
  }

  return (
    <>
      <div className="flex h-full min-h-0 flex-col gap-2">
        {taskInputLocked && <div className="shrink-0 rounded-comfortable border border-warning/20 bg-warning/8 px-3 py-2 text-[10px] text-warning">{taskInputLockReason}</div>}
        <div className="media-workspace-grid grid min-h-0 flex-1 gap-2">
        <TrackList
          tracks={displayTracks}
          selectedId={selectedId}
          missingIds={validation?.missingSourceTrackIds ?? []}
          onSelect={setSelectedId}
          onAddFiles={addFiles}
          onAddFolder={addFolder}
          onRemove={setRemoveCandidate}
          activeTrackId={running ? progress.trackId : undefined}
          activePercent={activeTrackPercent}
          activeCount={activeCount}
          activeEta={formatEta(progress.etaSeconds)}
          editingDisabled={taskInputLocked}
          editingDisabledReason={taskInputLockReason}
        />

        <TrackEditor
          track={selectedTrack}
          projectRoot={projectRoot}
          onSave={updateTrackSettings}
          onSelection={setItemSelection}
          selectionDisabled={taskInputLocked || (running && Boolean(selectedTrack && (mediaItems[selectedTrack.id]?.length ?? 0) > 0))}
          settingsDisabled={taskInputLocked}
          settingsDisabledReason={taskInputLockReason}
        />

        <aside className="media-monitor liquid-panel flex min-h-0 flex-col overflow-hidden p-0">
          <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-3">
            <div><h2 className="text-[13px] font-semibold text-ink">素材准备</h2><p className="text-[10px] text-muted">逐轨执行</p></div>
            <span className="rounded-full bg-brand/8 px-2 py-1 font-mono text-[10px] text-brand">r{project.revisions.media}</span>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="glass-inset rounded-comfortable p-3"><p className="font-mono text-[18px] font-semibold text-ink">{readiness.readyTrackCount}/{project.tracks.length}</p><p className="mt-1 text-[10px] text-muted">已准备轨道</p></div>
              <div className="glass-inset rounded-comfortable p-3"><p className="font-mono text-[18px] font-semibold text-ink">{selectedItems}/{totalItems}</p><p className="mt-1 text-[10px] text-muted">参与素材项</p></div>
            </div>
            <div className="mt-3 space-y-2 rounded-comfortable border border-[var(--xp-line)] p-3 text-[11px] text-muted">
              <p className="flex items-center justify-between"><span className="flex items-center gap-1.5"><Gauge className="h-3.5 w-3.5" /> 当前轨道</span><span className="truncate pl-3 text-ink">{activeTrack?.label || selectedTrack?.label || '未选择'}</span></p>
              <p className="flex items-center justify-between"><span className="flex items-center gap-1.5"><Clock3 className="h-3.5 w-3.5" /> 预计时间</span><span>{running ? formatEta(progress.etaSeconds) : '正在估算'}</span></p>
            </div>
            {running && preview?.left && (
              <div className={`mt-3 grid overflow-hidden rounded-comfortable border border-[var(--xp-line)] bg-black/70 ${preview.right ? 'grid-cols-2' : 'grid-cols-1'}`}>
                <img src={previewSource(preview.left)} alt="当前抽帧" className="aspect-video h-full w-full object-contain" />
                {preview.right && <img src={previewSource(preview.right)} alt="当前抽帧右镜头" className="aspect-video h-full w-full object-contain" />}
              </div>
            )}
          </div>
          <div className="shrink-0 border-t border-[var(--xp-line)] p-3">
            {!running && pendingTrackIds.length === 0 && !readiness.canContinue && <p className="mb-2 text-[10px] leading-4 text-danger">{readiness.blockReason}</p>}
            {readiness.canContinue && !running
              ? <button type="button" onClick={continueToReconstruction} className="motion-press flex h-10 w-full items-center justify-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-semibold text-white shadow-sm shadow-brand/20">下一步：对齐与重建 <ChevronRight className="h-4 w-4" /></button>
              : <button type="button" onClick={startPreparation} disabled={batchActive || running || pendingTrackIds.length === 0} title={batchActive ? '批量队列运行中，请先停止队列' : undefined} className="motion-press flex h-10 w-full items-center justify-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-semibold text-white shadow-sm shadow-brand/20 disabled:cursor-not-allowed disabled:opacity-45"><Play className="h-4 w-4 fill-current" /> {batchActive ? '批量队列运行中' : running ? progress.message : pendingTrackIds.length ? '开始抽帧' : '素材状态需处理'}</button>}
          </div>
        </aside>
        </div>
      </div>

      {drafts.length > 0 && <MaterialImportDialog drafts={drafts} onChange={setDrafts} onCancel={() => setDrafts([])} onConfirm={confirmImport} busy={importing} error={importError} />}
      <ConfirmDialog open={Boolean(removeCandidate)} title="移除素材轨道" message={`仅从工程中移除“${removeCandidate?.label ?? ''}”，不会删除源文件。`} confirmText="移除" danger onConfirm={executeRemove} onCancel={() => setRemoveCandidate(null)} />
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </>
  )
}
