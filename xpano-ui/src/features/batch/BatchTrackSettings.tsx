import { useEffect, useState } from 'react'
import { ChevronDown, Save, Settings2, Trash2 } from 'lucide-react'
import { VideoTrimmer } from '../../components/pipeline/VideoTrimmer'
import { framesPerSecondForLimit } from '../../lib/extractionRate'
import type { ProjectTrack, TrackSettingsPatch } from '../../lib/contracts'
import { ColorLutField } from '../media/ColorLutField'
import { builtinColorLutPresetForSource, isStyleLutSupported } from '../media/colorLut'

interface Props {
  track: ProjectTrack
  selected: boolean
  disabled: boolean
  onSelectedChange: () => void
  onSave: (trackId: string, patch: TrackSettingsPatch) => Promise<boolean>
  onRemove: (track: ProjectTrack) => void
}

export function BatchTrackSettings({ track, selected, disabled, onSelectedChange, onSave, onRemove }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [trim, setTrim] = useState(track.trim)
  const [framesPerSecond, setFramesPerSecond] = useState(track.extraction.framesPerSecond)
  const [frameLimit, setFrameLimit] = useState(track.extraction.frameLimit)
  const [styleLutPath, setStyleLutPath] = useState<string | null>(track.extraction.styleLutPath ?? null)
  const [colorLutPreset, setColorLutPreset] = useState<string | null>(track.extraction.colorLutPreset ?? null)
  const [cameraProfile, setCameraProfile] = useState<'wide' | 'standard'>(track.cameraProfile === 'standard' ? 'standard' : 'wide')

  useEffect(() => {
    setTrim(track.trim)
    setFramesPerSecond(track.extraction.framesPerSecond)
    setFrameLimit(track.extraction.frameLimit)
    setStyleLutPath(track.extraction.styleLutPath ?? null)
    setColorLutPreset(track.extraction.colorLutPreset ?? null)
    setCameraProfile(track.cameraProfile === 'standard' ? 'standard' : 'wide')
  }, [track])

  const video = track.type === 'panoramic_video' || track.type === 'ordinary_video'
  const styleSupported = isStyleLutSupported(track.type)
  const builtinPreset = builtinColorLutPresetForSource(track.type, track.sourcePath)

  const save = async () => {
    setSaving(true)
    const saved = await onSave(track.id, {
      ...(styleSupported ? {
        trim,
        extraction: { framesPerSecond, frameLimit, styleLutPath, colorLutPreset },
      } : {}),
      ...(track.type === 'ordinary_video' ? { cameraProfile } : {}),
    })
    setSaving(false)
    if (saved) setExpanded(false)
  }

  return (
    <article className="overflow-hidden rounded-subtle border border-ink/[0.07] bg-ink/[0.018]">
      <div className="flex min-h-12 items-center gap-2 px-3 py-2">
        <input
          type="checkbox"
          disabled={disabled}
          checked={selected}
          onChange={onSelectedChange}
          className="accent-brand"
          aria-label={`将 ${track.label} 加入任务`}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-medium">{track.label}</span>
          <span className="mt-0.5 block truncate text-[9px] text-muted" title={track.sourcePath}>{track.sourcePath}</span>
        </span>
        <span className="hidden shrink-0 font-mono text-[9px] text-muted sm:block">
          {video ? `${framesPerSecond} FPS · ${frameLimit > 0 ? `${frameLimit} 帧` : '不限帧数'}` : '照片轨道'}
        </span>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setExpanded((value) => !value)}
          className="glass-control motion-press grid h-7 w-7 place-items-center rounded-subtle text-muted hover:text-brand disabled:opacity-40"
          aria-label={`${expanded ? '收起' : '设置'} ${track.label}`}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="h-3.5 w-3.5 rotate-180" /> : <Settings2 className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onRemove(track)}
          className="glass-control motion-press grid h-7 w-7 place-items-center rounded-subtle text-muted hover:text-danger disabled:opacity-40"
          aria-label={`删除 ${track.label}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {expanded && (
        <div className="border-t border-ink/[0.07] px-3 py-3">
          {video && (
            <>
              <VideoTrimmer path={track.sourcePath} trim={trim ?? undefined} onChange={(next) => {
                setTrim(next)
                if (frameLimit > 0) {
                  setFramesPerSecond(framesPerSecondForLimit(next.end - next.start, frameLimit, framesPerSecond))
                }
              }} />
              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className="text-[10px] text-muted">帧 / 秒<input type="number" min="0.01" step="0.1" value={framesPerSecond} onChange={(event) => setFramesPerSecond(Math.max(0.01, Number(event.target.value) || 0.01))} className="theme-input batch-form-control mt-1 w-full" /></label>
                <label className="text-[10px] text-muted">帧数上限<input type="number" min="0" step="1" value={frameLimit} onChange={(event) => setFrameLimit(Math.max(0, Math.floor(Number(event.target.value) || 0)))} className="theme-input batch-form-control mt-1 w-full" /></label>
              </div>
            </>
          )}
          {builtinPreset && (
            <div className="mt-3">
              <ColorLutField value={null} preset={colorLutPreset} builtinPreset={builtinPreset} onChange={() => {}} onPresetChange={setColorLutPreset} disabled={saving} />
            </div>
          )}
          {styleSupported && <div className="mt-3"><ColorLutField value={styleLutPath} onChange={setStyleLutPath} disabled={saving} /></div>}
          {track.type === 'ordinary_video' && (
            <label className="mt-3 block text-[10px] text-muted">初始相机视角<select value={cameraProfile} onChange={(event) => setCameraProfile(event.target.value as 'wide' | 'standard')} className="theme-input batch-form-control mt-1 w-full"><option value="wide">广角视角</option><option value="standard">标准视角</option></select></label>
          )}
          <div className="mt-3 flex justify-end">
            <button type="button" disabled={saving} onClick={save} className="motion-press flex h-8 items-center gap-1.5 rounded-comfortable bg-brand px-3 text-[10px] font-semibold text-white disabled:opacity-45"><Save className="h-3 w-3" />{saving ? '保存中…' : '保存轨道参数'}</button>
          </div>
        </div>
      )}
    </article>
  )
}
