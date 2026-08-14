import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, FileWarning, Image, Plane, Video, X } from 'lucide-react'
import { VideoTrimmer } from '../../components/pipeline/VideoTrimmer'
import type { ProjectTrackType } from '../../lib/contracts'
import { framesPerSecondForLimit } from '../../lib/extractionRate'
import { allowedTrackTypes, isDraftValid, type MediaImportDraft } from './mediaTypes'
import { ColorLutField } from './ColorLutField'
import { builtinColorLutPresetForSource } from './colorLut'
import { PhotoFolderPreview } from './PhotoFolderPreview'

interface MaterialImportDialogProps {
  drafts: MediaImportDraft[]
  onChange: (drafts: MediaImportDraft[]) => void
  onCancel: () => void
  onConfirm: () => void
  busy?: boolean
  error?: string
}

const typeOptions: Array<{ type: ProjectTrackType; label: string; icon: typeof Video }> = [
  { type: 'panoramic_video', label: '全景视频', icon: Video },
  { type: 'ordinary_video', label: '普通视频', icon: Video },
  { type: 'standard_photos', label: '标准照片', icon: Image },
  { type: 'aerial_photos', label: '航拍照片', icon: Plane },
]

function selectedDuration(draft: MediaImportDraft) {
  if (draft.trim) return Math.max(0, draft.trim.end - draft.trim.start)
  return Math.max(0, draft.duration)
}

