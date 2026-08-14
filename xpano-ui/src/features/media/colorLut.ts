import type { ProjectTrackType } from '../../lib/contracts'

export const DJI_OSMO_360_DLOGM_REC709_PRESET = 'builtin:dji-osmo360-dlogm-rec709'

export function isVideoTrackType(trackType: ProjectTrackType) {
  return trackType === 'panoramic_video' || trackType === 'ordinary_video'
}

export function isStyleLutSupported(trackType: ProjectTrackType) {
  return trackType === 'panoramic_video'
    || trackType === 'ordinary_video'
    || trackType === 'standard_photos'
    || trackType === 'aerial_photos'
}

export function normalizeColorLutPath(path?: string | null) {
  const normalized = path?.trim() ?? ''
  return normalized || null
}

export function isCubeLutPath(path?: string | null) {
  const normalized = normalizeColorLutPath(path)
  return normalized !== null && /\.cube$/i.test(normalized)
}

export function builtinColorLutPresetForSource(trackType: ProjectTrackType, sourcePath: string) {
  const extension = sourcePath.split('.').pop()?.toLowerCase()
  return trackType === 'panoramic_video' && extension === 'osv'
    ? DJI_OSMO_360_DLOGM_REC709_PRESET
    : null
}
