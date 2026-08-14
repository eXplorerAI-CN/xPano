import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, ImageOff, Images, Save, Scissors, X } from 'lucide-react'
import { VideoTrimmer } from '../../components/pipeline/VideoTrimmer'
import type { ProjectMediaItem, ProjectTrack, TrackSettingsPatch } from '../../lib/contracts'
import { assetSource } from '../../lib/assetSource'
import { joinDisplayPath } from '../../lib/paths'
import { framesPerSecondForLimit } from '../../lib/extractionRate'
import { PhotoFolderPreview } from './PhotoFolderPreview'
import { ColorLutField } from './ColorLutField'
import { builtinColorLutPresetForSource, isStyleLutSupported } from './colorLut'

interface TrackEditorProps {
  track: ProjectTrack | null
  projectRoot: string
  onSave: (trackId: string, patch: TrackSettingsPatch) => Promise<boolean>
  onSelection: (trackId: string, itemIds: string[], selected: boolean) => Promise<boolean>
  selectionDisabled?: boolean
  settingsDisabled?: boolean
  settingsDisabledReason?: string
}

const PAGE_SIZE = 120

function itemThumb(projectRoot: string, item: ProjectMediaItem, side: 'left' | 'right' | 'single') {
  const relative = side === 'left' ? item.thumbnailLeft || item.left : side === 'right' ? item.thumbnailRight || item.right : item.thumbnail || item.image
  return relative ? assetSource(joinDisplayPath(projectRoot, relative)) : ''
}

function ItemGrid({ track, projectRoot, onSelection, selectionDisabled }: Pick<TrackEditorProps, 'track' | 'projectRoot' | 'onSelection' | 'selectionDisabled'> & { track: ProjectTrack }) {
  const [filter, setFilter] = useState<'all' | 'selected' | 'unselected'>('all')
  const [page, setPage] = useState(0)
  const filtered = useMemo(() => track.items.filter((item) => filter === 'all' || (filter === 'selected' ? item.selected : !item.selected)), [filter, track.items])
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const visible = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)

  useEffect(() => setPage(0), [filter, track.id])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[var(--xp-line)] px-4 py-2.5">
        <div className="flex items-center gap-1">
          {(['all', 'selected', 'unselected'] as const).map((value) => (
            <button key={value} type="button" onClick={() => setFilter(value)} className={`motion-press h-8 rounded-comfortable px-3 text-[11px] font-medium ${filter === value ? 'bg-brand text-white' : 'glass-control text-muted hover:text-ink'}`}>
              {value === 'all' ? '全部' : value === 'selected' ? '已选择' : '未选择'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-muted">
          <span>已选择 {track.items.filter((item) => item.selected).length} / {track.items.length}</span>
          <button type="button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))} className="glass-control grid h-7 w-7 place-items-center rounded-comfortable disabled:opacity-30" aria-label="上一页"><ChevronLeft className="h-3.5 w-3.5" /></button>
          <span className="font-mono">{page + 1}/{pageCount}</span>
          <button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))} className="glass-control grid h-7 w-7 place-items-center rounded-comfortable disabled:opacity-30" aria-label="下一页"><ChevronRight className="h-3.5 w-3.5" /></button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(118px,1fr))] gap-1.5">
          {visible.map((item) => {
            const paired = Boolean(item.left || item.right)
            const left = itemThumb(projectRoot, item, paired ? 'left' : 'single')
            const right = paired ? itemThumb(projectRoot, item, 'right') : ''
            return (
              <button key={item.id} type="button" disabled={selectionDisabled} onClick={() => onSelection(track.id, [item.id], !item.selected)} className={`group relative aspect-[4/3] min-w-0 overflow-hidden rounded-comfortable border bg-[var(--xp-inset)] disabled:cursor-wait ${item.selected ? 'border-brand ring-1 ring-brand/35' : 'border-[var(--xp-line)] opacity-60 hover:opacity-100'}`}>
                <span className={`grid h-full ${right ? 'grid-cols-2' : 'grid-cols-1'}`}>
                  {left ? <img src={left} alt="" className="h-full w-full object-cover" loading="lazy" /> : <span className="grid place-items-center"><ImageOff className="h-5 w-5 text-muted" /></span>}
                  {right && <img src={right} alt="" className="h-full w-full object-cover" loading="lazy" />}
                </span>
                <span className={`absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full border text-white ${item.selected ? 'border-brand bg-brand' : 'border-white/50 bg-black/30'}`}>{item.selected && <Check className="h-3 w-3" />}</span>
              </button>
            )
          })}
        </div>
        {visible.length === 0 && <div className="grid min-h-48 place-items-center text-[12px] text-muted">当前筛选没有素材项</div>}
      </div>
    </div>
  )
}