export function MaterialImportDialog({ drafts, onChange, onCancel, onConfirm, busy = false, error = '' }: MaterialImportDialogProps) {
  const [selectedId, setSelectedId] = useState(drafts[0]?.id ?? '')
  const selected = drafts.find((draft) => draft.id === selectedId) ?? drafts[0]
  const validCount = drafts.filter(isDraftValid).length

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [busy, onCancel])

  const allowed = useMemo(() => selected ? allowedTrackTypes(selected.info) : [], [selected])

  const update = (id: string, change: Partial<MediaImportDraft>) => {
    onChange(drafts.map((draft) => draft.id === id ? { ...draft, ...change } : draft))
  }

  const updateTrim = (trim: { start: number; end: number }) => {
    if (!selected) return
    const duration = Math.max(0, trim.end - trim.start)
    const frameLimit = selected.extraction.frameLimit
    update(selected.id, {
      trim,
      extraction: frameLimit > 0 && duration > 0
        ? { ...selected.extraction, framesPerSecond: framesPerSecondForLimit(duration, frameLimit, selected.extraction.framesPerSecond) }
        : selected.extraction,
    })
  }

  const updateFrameLimit = (value: number) => {
    if (!selected) return
    const frameLimit = Math.max(0, Math.floor(value || 0))
    const duration = selectedDuration(selected)
    update(selected.id, {
      extraction: {
        ...selected.extraction,
        frameLimit,
        framesPerSecond: framesPerSecondForLimit(duration, frameLimit, selected.extraction.framesPerSecond),
      },
    })
  }

  return createPortal(
    <div className="app-modal-backdrop fixed inset-0 z-[110] grid place-items-center p-6" onMouseDown={() => { if (!busy) onCancel() }}>
      <section className="app-modal-panel flex max-h-[calc(100vh-48px)] w-full max-w-[960px] flex-col overflow-hidden p-0" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="导入素材">
        <header className="app-modal-header flex h-14 shrink-0 items-center justify-between px-5">
          <div>
            <h2 className="text-[15px] font-semibold text-ink">导入 {drafts.length} 项素材</h2>
            <p className="mt-0.5 text-[11px] text-muted">{validCount} 项已完成配置</p>
          </div>
          <button type="button" onClick={onCancel} disabled={busy} className="motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:bg-ink/[0.05] hover:text-ink disabled:opacity-40" aria-label="关闭导入">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] overflow-hidden">
          <aside className="min-h-0 overflow-y-auto border-r border-[var(--xp-line)] p-3">
            <div className="space-y-1.5">
              {drafts.map((draft) => {
                const valid = isDraftValid(draft)
                return (
                  <button key={draft.id} type="button" onClick={() => setSelectedId(draft.id)} className={`flex w-full items-center gap-2.5 rounded-comfortable border px-2.5 py-2.5 text-left transition-colors ${selected?.id === draft.id ? 'border-brand/30 bg-brand/[0.08]' : 'border-transparent hover:bg-ink/[0.035]'}`}>
                    <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-comfortable ${valid ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'}`}>
                      {valid ? <CheckCircle2 className="h-4 w-4" /> : <FileWarning className="h-4 w-4" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] font-medium text-ink">{draft.label || draft.info.name}</span>
                      <span className={`mt-0.5 block truncate text-[10px] ${valid ? 'text-muted' : 'text-danger'}`}>{draft.info.message}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </aside>

          <div className="min-h-0 overflow-y-auto p-5">
            {selected && (
              <div className="space-y-5">
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium text-muted">轨道名称</label>
                  <input value={selected.label} onChange={(event) => update(selected.id, { label: event.target.value })} className="theme-input h-10 w-full rounded-comfortable border px-3 text-[13px] outline-none" />
                  <p className="mt-1 truncate font-mono text-[10px] text-faint" title={selected.sourcePath}>{selected.sourcePath}</p>
                </div>

                <div>
                  <p className="mb-2 text-[11px] font-medium text-muted">素材类型</p>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {typeOptions.map((option) => {
                      const Icon = option.icon
                      const enabled = allowed.includes(option.type)
                      return (
                        <button key={option.type} type="button" disabled={!enabled} onClick={() => update(selected.id, {
                          trackType: option.type,
                          cameraProfile: option.type === 'ordinary_video' ? 'wide' : null,
                          extraction: {
                            ...selected.extraction,
                            colorLutPreset: builtinColorLutPresetForSource(option.type, selected.sourcePath)
                              ? selected.extraction.colorLutPreset
                              : null,
                          },
                        })} className={`motion-press flex h-10 items-center justify-center gap-1.5 rounded-comfortable border text-[11px] font-medium ${selected.trackType === option.type ? 'border-brand bg-brand text-white' : 'border-[var(--xp-line)] text-muted hover:text-ink'} disabled:cursor-not-allowed disabled:opacity-35`}>
                          <Icon className="h-3.5 w-3.5" /> {option.label}
                        </button>
                      )
                    })}
                  </div>
                </div>

                {(selected.trackType === 'panoramic_video' || selected.trackType === 'ordinary_video') && (
                  <>
                    <VideoTrimmer
                      path={selected.sourcePath}
                      trim={selected.trim ?? undefined}
                      onChange={updateTrim}
                      onDuration={(duration) => {
                        const trim = selected.trim ?? (duration > 0 ? { start: 0, end: duration } : null)
                        const range = trim ? trim.end - trim.start : duration
                        update(selected.id, {
                          duration,
                          trim,
                          extraction: selected.extraction.frameLimit > 0 && range > 0
                            ? { ...selected.extraction, framesPerSecond: framesPerSecondForLimit(range, selected.extraction.frameLimit, selected.extraction.framesPerSecond) }
                            : selected.extraction,
                        })
                      }}
                    />
                    <div className="grid grid-cols-2 gap-3">
                      <label className="text-[11px] font-medium text-muted">
                        帧 / 秒
                        <input type="number" min="0.01" step="0.1" value={selected.extraction.framesPerSecond} onChange={(event) => update(selected.id, { extraction: { ...selected.extraction, framesPerSecond: Math.max(0.01, Number(event.target.value) || 0.01) } })} className="theme-input mt-1.5 h-10 w-full rounded-comfortable border px-3 font-mono text-[12px] text-ink outline-none" />
                      </label>
                      <label className="text-[11px] font-medium text-muted">
                        帧数上限
                        <input type="number" min="0" step="1" value={selected.extraction.frameLimit} onChange={(event) => updateFrameLimit(Number(event.target.value))} className="theme-input mt-1.5 h-10 w-full rounded-comfortable border px-3 font-mono text-[12px] text-ink outline-none" />
                      </label>
                    </div>
                    {builtinColorLutPresetForSource(selected.trackType, selected.sourcePath) && (
                      <ColorLutField
                        value={null}
                        preset={selected.extraction.colorLutPreset}
                        builtinPreset={builtinColorLutPresetForSource(selected.trackType, selected.sourcePath)}
                        onChange={() => {}}
                        onPresetChange={(colorLutPreset) => update(selected.id, {
                          extraction: { ...selected.extraction, colorLutPreset },
                        })}
                        disabled={busy}
                      />
                    )}
                    <ColorLutField
                      value={selected.extraction.styleLutPath}
                      onChange={(styleLutPath) => update(selected.id, {
                        extraction: { ...selected.extraction, styleLutPath },
                      })}
                      disabled={busy}
                    />
                  </>
                )}

                {selected.trackType === 'ordinary_video' && (
                  <div>
                    <p className="mb-2 text-[11px] font-medium text-muted">初始相机视角</p>
                    <div className="grid grid-cols-2 gap-2">
                      {(['wide', 'standard'] as const).map((profile) => (
                        <button key={profile} type="button" onClick={() => update(selected.id, { cameraProfile: profile })} className={`motion-press h-10 rounded-comfortable border text-[12px] font-medium ${selected.cameraProfile === profile ? 'border-brand bg-brand text-white' : 'border-[var(--xp-line)] text-muted hover:text-ink'}`}>
                          {profile === 'wide' ? '广角视角' : '标准视角'}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {(selected.trackType === 'standard_photos' || selected.trackType === 'aerial_photos') && (
                  <>
                    <PhotoFolderPreview path={selected.sourcePath} compact initialPaths={selected.info.previewPaths} initialTotal={selected.info.photoCount} />
                    <ColorLutField
                      value={selected.extraction.styleLutPath}
                      onChange={(styleLutPath) => update(selected.id, {
                        extraction: { ...selected.extraction, styleLutPath },
                      })}
                      disabled={busy}
                    />
                  </>
                )}

                {!selected.info.valid && (
                  <div className="rounded-comfortable border border-danger/20 bg-danger/8 p-3 text-[12px] text-danger">{selected.info.message}</div>
                )}
              </div>
            )}
          </div>
        </div>

        {error && <div role="alert" className="shrink-0 border-t border-danger/20 bg-danger/[0.07] px-5 py-2 text-[11px] text-danger">导入失败：{error}</div>}

        <footer className="app-modal-footer flex h-14 shrink-0 items-center justify-between px-5">
          <span className="text-[11px] text-muted">{validCount} 项有效 / {drafts.length - validCount} 项不可导入</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onCancel} disabled={busy} className="glass-control motion-press h-9 rounded-comfortable px-4 text-[12px] font-medium text-muted hover:text-ink disabled:opacity-40">取消</button>
            <button type="button" onClick={onConfirm} disabled={validCount === 0 || busy} className="motion-press h-9 min-w-28 rounded-comfortable bg-brand px-4 text-[12px] font-semibold text-white shadow-sm shadow-brand/20 disabled:cursor-not-allowed disabled:opacity-40">{busy ? '正在导入…' : '导入有效素材'}</button>
          </div>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
