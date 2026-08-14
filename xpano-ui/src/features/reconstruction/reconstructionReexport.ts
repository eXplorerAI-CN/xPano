import type { ReconstructionBackend } from '../../lib/contracts'

interface PsxReexportContext {
  backend: ReconstructionBackend
  projectCurrent: boolean
  projectPath: string | null
  manifestPath: string | null
  dirty: boolean
  running: boolean
  backendAvailable: boolean
}

export function psxReexportAvailability(context: PsxReexportContext) {
  if (context.running) return { allowed: false, reason: '当前已有任务正在运行' }
  if (context.backend !== 'metashape') return { allowed: false, reason: '仅 Metashape 工程支持从 PSX 重新导出' }
  if (!context.projectCurrent) return { allowed: false, reason: 'PSX 与当前素材版本不一致' }
  if (!context.projectPath) return { allowed: false, reason: '当前工程没有可用的 PSX 文件' }
  if (!context.manifestPath) return { allowed: false, reason: '当前工程缺少对齐素材清单' }
  if (context.dirty) return { allowed: false, reason: '请先应用当前参数修改' }
  if (!context.backendAvailable) return { allowed: false, reason: 'Metashape 当前不可用' }
  return { allowed: true, reason: '' }
}
