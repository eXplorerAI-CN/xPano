import { useMemo } from 'react'
import { FilePlus2, FolderPlus, Image, Plane, Trash2, Video } from 'lucide-react'
import type { ProjectTrack } from '../../lib/contracts'

interface TrackListProps {
  tracks: ProjectTrack[]
  selectedId: string
  missingIds: string[]
  onSelect: (id: string) => void
  onAddFiles: () => void
  onAddFolder: () => void
  onRemove: (track: ProjectTrack) => void
  activeTrackId?: string
  activePercent?: number
  activeCount?: string
  activeEta?: string
  editingDisabled?: boolean
  editingDisabledReason?: string
}

const statusMeta = {
  draft: { label: '准备处理', className: 'text-brand' },
  prepared: { label: '已准备', className: 'text-success' },
  running: { label: '处理中', className: 'text-data' },
  ready: { label: '已完成', className: 'text-success' },
  stale: { label: '需重新处理', className: 'text-warning' },
  missing: { label: '源文件缺失', className: 'text-danger' },
  failed: { label: '处理失败', className: 'text-danger' },
  interrupted: { label: '任务中断', className: 'text-warning' },
} as const

function trackIcon(track: ProjectTrack) {
  if (track.type === 'aerial_photos') return Plane
  if (track.type === 'standard_photos') return Image
  return Video
}

export function TrackList({ tracks, selectedId, missingIds, onSelect, onAddFiles, onAddFolder, onRemove, activeTrackId, activePercent = 0, activeCount, activeEta, editingDisabled = false, editingDisabledReason }: TrackListProps) {
  const selectedCounts = useMemo(() => new Map(tracks.map((track) => [track.id, track.items.filter((item) => item.selected).length])), [tracks])
  return (
    <aside className="liquid-panel flex min-h-0 flex-col overflow-hidden p-0">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-3">
        <div>
          <h1 className="text-[13px] font-semibold text-ink">素材轨道</h1>
          <p className="text-[10px] text-muted">{tracks.length} 条轨道</p>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" disabled={editingDisabled} onClick={onAddFiles} className="glass-control motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:text-brand disabled:cursor-not-allowed disabled:opacity-40" title={editingDisabled ? editingDisabledReason : '添加文件'} aria-label="添加文件"><FilePlus2 className="h-4 w-4" /></button>
          <button type="button" disabled={editingDisabled} onClick={onAddFolder} className="glass-control motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:text-brand disabled:cursor-not-allowed disabled:opacity-40" title={editingDisabled ? editingDisabledReason : '添加照片文件夹'} aria-label="添加照片文件夹"><FolderPlus className="h-4 w-4" /></button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {tracks.length === 0 ? (
          <button type="button" disabled={editingDisabled} onClick={onAddFiles} title={editingDisabled ? editingDisabledReason : undefined} className="grid h-full min-h-56 w-full place-items-center rounded-comfortable border border-dashed border-[var(--xp-line)] text-center text-muted hover:border-brand/35 hover:text-brand disabled:cursor-not-allowed disabled:opacity-45">
            <span>
              <FilePlus2 className="mx-auto h-6 w-6" />
              <span className="mt-2 block text-[12px] font-medium">添加或拖入素材</span>
            </span>
          </button>
        ) : (
          <div className="space-y-1.5">
            {tracks.map((track) => {
              const Icon = trackIcon(track)
              const missing = missingIds.includes(track.id)
              const status = missing ? { label: '源文件缺失', className: 'text-danger' } : statusMeta[track.status]
              const selectedCount = selectedCounts.get(track.id) ?? 0
              const active = activeTrackId === track.id
              return (
                <div key={track.id} className={`group flex items-stretch overflow-hidden rounded-comfortable border transition-colors ${selectedId === track.id ? 'border-brand/30 bg-brand/[0.08]' : 'border-transparent hover:bg-ink/[0.035]'}`}>
                  <button type="button" onClick={() => onSelect(track.id)} className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2.5 text-left">
                    <span className="icon-tile grid h-9 w-9 shrink-0 place-items-center rounded-comfortable"><Icon className="h-4 w-4" /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] font-medium text-ink">{track.label}</span>
                      {active ? (
                        <span className="mt-1 block">
                          <span className="flex items-center justify-between gap-2 text-[10px] text-data"><span>处理中 {Math.round(activePercent)}%</span><span className="font-mono">{activeCount || activeEta || ''}</span></span>
                          <span className="mt-1 block h-1 overflow-hidden rounded-full bg-ink/[0.08]"><span className="block h-full rounded-full bg-data transition-[width] duration-300" style={{ width: `${activePercent}%` }} /></span>
                        </span>
                      ) : (
                        <span className="mt-0.5 flex items-center justify-between gap-2 text-[10px]"><span className={status.className}>{status.label}</span><span className="font-mono text-muted">{selectedCount}/{track.items.length}</span></span>
                      )}
                    </span>
                  </button>
                  <button type="button" disabled={editingDisabled} onClick={() => onRemove(track)} className="motion-press m-1.5 grid w-8 shrink-0 place-items-center rounded-comfortable bg-danger/12 text-danger opacity-75 transition-opacity hover:bg-danger hover:text-white group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-30" title={editingDisabled ? editingDisabledReason : '移除轨道'} aria-label={`移除 ${track.label}`}><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}
