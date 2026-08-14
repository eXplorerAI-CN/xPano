import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { Scissors } from 'lucide-react'
import { assetSource } from '../../lib/assetSource'

interface VideoTrimmerProps {
  path: string
  trim?: { start: number; end: number }
  onChange: (trim: { start: number; end: number }) => void
  onDuration?: (duration: number) => void
}

interface Thumb {
  front: string
  back: string
}

const MIN_SELECTION = 0.5

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds)) return '00:00.0'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const tenths = Math.floor((seconds % 1) * 10)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${tenths}`
}

/**
 * Panoramic video clip selector with a progressive thumbnail strip.
 *
 * 4K HEVC sources take ~1s per random-access frame extraction, so instead of
 * extracting on every scrub we pre-generate a sparse thumbnail strip in the
 * background (one frame every few seconds). The timeline shows whatever
 * thumbnails are ready; dragging a handle moves instantly and previews the
 * nearest available thumbnail. On release we do one precise frame extraction
 * at the handle position so the displayed frame matches the selection exactly.
 */
export function VideoTrimmer({ path, trim, onChange, onDuration }: VideoTrimmerProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [duration, setDuration] = useState(0)
  const [dragging, setDragging] = useState<'start' | 'end' | 'scrub' | null>(null)
  // Pre-generated thumbnails keyed by their time stamp.
  const [thumbs, setThumbs] = useState<Map<number, Thumb>>(new Map())
  // The currently displayed preview frame (front/back), from either a thumbnail
  // or a precise on-demand extraction.
  const [frontFrame, setFrontFrame] = useState('')
  const [backFrame, setBackFrame] = useState('')
  const [frameLoading, setFrameLoading] = useState(false)
  const [thumbProgress, setThumbProgress] = useState(0)
  const [previewTime, setPreviewTime] = useState(0)
  const debounceRef = useRef<number>(0)
  const tokenRef = useRef(0)
  // Mirror previewTime so the pointerup handler reads the latest value.
  const previewTimeRef = useRef(0)

  const start = trim?.start ?? 0
  const end = trim?.end ?? duration
  const boundsRef = useRef({ start: 0, end: 0, duration: 0 })
  boundsRef.current = { start, end, duration }

  const tauri = isTauriRuntime()

  // Probe duration, default the selection, kick off background thumbgen.
  useEffect(() => {
    if (!tauri) return
    setThumbs(new Map())
    setFrontFrame('')
    setBackFrame('')
    setThumbProgress(0)
    let cancelled = false
    invoke<number>('probe_video_duration', { path })
      .then((d) => {
        if (cancelled) return
        setDuration(d)
        onDuration?.(d)
        if (d > 0) {
          // Pre-generate thumbnails every ~3% of the video, capped at 40 frames.
          const interval = Math.max(2, d / 40)
          const count = Math.min(40, Math.ceil(d / interval) + 1)
          invoke('start_thumbgen', { path, from: 0, interval, count })
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tauri])

  // Listen for thumbnails arriving from the background batch.
  useEffect(() => {
    if (!tauri) return
    let mounted = true
    let unlisten: UnlistenFn | undefined
    listen<{ time: number; front: string; back: string }>('thumbgen:frame', (event) => {
      if (!mounted) return
      const { time, front, back } = event.payload
      setThumbs((prev) => {
        const next = new Map(prev)
        next.set(time, { front: assetSource(front), back: back ? assetSource(back) : '' })
        return next
      })
      setThumbProgress((p) => p + 1)
      // Show the first thumbnail as the initial preview.
      setFrontFrame((cur) => cur || assetSource(front))
      setBackFrame((cur) => cur || (back ? assetSource(back) : ''))
    }).then((fn) => {
      if (!mounted) {
        fn()
        return
      }
      unlisten = fn
    })
    return () => {
      mounted = false
      unlisten?.()
      // Cancel any in-flight batch when unmounting / switching videos.
      invoke('stop_thumbgen').catch(() => {})
    }
  }, [path, tauri])

  useEffect(() => {
    tokenRef.current += 1
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current)
      debounceRef.current = 0
    }
    setFrameLoading(false)
    return () => {
      tokenRef.current += 1
      if (debounceRef.current) {
        window.clearTimeout(debounceRef.current)
        debounceRef.current = 0
      }
    }
  }, [path])

  const pct = (t: number) => (duration > 0 ? Math.min(100, Math.max(0, (t / duration) * 100)) : 0)

  const timeFromClientX = (clientX: number) => {
    const el = trackRef.current
    if (!el || duration === 0) return 0
    const rect = el.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
    return ratio * duration
  }

  // Show the nearest pre-generated thumbnail for a given time (0 latency).
  const showNearestThumb = (time: number) => {
    if (thumbs.size === 0) return
    let best: { t: number; thumb: Thumb } | null = null
    for (const [t, thumb] of thumbs) {
      if (!best || Math.abs(t - time) < Math.abs(best.t - time)) best = { t, thumb }
    }
    if (best) {
      setFrontFrame(best.thumb.front)
      setBackFrame(best.thumb.back)
    }
  }

  // Precise on-demand extraction (debounced + token-guarded). Used on release.
  const fetchFrame = (time: number) => {
    if (!tauri || duration === 0) return
    if (debounceRef.current) window.clearTimeout(debounceRef.current)
    const myToken = ++tokenRef.current
    setFrameLoading(true)
    debounceRef.current = window.setTimeout(() => {
      invoke<string[]>('extract_pano_frame', { path, time })
        .then((res) => {
          if (myToken !== tokenRef.current) return
          setFrontFrame(res[0] ? assetSource(res[0]) : '')
          setBackFrame(res[1] ? assetSource(res[1]) : '')
        })
        .catch(() => {})
        .finally(() => { if (myToken === tokenRef.current) setFrameLoading(false) })
    }, 200)
  }

  const beginDrag = (which: 'start' | 'end') => (event: React.PointerEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setDragging(which)
  }

  // Drag: move the handle visually + show the nearest thumbnail (0 latency).
  // The precise frame is fetched once on release. 'scrub' mode (dragging the
  // track body) only moves the playhead/preview — it doesn't change the selection.
  useEffect(() => {
    if (!dragging) return
    const onMove = (event: PointerEvent) => {
      const { start: s, end: e, duration: d } = boundsRef.current
      const t = timeFromClientX(event.clientX)
      if (dragging === 'scrub') {
        setPreviewTime(t)
        showNearestThumb(t)
        return
      }
      if (dragging === 'start') {
        const clamped = Math.max(0, Math.min(t, e - MIN_SELECTION))
        onChange({ start: clamped, end: e })
        setPreviewTime(clamped)
        showNearestThumb(clamped)
      } else {
        const clamped = Math.min(d, Math.max(t, s + MIN_SELECTION))
        onChange({ start: s, end: clamped })
        setPreviewTime(clamped)
        showNearestThumb(clamped)
      }
    }
    const onUp = () => {
      if (dragging === 'scrub') {
        // On release, fetch a precise frame at the final scrub position.
        fetchFrame(previewTimeRef.current)
      } else {
        const { start: s, end: e } = boundsRef.current
        fetchFrame(dragging === 'start' ? s : e)
      }
      setDragging(null)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging, thumbs])

  // Pressing the track body starts a scrub: the playhead follows the cursor
  // (showing the nearest thumbnail) until release, then we fetch a precise frame.
  const onTrackPointerDown = (event: React.PointerEvent) => {
    if (dragging) return
    const t = timeFromClientX(event.clientX)
    previewTimeRef.current = t
    setPreviewTime(t)
    showNearestThumb(t)
    setDragging('scrub')
  }

  // Keep the ref in sync as previewTime changes.
  useEffect(() => { previewTimeRef.current = previewTime }, [previewTime])

  const selectionDuration = end - start
  const hasBack = Boolean(backFrame)
  // Sorted thumbnail times for rendering the strip.
  const thumbTimes = Array.from(thumbs.keys()).sort((a, b) => a - b)

  return (
    <div className="min-w-0 space-y-2 overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-[11px] font-medium text-muted">拖动把手选择片段，点击轨道预览</span>
        <span className="rounded-full border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-0.5 font-mono text-[11px] text-muted">
          {frameLoading ? '精确定位中…' : thumbProgress >= 40 ? '已完成' : thumbProgress > 0 ? `${thumbProgress}/40` : '准备中'}
        </span>
      </div>

      {/* Frame preview — front/back lenses side by side */}
      <div
        className={`relative mx-auto grid w-full overflow-hidden rounded-card bg-black/50 ${hasBack ? 'grid-cols-2' : 'grid-cols-1'}`}
        style={{ minHeight: '96px' }}
      >
        {frontFrame ? (
          <>
            <div className="relative flex items-center justify-center">
              <img src={frontFrame} alt="前镜头" className="max-h-[168px] w-full object-contain" draggable={false} onError={() => setFrontFrame('')} />
              <span className="pointer-events-none absolute left-1.5 top-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-milk/80">前</span>
            </div>
            {hasBack && (
              <div className="relative flex items-center justify-center">
                <img src={backFrame} alt="后镜头" className="max-h-[168px] w-full object-contain" draggable={false} onError={() => setBackFrame('')} />
                <span className="pointer-events-none absolute right-1.5 top-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-milk/80">后</span>
              </div>
            )}
          </>
        ) : (
          <div className="grid h-24 w-full place-items-center text-muted">
            <div className="text-center">
              <Scissors className="mx-auto mb-2 h-6 w-6" />
              <p className="text-[12px]">{tauri ? '正在生成缩略图…' : '需在桌面应用中预览视频'}</p>
            </div>
          </div>
        )}
        {frontFrame && (
          <span className="pointer-events-none absolute bottom-1.5 left-1/2 -translate-x-1/2 rounded bg-black/55 px-2 py-0.5 font-mono text-[10px] text-milk/80">
            {formatTime(previewTime)}
          </span>
        )}
      </div>

      {/* Timeline: thumbnail strip + dual handles */}
      <div>
        <div
          ref={trackRef}
          onPointerDown={onTrackPointerDown}
          className="relative h-9 cursor-pointer select-none overflow-hidden rounded-comfortable border border-[var(--xp-line)] bg-[var(--xp-inset)]"
          title="按住拖动预览，拖动把手选择片段"
        >
          {/* Thumbnail strip — each generated thumbnail sits at its time position */}
          {thumbTimes.map((t) => {
            const th = thumbs.get(t)!
            return (
              <img
                key={t}
                src={th.front}
                alt=""
                className="pointer-events-none absolute inset-y-0 h-full opacity-60"
                style={{ left: `${pct(t)}%`, width: `${100 / Math.max(thumbTimes.length, 1)}%` }}
                draggable={false}
              />
            )
          })}
          {/* Selected region */}
          <div
            className="pointer-events-none absolute inset-y-0 bg-brand/20 ring-1 ring-inset ring-brand/50"
            style={{ left: `${pct(start)}%`, width: `${pct(end) - pct(start)}%` }}
          />
          {/* Preview playhead */}
          <div
            className="pointer-events-none absolute inset-y-0 w-0.5 bg-data"
            style={{ left: `${pct(previewTime)}%` }}
          />
          {/* Start handle */}
          <div
            onPointerDown={beginDrag('start')}
            className="absolute inset-y-0 z-10 flex w-4 -translate-x-1/2 cursor-ew-resize items-center justify-center"
            style={{ left: `${pct(start)}%` }}
          >
            <span className="h-6 w-3 rounded-full bg-brand shadow-[0_0_0_2px_var(--xp-highlight)]" />
          </div>
          {/* End handle */}
          <div
            onPointerDown={beginDrag('end')}
            className="absolute inset-y-0 z-10 flex w-4 -translate-x-1/2 cursor-ew-resize items-center justify-center"
            style={{ left: `${pct(end)}%` }}
          >
            <span className="h-6 w-3 rounded-full bg-brand shadow-[0_0_0_2px_var(--xp-highlight)]" />
          </div>
        </div>

        {/* Numeric readout */}
        <div className="mt-1.5 flex items-center justify-between font-mono text-[11px] text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-brand" />
            {formatTime(start)}
          </span>
          <span className="rounded-full border border-[var(--xp-line)] bg-[var(--xp-control)] px-2 py-0.5 text-brand">
            片段时长 {formatTime(selectionDuration)}
          </span>
          <span className="inline-flex items-center gap-1.5">
            {formatTime(end)}
            <span className="h-1.5 w-1.5 rounded-full bg-brand" />
          </span>
        </div>
      </div>
    </div>
  )
}
