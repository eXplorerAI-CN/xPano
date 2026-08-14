export const PHOTO_PREVIEW_BATCH_SIZE = 24

export function initialPhotoPreviewCount(total: number, batchSize = PHOTO_PREVIEW_BATCH_SIZE) {
  return Math.min(Math.max(0, Math.floor(total)), Math.max(1, Math.floor(batchSize)))
}

export function nextPhotoPreviewCount(current: number, total: number, batchSize = PHOTO_PREVIEW_BATCH_SIZE) {
  const safeCurrent = Math.max(0, Math.floor(current))
  const safeTotal = Math.max(0, Math.floor(total))
  const safeBatch = Math.max(1, Math.floor(batchSize))
  return Math.min(safeTotal, safeCurrent + safeBatch)
}
