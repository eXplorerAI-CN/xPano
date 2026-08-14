import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { ImageOff, Images, LoaderCircle } from 'lucide-react'
import { assetSource } from '../../lib/assetSource'
import { initialPhotoPreviewCount, nextPhotoPreviewCount, PHOTO_PREVIEW_BATCH_SIZE } from './photoPreviewProgress'

interface PhotoPreviewResult {
  total: number
  paths: string[]
}

interface PhotoFolderPreviewProps {
  path: string
  compact?: boolean
  initialPaths?: string[]
  initialTotal?: number
}

function LazyPhotoPreview({ source }: { source: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [requested, setRequested] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setRequested(false)
    setFailed(false)
  }, [source])

  useEffect(() => {
    if (requested) return
    const node = containerRef.current
    if (!node || typeof IntersectionObserver === 'undefined') {
      setRequested(true)
      return
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      setRequested(true)
      observer.disconnect()
    }, { threshold: 0.01 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [requested])

  const resolvedSource = requested ? assetSource(source) : ''
  return (
    <div ref={containerRef} className="relative aspect-[4/3] min-w-0 overflow-hidden rounded-comfortable border border-[var(--xp-line)] bg-[var(--xp-inset)]">
      {!requested ? (
        <span className="block h-full w-full animate-pulse bg-ink/[0.035]" aria-label="等待进入视口" />
      ) : failed || !resolvedSource ? (
        <span className="grid h-full place-items-center"><ImageOff className="h-5 w-5 text-muted" /></span>
      ) : (
        <img
          src={resolvedSource}
          alt="照片预览"
          className="h-full w-full object-cover"
          decoding="async"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}

export function PhotoFolderPreview({ path, compact = false, initialPaths, initialTotal }: PhotoFolderPreviewProps) {
  const [result, setResult] = useState<PhotoPreviewResult | null>(null)
  const [visibleCount, setVisibleCount] = useState(0)
  const [error, setError] = useState('')
  const loadMoreRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let disposed = false
    setError('')
    if (initialPaths?.length) {
      const preview = { total: initialTotal ?? initialPaths.length, paths: initialPaths }
      setResult(preview)
      setVisibleCount(initialPhotoPreviewCount(preview.paths.length))
      return () => { disposed = true }
    }
    setResult(null)
    setVisibleCount(0)
    invoke<PhotoPreviewResult>('preview_photo_folder', { path })
      .then((preview) => {
        if (!disposed) {
          setResult(preview)
          setVisibleCount(initialPhotoPreviewCount(preview.paths.length))
        }
      })
      .catch((reason) => {
        if (!disposed) setError(String(reason))
      })
    return () => { disposed = true }
  }, [initialPaths, initialTotal, path])

  const totalPaths = result?.paths.length ?? 0
  const hasMore = visibleCount < totalPaths
  useEffect(() => {
    if (!hasMore) return
    if (typeof IntersectionObserver === 'undefined') {
      setVisibleCount(totalPaths)
      return
    }
    const node = loadMoreRef.current
    if (!node) return
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      setVisibleCount((current) => nextPhotoPreviewCount(current, totalPaths, PHOTO_PREVIEW_BATCH_SIZE))
    }, { threshold: 0.01 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [hasMore, totalPaths, visibleCount])

  if (error) {
    return <div className="grid min-h-32 place-items-center rounded-comfortable border border-[var(--xp-line)] text-center text-[11px] text-muted"><span><ImageOff className="mx-auto mb-2 h-5 w-5" />快速预览不可用<br /><span className="text-[10px] text-faint">{error}</span></span></div>
  }

  if (!result) {
    return <div className="grid min-h-32 place-items-center rounded-comfortable border border-[var(--xp-line)] text-[11px] text-muted"><span className="inline-flex items-center gap-2"><LoaderCircle className="h-4 w-4 animate-spin" />正在索引照片路径</span></div>
  }

  const visiblePaths = result.paths.slice(0, visibleCount)

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3 text-[10px] text-muted">
        <span className="inline-flex items-center gap-1.5"><Images className="h-3.5 w-3.5" />渐进照片预览</span>
        <span className="font-mono">已加入 {visiblePaths.length} / 共 {result.total} 张</span>
      </div>
      <div className={`grid gap-1.5 ${compact ? 'grid-cols-4' : 'grid-cols-[repeat(auto-fill,minmax(118px,1fr))]'}`}>
        {visiblePaths.map((source) => <LazyPhotoPreview key={source} source={source} />)}
      </div>
      {hasMore && <div ref={loadMoreRef} className="grid h-12 place-items-center text-[10px] text-muted"><span className="inline-flex items-center gap-1.5"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />向下滚动继续加载</span></div>}
    </div>
  )
}
