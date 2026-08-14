import { createPortal } from 'react-dom'
import { useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Cpu, FolderOpen, ScanLine, X } from 'lucide-react'
import type { ProjectTrack } from '../../lib/contracts'
import type { BackendProbe, ReconstructionConfigDraft } from './reconstructionTypes'

interface ReconstructionSetupDialogProps {
  open: boolean
  config: ReconstructionConfigDraft
  probes: BackendProbe[]
  tracks: ProjectTrack[]
  projectRoot: string
  onChange: (config: ReconstructionConfigDraft) => void
  onBrowseMetashape: () => void
  onClose: () => void
  onStart: () => void
}

export function ReconstructionSetupDialog({ open, config, probes, tracks, projectRoot, onChange, onBrowseMetashape, onClose, onStart }: ReconstructionSetupDialogProps) {
  const [step, setStep] = useState(0)
  if (!open) return null
  const hasFlat = tracks.some((track) => track.type !== 'panoramic_video')
  const metashape = probes.find((probe) => probe.backend === 'metashape')
  const colmap = probes.find((probe) => probe.backend === 'colmap')
  const selectedProbe = config.backend === 'metashape' ? metashape : colmap
  const canContinue = Boolean(selectedProbe?.available) && !(config.backend === 'colmap' && hasFlat)

  return createPortal(
    <div className="fixed inset-0 z-[170] grid place-items-center bg-black/35 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="配置对齐任务">
      <section className="reconstruction-setup-dialog liquid-panel flex max-h-[min(680px,calc(100vh-32px))] w-[min(720px,calc(100vw-32px))] flex-col overflow-hidden p-0">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-5">
          <div><h2 className="text-[14px] font-semibold text-ink">配置对齐与重建</h2><p className="mt-0.5 text-[10px] text-muted">{step + 1}/3 · {['选择后端', '确认参数', '输出与启动'][step]}</p></div>
          <button type="button" onClick={onClose} className="motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:bg-ink/[0.04] hover:text-ink" aria-label="关闭向导"><X className="h-4 w-4" /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {step === 0 && (
            <div>
              <h3 className="text-[13px] font-semibold text-ink">选择重建后端</h3>
              <p className="mt-1 text-[11px] leading-5 text-muted">后端能力会按当前素材组合校验，不可用方案仍保留说明。</p>
              <div className="mt-4 grid grid-cols-2 gap-3">
                {([
                  { backend: 'metashape' as const, name: 'Metashape', icon: ScanLine, probe: metashape, description: '推荐用于全景与普通照片混合素材，支持分阶段骨架对齐。', blocked: false },
                  { backend: 'colmap' as const, name: 'COLMAP', icon: Cpu, probe: colmap, description: '内置后端，当前只开放经过验证的纯全景流程。', blocked: hasFlat },
                ]).map((item) => {
                  const Icon = item.icon
                  const disabled = item.blocked || (item.backend === 'colmap' && !item.probe?.available)
                  const selected = config.backend === item.backend
                  return <button key={item.backend} type="button" disabled={disabled} onClick={() => onChange({ ...config, backend: item.backend })} className={`motion-press min-h-[170px] rounded-comfortable border p-4 text-left transition-colors ${selected ? 'border-brand/45 bg-brand/[0.055]' : 'border-[var(--xp-line)] bg-ink/[0.018] hover:border-brand/25'} disabled:cursor-not-allowed disabled:opacity-55`}><div className="flex items-center justify-between"><span className={`grid h-9 w-9 place-items-center rounded-comfortable ${selected ? 'bg-brand text-white' : 'bg-ink/[0.045] text-muted'}`}><Icon className="h-4 w-4" /></span><span className={`rounded-full px-2 py-1 text-[9px] ${item.probe?.available && !item.blocked ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'}`}>{item.blocked ? '混合素材未开放' : item.probe?.available ? '可用' : '未检测到'}</span></div><h4 className="mt-3 text-[13px] font-semibold text-ink">{item.name}</h4><p className="mt-1 text-[10px] leading-5 text-muted">{item.description}</p><p className="mt-3 truncate font-mono text-[9px] text-muted/70" title={item.probe?.path}>{item.probe?.path || '正在检测'}</p></button>
                })}
              </div>
              {config.backend === 'metashape' && <div className="mt-3 flex gap-2"><input value={config.metashapePath} onChange={(event) => onChange({ ...config, metashapePath: event.target.value })} placeholder={metashape?.path || 'metashape.exe'} className="theme-input h-10 min-w-0 flex-1 rounded-comfortable border px-3 font-mono text-[11px]" aria-label="Metashape executable path" /><button type="button" onClick={onBrowseMetashape} className="glass-control motion-press h-10 shrink-0 rounded-comfortable px-4 text-[11px] font-medium text-ink/75">浏览 Metashape.exe</button></div>}
            </div>
          )}

          {step === 1 && (
            <div>
              <h3 className="text-[13px] font-semibold text-ink">确认关键参数</h3>
              <p className="mt-1 text-[11px] leading-5 text-muted">这里仅展示首次启动所需参数，完整高级参数可在主页面继续修改。</p>
              {config.backend === 'metashape' ? <div className="mt-5 grid grid-cols-2 gap-4"><div className="text-[11px] text-muted"><span className="mb-1.5 block font-medium text-ink/75">对齐策略</span><div className="theme-input flex h-10 w-full items-center rounded-comfortable border px-3 text-[12px]">稳定分阶段模式</div></div><label className="text-[11px] text-muted"><span className="mb-1.5 block font-medium text-ink/75">关键点上限</span><input type="number" value={config.metashapeKeypointLimit} onChange={(event) => onChange({ ...config, metashapeKeypointLimit: Math.max(0, Number(event.target.value) || 0) })} className="theme-input h-10 w-full rounded-comfortable border px-3 text-[12px]" /></label><div className="col-span-2 rounded-comfortable border border-[var(--xp-line)] p-3 text-[10px] leading-5 text-muted">当前工程包含 {tracks.length} 条素材轨、{tracks.reduce((sum, track) => sum + track.items.filter((item) => item.selected).length, 0)} 个已选素材项。{hasFlat ? '将先对齐全景，再增量接入平面素材。' : '当前为纯全景素材，将执行全景站点对齐。'}</div></div> : <div className="mt-5 grid grid-cols-2 gap-4"><label className="text-[11px] text-muted"><span className="mb-1.5 block font-medium text-ink/75">匹配策略</span><select value={config.colmapMatcher} onChange={(event) => onChange({ ...config, colmapMatcher: event.target.value as ReconstructionConfigDraft['colmapMatcher'] })} className="theme-input h-10 w-full rounded-comfortable border px-3 text-[12px]"><option value="sequential">顺序匹配</option><option value="exhaustive">穷举匹配</option></select></label><label className="text-[11px] text-muted"><span className="mb-1.5 block font-medium text-ink/75">GPU</span><span className="flex h-10 items-center justify-between rounded-comfortable border border-[var(--xp-line)] px-3 text-ink/75">CUDA 加速<input type="checkbox" checked={config.colmapUseGpu} onChange={(event) => onChange({ ...config, colmapUseGpu: event.target.checked })} className="h-4 w-4 accent-[var(--xp-brand)]" /></span></label></div>}
            </div>
          )}

          {step === 2 && (
            <div>
              <h3 className="text-[13px] font-semibold text-ink">确认输出位置</h3>
              <p className="mt-1 text-[11px] leading-5 text-muted">当前工程会继续复用素材、PSX 和 COLMAP 目录，不覆盖工程外文件。</p>
              <div className="mt-5 flex items-center gap-3 rounded-comfortable border border-[var(--xp-line)] p-4"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-comfortable bg-brand/8 text-brand"><FolderOpen className="h-4 w-4" /></span><div className="min-w-0"><p className="text-[10px] text-muted">工程输出目录</p><p className="mt-1 truncate font-mono text-[11px] text-ink" title={projectRoot}>{projectRoot}</p></div><Check className="ml-auto h-4 w-4 shrink-0 text-success" /></div>
              <div className="mt-3 rounded-comfortable border border-warning/25 bg-warning/[0.045] p-3 text-[10px] leading-5 text-warning">启动后参数将锁定；停止任务会终止 Metashape/COLMAP 及其子进程，但保留已写出的日志和工程文件。</div>
            </div>
          )}
        </div>

        <footer className="flex h-16 shrink-0 items-center justify-between border-t border-[var(--xp-line)] px-5">
          <button type="button" onClick={step === 0 ? onClose : () => setStep((value) => Math.max(0, value - 1))} className="glass-control motion-press flex h-9 items-center gap-1.5 rounded-comfortable px-4 text-[11px] font-medium text-ink/70"><ChevronLeft className="h-3.5 w-3.5" /> {step === 0 ? '取消' : '上一步'}</button>
          {step < 2 ? <button type="button" disabled={!canContinue} onClick={() => setStep((value) => Math.min(2, value + 1))} className="motion-press flex h-9 items-center gap-1.5 rounded-comfortable bg-brand px-4 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45">下一步 <ChevronRight className="h-3.5 w-3.5" /></button> : <button type="button" onClick={onStart} className="motion-press flex h-9 items-center gap-2 rounded-comfortable bg-brand px-5 text-[11px] font-semibold text-white"><Check className="h-3.5 w-3.5" /> 一键启动对齐</button>}
        </footer>
      </section>
    </div>,
    document.body,
  )
}