export function TrackEditor({ track, projectRoot, onSave, onSelection, selectionDisabled = false, settingsDisabled = false, settingsDisabledReason }: TrackEditorProps) {
  const [trim, setTrim] = useState<{ start: number; end: number } | null>(track?.trim ?? null)
  const [framesPerSecond, setFramesPerSecond] = useState(track?.extraction.framesPerSecond ?? 1)
  const [frameLimit, setFrameLimit] = useState(track?.extraction.frameLimit ?? 0)
  const [styleLutPath, setStyleLutPath] = useState<string | null>(track?.extraction.styleLutPath ?? null)
  const [colorLutPreset, setColorLutPreset] = useState<string | null>(track?.extraction.colorLutPreset ?? null)
  const [cameraProfile, setCameraProfile] = useState<'wide' | 'standard'>(track?.cameraProfile === 'standard' ? 'standard' : 'wide')
  const [saving, setSaving] = useState(false)
  const [editingExisting, setEditingExisting] = useState(false)

  useEffect(() => {
    setTrim(track?.trim ?? null)
    setFramesPerSecond(track?.extraction.framesPerSecond ?? 1)
    setFrameLimit(track?.extraction.frameLimit ?? 0)
    setStyleLutPath(track?.extraction.styleLutPath ?? null)
    setColorLutPreset(track?.extraction.colorLutPreset ?? null)
    setCameraProfile(track?.cameraProfile === 'standard' ? 'standard' : 'wide')
    setEditingExisting(false)
  }, [track])

  if (!track) {
    return (
      <section className="liquid-panel grid min-h-0 place-items-center text-center text-muted">
        <div><Images className="mx-auto h-7 w-7 opacity-45" /><p className="mt-3 text-[12px]">选择一条素材轨道</p></div>
      </section>
    )
  }

  const video = track.type === 'panoramic_video' || track.type === 'ordinary_video'
  const styleSupported = isStyleLutSupported(track.type)
  const cancelEditing = () => {
    setTrim(track.trim)
    setFramesPerSecond(track.extraction.framesPerSecond)
    setFrameLimit(track.extraction.frameLimit)
    setStyleLutPath(track.extraction.styleLutPath ?? null)
    setColorLutPreset(track.extraction.colorLutPreset ?? null)
    setCameraProfile(track.cameraProfile === 'standard' ? 'standard' : 'wide')
    setEditingExisting(false)
  }

  if (settingsDisabled) {
    return (
      <section className="liquid-panel flex min-h-0 flex-col overflow-hidden p-0">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-4">
          <div className="min-w-0"><h2 className="truncate text-[13px] font-semibold text-ink">{track.label}</h2><p className="truncate font-mono text-[10px] text-muted">{track.sourcePath}</p></div>
          <span className="rounded-full bg-warning/10 px-2 py-1 text-[10px] text-warning" title={settingsDisabledReason}>参数已锁定</span>
        </header>
        {track.items.length > 0
          ? <ItemGrid track={track} projectRoot={projectRoot} onSelection={onSelection} selectionDisabled />
          : <div className="grid min-h-0 flex-1 place-items-center px-6 text-center text-[11px] leading-5 text-muted">{settingsDisabledReason || '该任务的素材参数已锁定'}</div>}
      </section>
    )
  }

  if (track.items.length > 0 && !editingExisting) {
    return (
      <section className="liquid-panel flex min-h-0 flex-col overflow-hidden p-0">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-4">
          <div className="min-w-0"><h2 className="truncate text-[13px] font-semibold text-ink">{track.label}</h2><p className="truncate font-mono text-[10px] text-muted">{track.sourcePath}</p></div>
          {styleSupported && <button type="button" onClick={() => setEditingExisting(true)} className="glass-control motion-press flex h-8 items-center gap-1.5 rounded-comfortable px-3 text-[11px] font-medium text-muted hover:text-brand"><Scissors className="h-3.5 w-3.5" /> 修改素材设置</button>}
        </header>
        <ItemGrid track={track} projectRoot={projectRoot} onSelection={onSelection} selectionDisabled={selectionDisabled} />
      </section>
    )
  }

  const save = async () => {
    setSaving(true)
    const saved = await onSave(track.id, {
      ...(styleSupported ? { trim, extraction: { framesPerSecond, frameLimit, styleLutPath, colorLutPreset } } : {}),
      ...(track.type === 'ordinary_video' ? { cameraProfile } : {}),
    })
    setSaving(false)
    if (saved) setEditingExisting(false)
  }

  return (
    <section className="liquid-panel min-h-0 overflow-y-auto p-4">
      <header className="mb-4 border-b border-[var(--xp-line)] pb-3">
        <h2 className="truncate text-[14px] font-semibold text-ink">{track.label}</h2>
        <p className="mt-1 truncate font-mono text-[10px] text-muted" title={track.sourcePath}>{track.sourcePath}</p>
      </header>
      {video ? (
        <div className="space-y-4">
          <VideoTrimmer path={track.sourcePath} trim={trim ?? undefined} onChange={(next) => {
            setTrim(next)
            if (frameLimit > 0) setFramesPerSecond(framesPerSecondForLimit(next.end - next.start, frameLimit, framesPerSecond))
          }} />
          <div className="grid grid-cols-2 gap-3">
            <label className="text-[11px] font-medium text-muted">帧 / 秒<input type="number" min="0.01" step="0.1" value={framesPerSecond} onChange={(event) => setFramesPerSecond(Math.max(0.01, Number(event.target.value) || 0.01))} className="theme-input mt-1.5 h-10 w-full rounded-comfortable border px-3 font-mono text-[12px] text-ink outline-none" /></label>
            <label className="text-[11px] font-medium text-muted">帧数上限<input type="number" min="0" step="1" value={frameLimit} onChange={(event) => {
              const next = Math.max(0, Math.floor(Number(event.target.value) || 0))
              setFrameLimit(next)
              if (next > 0 && trim) setFramesPerSecond(framesPerSecondForLimit(trim.end - trim.start, next, framesPerSecond))
            }} className="theme-input mt-1.5 h-10 w-full rounded-comfortable border px-3 font-mono text-[12px] text-ink outline-none" /></label>
          </div>
          {builtinColorLutPresetForSource(track.type, track.sourcePath) && (
            <ColorLutField
              value={null}
              preset={colorLutPreset}
              builtinPreset={builtinColorLutPresetForSource(track.type, track.sourcePath)}
              onChange={() => {}}
              onPresetChange={setColorLutPreset}
              disabled={saving}
            />
          )}
          <ColorLutField value={styleLutPath} onChange={setStyleLutPath} disabled={saving} />
          {track.type === 'ordinary_video' && (
            <div><p className="mb-2 text-[11px] font-medium text-muted">初始相机视角</p><div className="grid grid-cols-2 gap-2">{(['wide', 'standard'] as const).map((profile) => <button key={profile} type="button" onClick={() => setCameraProfile(profile)} className={`motion-press h-10 rounded-comfortable border text-[12px] font-medium ${cameraProfile === profile ? 'border-brand bg-brand text-white' : 'border-[var(--xp-line)] text-muted hover:text-ink'}`}>{profile === 'wide' ? '广角视角' : '标准视角'}</button>)}</div></div>
          )}
          <div className="flex justify-end gap-2">
            {editingExisting && <button type="button" onClick={cancelEditing} disabled={saving} className="glass-control motion-press flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-muted hover:text-ink disabled:opacity-45"><X className="h-3.5 w-3.5" /> 取消</button>}
            <button type="button" onClick={save} disabled={saving} className="glass-control motion-press flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/75 hover:text-brand disabled:opacity-45"><Save className="h-3.5 w-3.5" /> {saving ? '保存中' : '保存参数'}</button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <PhotoFolderPreview path={track.sourcePath} />
          <ColorLutField value={styleLutPath} onChange={setStyleLutPath} disabled={saving} />
          <div className="flex justify-end gap-2">
            {editingExisting && <button type="button" onClick={cancelEditing} disabled={saving} className="glass-control motion-press flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-muted hover:text-ink disabled:opacity-45"><X className="h-3.5 w-3.5" /> 取消</button>}
            <button type="button" onClick={save} disabled={saving} className="glass-control motion-press flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/75 hover:text-brand disabled:opacity-45"><Save className="h-3.5 w-3.5" /> {saving ? '保存中' : '保存参数'}</button>
          </div>
        </div>
      )}
    </section>
  )
}
