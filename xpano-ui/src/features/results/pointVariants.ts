import type { PointCloudVariant } from '../../lib/contracts.ts'

export function reconcilePreviewVariantId(
  variants: PointCloudVariant[],
  activeVariantId: string,
  requestedVariantId: string | null,
) {
  if (requestedVariantId && variants.some((variant) => variant.id === requestedVariantId && variant.status === 'ready')) {
    return requestedVariantId
  }
  return variants.some((variant) => variant.id === activeVariantId) ? activeVariantId : variants[0]?.id ?? null
}

export function canActivateVariant(variant: PointCloudVariant, activeVariantId: string) {
  return variant.status === 'ready' && variant.id !== activeVariantId
}

export function canDeleteVariant(variant: PointCloudVariant, activeVariantId: string) {
  return variant.kind === 'densified'
    && !variant.protected
    && variant.id !== activeVariantId
}

export function shouldMaterializeStandardVariant(
  variants: PointCloudVariant[],
  reconstructionStatus: string,
  activeVariantId: string,
) {
  const standard = variants.find((variant) => variant.id === 'standard')
  return reconstructionStatus === 'complete'
    && activeVariantId === 'standard'
    && Boolean(standard && standard.status !== 'ready')
}
