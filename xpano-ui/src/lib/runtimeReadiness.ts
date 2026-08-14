export type RuntimeResourceState = 'ready' | 'corrupt'
export type MetashapeRuntimeState = 'ready' | 'dependencies_missing' | 'missing' | 'unsupported' | 'error'
export type DensificationRuntimeState = 'ready' | 'downloadable'

export interface RuntimeReadinessStatus {
  bundled: RuntimeResourceState
  metashape: MetashapeRuntimeState
  densification: DensificationRuntimeState
  detail: string
}

export function runtimeReadinessPresentation(status: RuntimeReadinessStatus) {
  if (status.bundled === 'corrupt') return { tone: 'error' as const, label: '内置环境损坏' }
  if (status.metashape === 'dependencies_missing') return { tone: 'warning' as const, label: 'Metashape 待配置' }
  if (status.metashape === 'unsupported') return { tone: 'error' as const, label: 'Metashape 不兼容' }
  if (status.metashape === 'error') return { tone: 'error' as const, label: '环境检查失败' }
  if (status.metashape === 'missing') return { tone: 'warning' as const, label: '未安装 Metashape' }
  return { tone: 'ready' as const, label: '环境就绪' }
}
