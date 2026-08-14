import { Box, ArrowLeft } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProject } from '../../app/useProject'
import { ViewerPage } from '../../components/viewer/ViewerPage'
import { joinDisplayPath } from '../../lib/paths'
import type { ResolvedTheme } from '../../lib/types'
import type { Matrix4, PointCloudVariant, XpanoProjectV2 } from '../../lib/contracts'
import { PointVariantPanel } from './PointVariantPanel'
import { reconcilePreviewVariantId, shouldMaterializeStandardVariant } from './pointVariants'

interface PointVariantPreviewResult {
  variant: PointCloudVariant
  canonicalPath: string
  worldFromCanonical: Matrix4
  transformRevision: number
}

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function ResultsWorkspace({ resolvedTheme, active = true }: { resolvedTheme: ResolvedTheme; active?: boolean }) {
  const navigate = useNavigate()
  const { project, projectRoot, viewerOnlyPath } = useProject()
  const [variants, setVariants] = useState<PointCloudVariant[]>([])
  const [previewVariantId, setPreviewVariantId] = useState<string | null>(null)
  const [preview, setPreview] = useState<PointVariantPreviewResult | null>(null)
  const [busyVariantId, setBusyVariantId] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const dataPath = viewerOnlyPath || (project?.reconstruction.colmapPath && projectRoot
    ? joinDisplayPath(projectRoot, project.reconstruction.colmapPath)
    : '')
  const viewerVariantPreview = useMemo(() => preview ? {
    id: preview.variant.id,
    pointsPath: preview.canonicalPath,
    worldFromCanonical: preview.worldFromCanonical,
  } : null, [preview])

  useEffect(() => {
    if (!project || !projectRoot || !isTauriRuntime()) {
      setVariants(project?.geometry.variants ?? [])
      return
    }
    let disposed = false
    const loadVariants = async () => {
      let next = await invoke<PointCloudVariant[]>('list_point_variants', { projectRoot })
      if (shouldMaterializeStandardVariant(next, project.reconstruction.status, project.geometry.activeVariantId)) {
        await invoke('materialize_standard_variant', { projectRoot })
        next = await invoke<PointCloudVariant[]>('list_point_variants', { projectRoot })
      }
      return next
    }
    loadVariants()
      .then((next) => {
        if (disposed) return
        setVariants(next)
        setPreviewVariantId((current) => reconcilePreviewVariantId(next, project.geometry.activeVariantId, current))
      })
      .catch(() => {
        if (!disposed) setVariants(project.geometry.variants)
      })
    return () => { disposed = true }
  }, [project, projectRoot])

  const previewVariant = async (variant: PointCloudVariant) => {
    if (!project || !projectRoot || busyVariantId) return
    if (variant.id === project.geometry.activeVariantId) {
      setPreview(null)
      setPreviewVariantId(variant.id)
      return
    }
    setBusyVariantId(variant.id)
    try {
      const result = await invoke<PointVariantPreviewResult>('preview_point_variant', { projectRoot, variantId: variant.id })
      setPreview(result)
      setPreviewVariantId(variant.id)
    } finally {
      setBusyVariantId(null)
    }
  }

  const activateVariant = async (variant: PointCloudVariant) => {
    if (!project || !projectRoot || busyVariantId) return
    setBusyVariantId(variant.id)
    try {
      await invoke<XpanoProjectV2>('set_active_point_variant', {
        projectRoot,
        variantId: variant.id,
        expectedTransformRevision: project.geometry.transform.revision,
      })
      setPreview(null)
      setPreviewVariantId(variant.id)
      setReloadToken((value) => value + 1)
    } finally {
      setBusyVariantId(null)
    }
  }

  const deleteVariant = async (variant: PointCloudVariant) => {
    if (!projectRoot || busyVariantId || !window.confirm(`确认删除“${variant.label}”点云版本？此操作不可撤销。`)) return
    setBusyVariantId(variant.id)
    try {
      await invoke<XpanoProjectV2>('delete_point_variant', { projectRoot, variantId: variant.id })
      setPreview((current) => current?.variant.id === variant.id ? null : current)
      setPreviewVariantId((current) => current === variant.id ? project?.geometry.activeVariantId ?? null : current)
    } finally {
      setBusyVariantId(null)
    }
  }

  if (!dataPath) {
    return (
      <section className="liquid-panel flex h-full items-center justify-center p-8 text-center">
        <div>
          <span className="icon-tile-lg mx-auto grid h-14 w-14 place-items-center rounded-card"><Box className="h-6 w-6" /></span>
          <h1 className="mt-4 text-[16px] font-semibold text-ink">暂无可查看成果</h1>
          <p className="mt-1 text-[12px] text-muted">完成重建或拖入合法 COLMAP 目录后，点云将在此显示。</p>
          <button onClick={() => navigate('/project/reconstruction')} className="glass-control motion-press mt-5 inline-flex h-9 items-center gap-2 rounded-comfortable px-4 text-[12px] font-medium text-ink/70 hover:text-brand">
            <ArrowLeft className="h-4 w-4" /> 返回重建
          </button>
        </div>
      </section>
    )
  }

  return (
    <div className="relative h-full">
      <ViewerPage
        embedded
        dataPath={dataPath}
        projectLabel={project?.name || 'COLMAP 预览'}
        resolvedTheme={resolvedTheme}
        active={active}
        variantPreview={viewerVariantPreview}
        reloadToken={reloadToken}
        projectRoot={projectRoot || null}
        worldFromCanonical={project?.geometry.transform.worldFromCanonical ?? null}
        transformRevision={project?.geometry.transform.revision ?? null}
        onGeometryChanged={() => {
          setPreview(null)
          setReloadToken((value) => value + 1)
        }}
      />
      {project && projectRoot && (
        <PointVariantPanel
          variants={variants}
          activeVariantId={project.geometry.activeVariantId}
          previewVariantId={previewVariantId ?? project.geometry.activeVariantId}
          busyVariantId={busyVariantId}
          onPreview={previewVariant}
          onActivate={activateVariant}
          onDelete={deleteVariant}
        />
      )}
    </div>
  )
}
