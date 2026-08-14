import type { ReconstructionBackend } from '../../lib/contracts'

export type MetashapeAlignmentMode = 'backbone' | 'mixed'
export type ColmapDensityPreset = 'stable' | 'high-density' | 'experimental-high-density'
export type ColmapMatcher = 'sequential' | 'exhaustive'

export interface ReconstructionConfigDraft {
  backend: ReconstructionBackend
  metashapePath: string
  alignmentMode: MetashapeAlignmentMode
  metashapeKeypointLimit: number
  metashapeTiepointLimit: number
  upAxis: string
  colmapDensityPreset: ColmapDensityPreset
  colmapUseGpu: boolean
  colmapMatcher: ColmapMatcher
  colmapMaxImageSize: number
  colmapMaxNumFeatures: number
}

export interface BackendProbe {
  backend: ReconstructionBackend
  available: boolean
  path: string
  cudaAvailable: boolean | null
  detail: string
}

export type PlanNodeState = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

export const defaultReconstructionConfig: ReconstructionConfigDraft = {
  backend: 'metashape',
  metashapePath: '',
  alignmentMode: 'backbone',
  metashapeKeypointLimit: 40000,
  metashapeTiepointLimit: 0,
  upAxis: '+Y',
  colmapDensityPreset: 'stable',
  colmapUseGpu: true,
  colmapMatcher: 'sequential',
  colmapMaxImageSize: 1600,
  colmapMaxNumFeatures: 4096,
}

function numberValue(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function normalizeExecutablePath(value: unknown) {
  if (typeof value !== 'string') return ''
  const trimmed = value.trim()
  return trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')
    ? trimmed.slice(1, -1).trim()
    : trimmed
}

export function configFromProject(
  backend: ReconstructionBackend,
  value: Record<string, unknown>,
): ReconstructionConfigDraft {
  return {
    ...defaultReconstructionConfig,
    backend,
    metashapePath: normalizeExecutablePath(value.metashapePath),
    alignmentMode: 'backbone',
    metashapeKeypointLimit: numberValue(value.metashapeKeypointLimit, 40000),
    metashapeTiepointLimit: numberValue(value.metashapeTiepointLimit, 0),
    upAxis: typeof value.upAxis === 'string' ? value.upAxis : '+Y',
    colmapDensityPreset: value.colmapDensityPreset === 'high-density' || value.colmapDensityPreset === 'experimental-high-density'
      ? value.colmapDensityPreset
      : 'stable',
    colmapUseGpu: value.colmapUseGpu !== false,
    colmapMatcher: value.colmapMatcher === 'exhaustive' ? 'exhaustive' : 'sequential',
    colmapMaxImageSize: numberValue(value.colmapMaxImageSize, 1600),
    colmapMaxNumFeatures: numberValue(value.colmapMaxNumFeatures, 4096),
  }
}

export function persistedReconstructionConfig(config: ReconstructionConfigDraft) {
  return {
    metashapePath: normalizeExecutablePath(config.metashapePath),
    alignmentMode: config.alignmentMode,
    metashapeKeypointLimit: config.metashapeKeypointLimit,
    metashapeTiepointLimit: config.metashapeTiepointLimit,
    upAxis: config.upAxis,
    colmapDensityPreset: config.colmapDensityPreset,
    colmapUseGpu: config.colmapUseGpu,
    colmapMatcher: config.colmapMatcher,
    colmapMaxImageSize: config.colmapMaxImageSize,
    colmapMaxNumFeatures: config.colmapMaxNumFeatures,
  }
}
