import type { TrainingStatus } from '../../lib/contracts'

export type TrainingStrategy = 'mrnf' | 'mcmc' | 'igs+'
export type TrainingPreset = 'fast' | 'balanced' | 'quality'
export type TrainingPresetSelection = TrainingPreset | 'custom'
export type TrainingResizeFactor = 'auto' | '1' | '2' | '4' | '8'
export type TrainingCentralize = 'off' | 'by_pointcloud' | 'by_cameras'
export type TrainingBackgroundMode = 'solidcolor' | 'modulation' | 'random'
export type TrainingWorkspaceMode = 'setup' | 'running' | 'complete' | 'failed' | 'interrupted'
export type TrainingRecoveryAction = 'media' | 'reconstruction' | 'results' | 'recheck'

export interface TrainingStartBlocker {
  reason: string
  action: TrainingRecoveryAction | null
}

export interface TrainingConfig {
  iterations: number
  strategy: TrainingStrategy
  shDegree: 0 | 1 | 2 | 3
  maxGaussians: number
  resizeFactor: TrainingResizeFactor
  maxWidth: number
  testEvery: number
  useCpuCache: boolean
  useFsCache: boolean
  centralize: TrainingCentralize
  undistort: boolean
  enableMip: boolean
  bilateralGrid: boolean
  enableEval: boolean
  backgroundMode: TrainingBackgroundMode
  backgroundColor: string
  gui: boolean
}

export interface TrainingReadiness {
  runtimeAvailable: boolean
  runtimePath?: string
  runtimeCode?: string
  runtimeMessage?: string
  cudaAvailable?: boolean
  vulkanAvailable?: boolean
  datasetAvailable: boolean
  datasetMessage?: string
  geometryAvailable: boolean
  outputAvailable?: boolean
  outputMessage?: string
}

export const DEFAULT_TRAINING_CONFIG: TrainingConfig = {
  iterations: 30000,
  strategy: 'mrnf',
  shDegree: 3,
  maxGaussians: 1_000_000,
  resizeFactor: 'auto',
  maxWidth: 3840,
  testEvery: 0,
  useCpuCache: true,
  useFsCache: true,
  centralize: 'off',
  undistort: false,
  enableMip: false,
  bilateralGrid: true,
  enableEval: false,
  backgroundMode: 'solidcolor',
  backgroundColor: '#000000',
  gui: true,
}

const PRESET_VALUES: Record<TrainingPreset, Pick<TrainingConfig, 'iterations' | 'maxGaussians' | 'resizeFactor' | 'maxWidth'>> = {
  fast: { iterations: 10000, maxGaussians: 500000, resizeFactor: '2', maxWidth: 2560 },
  balanced: { iterations: 30000, maxGaussians: 1_000_000, resizeFactor: 'auto', maxWidth: 3840 },
  quality: { iterations: 60000, maxGaussians: 2_000_000, resizeFactor: '1', maxWidth: 0 },
}

export function applyTrainingPreset(config: TrainingConfig, preset: TrainingPreset): TrainingConfig {
  return { ...config, ...PRESET_VALUES[preset] }
}

export function deriveTrainingPreset(config: TrainingConfig): TrainingPresetSelection {
  const preset = (Object.entries(PRESET_VALUES) as Array<[TrainingPreset, typeof PRESET_VALUES[TrainingPreset]]>)
    .find(([, values]) => Object.entries(values).every(([key, value]) => config[key as keyof typeof values] === value))
  return preset?.[0] ?? 'custom'
}

export function trainingWorkspaceMode(status: TrainingStatus, live: boolean): TrainingWorkspaceMode {
  if (live || status === 'running') return 'running'
  if (status === 'complete') return 'complete'
  if (status === 'failed') return 'failed'
  if (status === 'interrupted') return 'interrupted'
  return 'setup'
}

export function trainingStartBlocker(
  hasProject: boolean,
  readiness: Pick<TrainingReadiness, 'runtimeAvailable' | 'runtimeMessage' | 'datasetAvailable' | 'datasetMessage' | 'geometryAvailable' | 'outputAvailable' | 'outputMessage'>,
  running: boolean,
): TrainingStartBlocker | null {
  if (!hasProject) return { reason: '请先打开并准备一个工程', action: 'media' }
  if (running) return { reason: '当前有任务正在运行', action: null }
  if (!readiness.runtimeAvailable) return { reason: readiness.runtimeMessage || 'LichtFeld 运行环境不可用', action: 'recheck' }
  if (!readiness.datasetAvailable) return { reason: readiness.datasetMessage || '训练数据尚未导出', action: 'reconstruction' }
  if (!readiness.geometryAvailable) return { reason: '尚未选择有效训练点云', action: 'results' }
  if (readiness.outputAvailable === false) return { reason: readiness.outputMessage || '训练输出位置不可写入', action: 'recheck' }
  return null
}

export function trainingCanStart(readiness: Pick<TrainingReadiness, 'runtimeAvailable' | 'datasetAvailable' | 'geometryAvailable' | 'outputAvailable'>, running: boolean) {
  return readiness.runtimeAvailable && readiness.datasetAvailable && readiness.geometryAvailable && readiness.outputAvailable !== false && !running
}

export function trainingDisplayPercent(
  status: string,
  current: number,
  total: number,
  live: boolean,
  livePercent: number,
) {
  if (live) return Math.max(0, Math.min(100, livePercent))
  if (status === 'complete') return 100
  if (current > 0 && total > 0) return Math.max(0, Math.min(100, current / total * 100))
  return 0
}
