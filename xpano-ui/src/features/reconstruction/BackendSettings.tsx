import { Cpu, FolderOpen, Gauge, Image, RotateCcw, ScanLine, Settings2, Sparkles } from 'lucide-react'
import type { ProjectTrack } from '../../lib/contracts'
import type { BackendProbe, ReconstructionConfigDraft } from './reconstructionTypes'
import { ParameterHelp } from './ParameterHelp'

interface BackendSettingsProps {
  config: ReconstructionConfigDraft
  probes: BackendProbe[]
  tracks: ProjectTrack[]
  running: boolean
  dirty: boolean
  onChange: (next: ReconstructionConfigDraft) => void
  onBrowseMetashape: () => void
  onReconfigure: () => void
  onReset: () => void
}

function FieldLabel({ children, help }: { children: string; help: React.ComponentProps<typeof ParameterHelp> }) {
  return <div className="mb-1.5 flex items-center justify-between gap-2"><span className="text-[11px] font-medium text-ink/75">{children}</span><ParameterHelp {...help} /></div>
}

const inputClass = 'theme-input h-9 w-full rounded-comfortable border px-2.5 text-[12px] outline-none transition-colors disabled:cursor-not-allowed disabled:opacity-55'

export function BackendSettings({ config, probes, tracks, running, dirty, onChange, onBrowseMetashape, onReconfigure, onReset }: BackendSettingsProps) {
  const hasFlatMedia = tracks.some((track) => track.type !== 'panoramic_video')
  const metashapeProbe = probes.find((probe) => probe.backend === 'metashape')
  const colmapProbe = probes.find((probe) => probe.backend === 'colmap')
  const colmapBlocked = hasFlatMedia || colmapProbe?.available === false
  const update = <K extends keyof ReconstructionConfigDraft>(key: K, value: ReconstructionConfigDraft[K]) => onChange({ ...config, [key]: value })

  return (
    <aside className="liquid-panel flex min-h-0 flex-col overflow-hidden p-0">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-3.5">
        <div><h2 className="text-[13px] font-semibold text-ink">参数设置</h2><p className="text-[10px] text-muted">任务启动后锁定</p></div>
        <div className="flex items-center gap-1"><button type="button" onClick={onReconfigure} disabled={running} className="motion-press grid h-7 w-7 place-items-center rounded-comfortable text-muted hover:bg-brand/8 hover:text-brand disabled:opacity-45" aria-label="重新配置"><Settings2 className="h-3.5 w-3.5" /></button>{dirty && <button type="button" onClick={onReset} disabled={running} className="motion-press grid h-7 w-7 place-items-center rounded-comfortable text-muted hover:bg-brand/8 hover:text-brand disabled:opacity-45" aria-label="撤销参数修改"><RotateCcw className="h-3.5 w-3.5" /></button>}<span className={`rounded-full px-2 py-1 text-[10px] font-medium ${dirty ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success'}`}>{dirty ? '尚未应用' : '已应用'}</span></div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3.5">
        <div className="theme-segment grid grid-cols-2 gap-1 p-1" role="tablist" aria-label="重建后端">
          <button type="button" disabled={running} onClick={() => update('backend', 'metashape')} className={`motion-press flex h-9 items-center justify-center gap-1.5 rounded-subtle text-[11px] font-medium ${config.backend === 'metashape' ? 'bg-brand text-white shadow-sm shadow-brand/20' : 'text-muted hover:text-ink'} disabled:cursor-not-allowed disabled:opacity-45`}><ScanLine className="h-3.5 w-3.5" /> Metashape</button>
          <button type="button" disabled={running || colmapBlocked} onClick={() => update('backend', 'colmap')} className={`motion-press flex h-9 items-center justify-center gap-1.5 rounded-subtle text-[11px] font-medium ${config.backend === 'colmap' ? 'bg-brand text-white shadow-sm shadow-brand/20' : 'text-muted hover:text-ink'} disabled:cursor-not-allowed disabled:opacity-45`}><Cpu className="h-3.5 w-3.5" /> COLMAP</button>
        </div>

        <div className="mt-3 rounded-comfortable border border-[var(--xp-line)] px-3 py-2.5 text-[10px] leading-4 text-muted">
          <p className="flex items-center justify-between gap-2"><span>{config.backend === 'metashape' ? 'Metashape 状态' : 'COLMAP 状态'}</span><span className={(config.backend === 'metashape' ? metashapeProbe : colmapProbe)?.available ? 'text-success' : 'text-danger'}>{(config.backend === 'metashape' ? metashapeProbe : colmapProbe)?.available ? '可用' : '不可用'}</span></p>
          <p className="mt-1 truncate font-mono" title={(config.backend === 'metashape' ? metashapeProbe : colmapProbe)?.path}>{(config.backend === 'metashape' ? metashapeProbe : colmapProbe)?.path || '正在检测'}</p>
          {hasFlatMedia && <p className="mt-2 text-warning">当前含普通视频或照片，COLMAP 暂不开放，避免忽略平面素材。</p>}
        </div>

        {config.backend === 'metashape' ? (
          <div className="mt-4 space-y-5">
            <section>
              <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted"><FolderOpen className="h-3.5 w-3.5" /> Metashape</div>
              <div className="flex gap-2">
                <input disabled={running} value={config.metashapePath} onChange={(event) => update('metashapePath', event.target.value)} placeholder={metashapeProbe?.path || 'metashape.exe'} className={inputClass} aria-label="Metashape executable path" />
                <button type="button" disabled={running} onClick={onBrowseMetashape} className="glass-control motion-press h-9 shrink-0 rounded-comfortable px-3 text-[11px] font-medium text-ink/75 disabled:opacity-45">浏览</button>
              </div>
              <p className="mt-1.5 text-[9px] leading-4 text-muted">留空时自动检测；手动指定后将严格使用该文件，不会静默切换到其他安装。</p>
            </section>
            <section>
              <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted"><Sparkles className="h-3.5 w-3.5" /> 对齐策略</div>
              <FieldLabel help={{ title: '对齐策略', description: '先完成全景站点对齐，再按视觉重叠增量接入普通帧和照片。', recommendation: hasFlatMedia ? '当前包含混合素材，将采用分阶段对齐。' : '当前将采用全景站点对齐。', tradeoff: '分阶段处理会增加一次匹配，但能避免平面素材干扰全景骨架。' }}>策略</FieldLabel>
              <div className={`${inputClass} flex items-center`}>稳定分阶段模式</div>
            </section>

            <section>
              <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted"><Gauge className="h-3.5 w-3.5" /> 特征与优化</div>
              <div>
                <FieldLabel help={{ title: '关键点上限', description: '每张图用于匹配的候选特征数量上限。', recommendation: tracks.length > 2 ? '多轨素材建议保持 40000，失败后再提高到 60000。' : '当前建议 40000。', tradeoff: '提高会增加匹配时间和内存，但可能改善低纹理区域。' }}>关键点上限</FieldLabel>
                <input disabled={running} type="number" min={0} step={1000} value={config.metashapeKeypointLimit} onChange={(event) => update('metashapeKeypointLimit', Math.max(0, Number(event.target.value) || 0))} className={inputClass} />
              </div>
              <div className="mt-3">
                <FieldLabel help={{ title: '连接点上限', description: '控制每张图保留的连接点数量；0 保持当前 Metashape 脚本的无限制语义。', recommendation: '保持 0，除非需要主动压低内存。', tradeoff: '降低可减少内存，但也可能削弱跨轨稳定性。' }}>连接点上限</FieldLabel>
                <input disabled={running} type="number" min={0} step={1000} value={config.metashapeTiepointLimit} onChange={(event) => update('metashapeTiepointLimit', Math.max(0, Number(event.target.value) || 0))} className={inputClass} />
              </div>
            </section>

            <section>
              <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted"><Image className="h-3.5 w-3.5" /> 坐标与导出</div>
              <FieldLabel help={{ title: '向上轴', description: '自动地面校正要对齐到的训练坐标轴。', recommendation: '保持 +Y，与当前 xPano 和查看器方向一致。', tradeoff: '修改只影响成果坐标方向，不改变相机匹配。' }}>向上轴</FieldLabel>
              <select disabled={running} value={config.upAxis} onChange={(event) => update('upAxis', event.target.value)} className={inputClass}><option value="+Y">+Y</option><option value="+Z">+Z</option><option value="-Y">-Y</option></select>
            </section>
          </div>
        ) : (
          <div className="mt-4 space-y-5">
            <section>
              <div className="mb-2 text-[10px] font-semibold uppercase text-muted">特征与匹配</div>
              <FieldLabel help={{ title: '匹配策略', description: '连续全景视频可使用顺序匹配；非时间对应素材不应使用顺序映射。', recommendation: '当前纯全景轨道建议顺序匹配。', tradeoff: '穷举匹配覆盖更广，但内存和时间增长明显。' }}>匹配策略</FieldLabel>
              <select disabled={running} value={config.colmapMatcher} onChange={(event) => update('colmapMatcher', event.target.value as ReconstructionConfigDraft['colmapMatcher'])} className={inputClass}><option value="sequential">顺序匹配</option><option value="exhaustive">穷举匹配</option></select>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <div><FieldLabel help={{ title: '最大图像尺寸', description: 'SIFT 特征提取前的最长边缩放上限。', recommendation: '当前建议 1600。', tradeoff: '提高可保留细节，但显存与耗时上升。' }}>图像尺寸</FieldLabel><input disabled={running} type="number" min={256} step={128} value={config.colmapMaxImageSize} onChange={(event) => update('colmapMaxImageSize', Math.max(256, Number(event.target.value) || 1600))} className={inputClass} /></div>
                <div><FieldLabel help={{ title: '最大特征数', description: '每张图保留的 SIFT 特征数量。', recommendation: '稳定模式建议 4096。', tradeoff: '提高会增加数据库体积、显存和匹配时间。' }}>特征数</FieldLabel><input disabled={running} type="number" min={512} step={512} value={config.colmapMaxNumFeatures} onChange={(event) => update('colmapMaxNumFeatures', Math.max(512, Number(event.target.value) || 4096))} className={inputClass} /></div>
              </div>
            </section>
            <section>
              <div className="mb-2 text-[10px] font-semibold uppercase text-muted">性能</div>
              <label className="flex min-h-10 items-center justify-between gap-3 rounded-comfortable border border-[var(--xp-line)] px-3 py-2 text-[11px] text-ink/75"><span>使用 GPU</span><input disabled={running || colmapProbe?.cudaAvailable === false} type="checkbox" checked={config.colmapUseGpu} onChange={(event) => update('colmapUseGpu', event.target.checked)} className="h-4 w-4 accent-[var(--xp-brand)]" /></label>
            </section>
          </div>
        )}
      </div>
    </aside>
  )
}
