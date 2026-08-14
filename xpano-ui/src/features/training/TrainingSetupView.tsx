import { Check, ChevronDown, CircleAlert, Database, Gauge, HardDrive, Play, Sparkles } from 'lucide-react'
import { ParameterHelp } from '../reconstruction/ParameterHelp'
import type {
  TrainingConfig,
  TrainingPreset,
  TrainingPresetSelection,
  TrainingReadiness,
  TrainingRecoveryAction,
  TrainingStartBlocker,
} from './trainingConfig'

const presetLabels: Record<TrainingPresetSelection, { title: string; detail: string }> = {
  fast: { title: '快速预览', detail: '1 万步 · 50 万高斯' },
  balanced: { title: '标准', detail: '3 万步 · 100 万高斯' },
  quality: { title: '高质量', detail: '6 万步 · 200 万高斯' },
  custom: { title: '自定义', detail: '已调整参数' },
}

const fieldClass = 'theme-input h-10 w-full rounded-comfortable border px-3 text-[12px] text-ink outline-none disabled:cursor-not-allowed disabled:opacity-45'

function FieldLabel({ title, help }: { title: string; help: React.ComponentProps<typeof ParameterHelp> }) {
  return <div className="mb-1.5 flex min-h-6 items-center justify-between gap-2"><span className="text-[11px] font-medium text-ink/75">{title}</span><ParameterHelp {...help} /></div>
}

function NumericField({ label, value, min, max, step = 1, help, onChange }: { label: string; value: number; min: number; max?: number; step?: number; help: React.ComponentProps<typeof ParameterHelp>; onChange: (value: number) => void }) {
  return (
    <label className="block min-w-0">
      <FieldLabel title={label} help={help} />
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => {
          const parsed = Number(event.target.value)
          if (!Number.isFinite(parsed)) return
          onChange(Math.min(max ?? parsed, Math.max(min, parsed)))
        }}
        className={fieldClass}
      />
    </label>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="motion-press flex min-h-10 w-full items-center justify-between gap-3 border-b border-line/70 py-2 text-left last:border-b-0">
      <span className="text-[12px] font-medium text-ink/80">{label}</span>
      <span className={`relative h-5 w-9 shrink-0 rounded-full transition-colors duration-200 ${checked ? 'bg-brand' : 'bg-ink/15'}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-[left] duration-200 ${checked ? 'left-[18px]' : 'left-0.5'}`} /></span>
    </button>
  )
}

function ReadinessRow({ label, ready, checking }: { label: string; ready: boolean; checking: boolean }) {
  return (
    <div className="flex min-h-8 items-center justify-between gap-3 border-b border-line/70 py-1.5 last:border-b-0">
      <span className="text-[11px] text-muted">{label}</span>
      <span className={`flex items-center gap-1.5 text-[11px] font-medium ${checking ? 'text-muted' : ready ? 'text-success' : 'text-warning'}`}>
        {checking ? <span className="h-3 w-3 animate-spin rounded-full border border-ink/20 border-t-brand" /> : ready ? <Check className="h-3.5 w-3.5" /> : <CircleAlert className="h-3.5 w-3.5" />}
        {checking ? '检查中' : ready ? '已就绪' : '未就绪'}
      </span>
    </div>
  )
}

function formatSummary(config: TrainingConfig) {
  const resolution = config.resizeFactor === 'auto'
    ? `自动 · ${config.maxWidth ? `${config.maxWidth}px` : '不限宽度'}`
    : config.resizeFactor === '1'
      ? config.maxWidth ? `原始 · ${config.maxWidth}px` : '原始分辨率'
      : `1/${config.resizeFactor}${config.maxWidth ? ` · ${config.maxWidth}px` : ''}`
  return {
    scale: `${config.iterations.toLocaleString()} 步 · ${config.maxGaussians.toLocaleString()} 高斯`,
    method: `${config.strategy.toUpperCase()} · SH ${config.shDegree}`,
    resolution,
  }
}

interface TrainingSetupViewProps {
  config: TrainingConfig
  preset: TrainingPresetSelection
  readiness: TrainingReadiness
  checking: boolean
  advancedOpen: boolean
  blocker: TrainingStartBlocker | null
  inputsDisabled?: boolean
  onAdvancedOpenChange: (open: boolean) => void
  onSelectPreset: (preset: TrainingPreset) => void
  onChange: <Key extends keyof TrainingConfig>(key: Key, value: TrainingConfig[Key]) => void
  onStart: () => void
  onRecover: (action: TrainingRecoveryAction) => void
}

export function TrainingSetupView({ config, preset, readiness, checking, advancedOpen, blocker, inputsDisabled = false, onAdvancedOpenChange, onSelectPreset, onChange, onStart, onRecover }: TrainingSetupViewProps) {
  const summary = formatSummary(config)
  const recoverLabel = blocker?.action === 'reconstruction' ? '前往对齐与重建' : blocker?.action === 'results' ? '前往成果与后处理' : blocker?.action === 'media' ? '前往素材与处理' : '重新检查'

  return (
    <section className="liquid-panel grid h-full min-h-0 grid-cols-[minmax(0,1fr)_clamp(264px,24vw,304px)] overflow-hidden rounded-panel">
      <fieldset disabled={inputsDisabled} className="flex min-h-0 min-w-0 flex-col disabled:opacity-65">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0"><h1 className="text-[18px] font-semibold text-ink">高斯训练</h1><p className="mt-1 text-[11px] text-muted">配置本次训练质量与资源规模</p></div>
          <span className={`flex shrink-0 items-center gap-1.5 text-[11px] font-medium ${!checking && !blocker ? 'text-success' : 'text-muted'}`}>
            {!checking && !blocker ? <Check className="h-4 w-4" /> : <Gauge className="h-4 w-4" />}
            {checking ? '正在检查训练输入' : blocker ? '需要完成准备' : '训练数据已就绪'}
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <section>
            <div className="mb-2.5 flex items-center gap-2"><Sparkles className="h-4 w-4 text-brand" /><h2 className="text-[13px] font-semibold text-ink">训练方案</h2></div>
            <div className="grid grid-cols-4 rounded-comfortable bg-ink/[0.055] p-1" role="group" aria-label="训练方案">
              {(Object.keys(presetLabels) as TrainingPresetSelection[]).map((item) => {
                const selected = preset === item
                const selectable = item !== 'custom'
                return <button key={item} type="button" disabled={!selectable} onClick={() => selectable && onSelectPreset(item)} className={`motion-press min-w-0 rounded-subtle px-2 py-2 text-center transition-colors ${selected ? 'bg-[rgb(var(--xp-surface-rgb))] text-brand shadow-sm' : 'text-muted hover:text-ink disabled:cursor-default disabled:hover:text-muted'}`} aria-pressed={selected}><span className="block truncate text-[12px] font-semibold">{presetLabels[item].title}</span><span className="mt-0.5 block truncate text-[10px]">{presetLabels[item].detail}</span></button>
              })}
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-4">
            <h2 className="text-[13px] font-semibold text-ink">质量与规模</h2>
            <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
              <NumericField label="训练迭代次数" value={config.iterations} min={1} max={500000} onChange={(value) => onChange('iterations', value)} help={{ title: '训练迭代次数', description: '控制参数优化的总步数。', recommendation: '标准场景建议 30000 步。', tradeoff: '步数越高训练时间越长，后期收益会逐渐降低。' }} />
              <NumericField label="最大高斯数量" value={config.maxGaussians} min={10000} step={10000} onChange={(value) => onChange('maxGaussians', value)} help={{ title: '最大高斯数量', description: '限制训练过程中可生成的高斯数量。', recommendation: '标准场景建议 100 万。', tradeoff: '提高可能保留更多细节，同时增加显存、内存和产物大小。' }} />
              <label className="block min-w-0"><FieldLabel title="优化策略" help={{ title: '优化策略', description: '选择 LichtFeld 的高斯增长与优化方法。', recommendation: '保持 MRNF，适合大多数重建场景。', tradeoff: '其他策略会改变训练速度、显存占用和收敛特征。' }} /><select value={config.strategy} onChange={(event) => onChange('strategy', event.target.value as TrainingConfig['strategy'])} className={fieldClass}><option value="mrnf">MRNF（推荐）</option><option value="mcmc">MCMC</option><option value="igs+">IGS+</option></select></label>
              <label className="block min-w-0"><FieldLabel title="SH 阶数" help={{ title: 'SH 阶数', description: '控制视角相关颜色表达能力。', recommendation: '一般保持 3。', tradeoff: '提高可表达更复杂的视角变化，但会增加显存与训练成本。' }} /><select value={config.shDegree} onChange={(event) => onChange('shDegree', Number(event.target.value) as TrainingConfig['shDegree'])} className={fieldClass}>{[0, 1, 2, 3].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
              <label className="block min-w-0"><FieldLabel title="图像缩放" help={{ title: '图像缩放', description: '训练前按比例缩小输入图像。', recommendation: '自动模式兼顾质量与显存；显存不足时使用 1/2。', tradeoff: '缩小会提升速度并降低显存，同时减少可恢复细节。' }} /><select value={config.resizeFactor} onChange={(event) => onChange('resizeFactor', event.target.value as TrainingConfig['resizeFactor'])} className={fieldClass}><option value="auto">自动</option><option value="1">原始</option><option value="2">1/2</option><option value="4">1/4</option><option value="8">1/8</option></select></label>
              <NumericField label="图像最大宽度" value={config.maxWidth} min={0} step={128} onChange={(value) => onChange('maxWidth', value)} help={{ title: '图像最大宽度', description: '在缩放后继续限制图像最长边，0 表示不限制。', recommendation: '标准场景建议 3840px。', tradeoff: '降低可明显减少显存和训练时间，但会损失远处与边缘细节。' }} />
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-3">
            <button type="button" onClick={() => onAdvancedOpenChange(!advancedOpen)} className="motion-press flex h-10 w-full items-center justify-between text-left" aria-expanded={advancedOpen}><span className="text-[13px] font-semibold text-ink">高级设置</span><ChevronDown className={`h-4 w-4 text-muted transition-transform duration-200 ${advancedOpen ? 'rotate-180' : ''}`} /></button>
            {advancedOpen && <div className="grid grid-cols-2 gap-x-5 gap-y-4 pb-2 pt-3">
              <div><h3 className="mb-1 border-b border-line pb-2 text-[11px] font-semibold text-muted">性能</h3><Toggle label="CPU 图像缓存" checked={config.useCpuCache} onChange={(value) => onChange('useCpuCache', value)} /><Toggle label="文件系统缓存" checked={config.useFsCache} onChange={(value) => onChange('useFsCache', value)} /></div>
              <div><h3 className="mb-1 border-b border-line pb-2 text-[11px] font-semibold text-muted">质量</h3><Toggle label="双边网格" checked={config.bilateralGrid} onChange={(value) => onChange('bilateralGrid', value)} /><Toggle label="Mip 抗锯齿" checked={config.enableMip} onChange={(value) => onChange('enableMip', value)} /><Toggle label="训练评估" checked={config.enableEval} onChange={(value) => onChange('enableEval', value)} /></div>
              <div className="space-y-3"><h3 className="border-b border-line pb-2 text-[11px] font-semibold text-muted">输入处理</h3><label className="block"><span className="mb-1.5 block text-[11px] font-medium text-ink/75">数据居中</span><select value={config.centralize} onChange={(event) => onChange('centralize', event.target.value as TrainingConfig['centralize'])} className={fieldClass}><option value="off">关闭</option><option value="by_pointcloud">按点云</option><option value="by_cameras">按相机</option></select></label><Toggle label="在线去畸变" checked={config.undistort} onChange={(value) => onChange('undistort', value)} /></div>
              <div className="space-y-3"><h3 className="border-b border-line pb-2 text-[11px] font-semibold text-muted">验证与背景</h3><NumericField label="测试集间隔" value={config.testEvery} min={0} onChange={(value) => onChange('testEvery', value)} help={{ title: '测试集间隔', description: '按指定间隔保留测试视图，0 表示关闭。', recommendation: '只在需要定量评估时启用。', tradeoff: '启用会减少训练视图并增加评估开销。' }} /><label className="block"><span className="mb-1.5 block text-[11px] font-medium text-ink/75">背景模式</span><select value={config.backgroundMode} onChange={(event) => onChange('backgroundMode', event.target.value as TrainingConfig['backgroundMode'])} className={fieldClass}><option value="solidcolor">纯色</option><option value="modulation">调制</option><option value="random">随机</option></select></label>{config.backgroundMode === 'solidcolor' && <label className="flex h-10 items-center justify-between gap-3"><span className="text-[11px] font-medium text-ink/75">背景颜色</span><input type="color" value={config.backgroundColor} onChange={(event) => onChange('backgroundColor', event.target.value)} className="h-8 w-16 rounded-subtle border border-line bg-transparent p-1" /></label>}</div>
            </div>}
          </section>
        </div>
      </fieldset>

      <aside className="flex min-h-0 flex-col border-l border-line bg-ink/[0.022] px-4 py-4">
        <div><p className="text-[12px] font-semibold text-ink">本次训练</p><p className="mt-2 text-[13px] font-semibold text-brand">{presetLabels[preset].title}</p><dl className="mt-3 space-y-2 text-[11px]"><div><dt className="text-muted">规模</dt><dd className="mt-0.5 font-medium text-ink/80">{summary.scale}</dd></div><div><dt className="text-muted">方法</dt><dd className="mt-0.5 font-medium text-ink/80">{summary.method}</dd></div><div><dt className="text-muted">图像</dt><dd className="mt-0.5 font-medium text-ink/80">{summary.resolution}</dd></div><div><dt className="text-muted">外观补偿</dt><dd className="mt-0.5 font-medium text-ink/80">双边网格{config.bilateralGrid ? '已开启' : '已关闭'}</dd></div></dl></div>

        <div className="mt-5 border-t border-line pt-4"><p className="text-[11px] font-semibold text-ink/75">训练检查</p><div className="mt-2"><ReadinessRow label="LichtFeld 运行时" ready={readiness.runtimeAvailable} checking={checking} /><ReadinessRow label="NVIDIA CUDA" ready={readiness.cudaAvailable ?? false} checking={checking} /><ReadinessRow label="Vulkan 图形设备" ready={readiness.vulkanAvailable ?? false} checking={checking} /><ReadinessRow label="训练数据" ready={readiness.datasetAvailable} checking={checking} /><ReadinessRow label="训练点云" ready={readiness.geometryAvailable} checking={checking} /><ReadinessRow label="训练输出位置" ready={readiness.outputAvailable ?? false} checking={checking} /></div></div>

        <div className="mt-auto border-t border-line pt-4">
          {blocker && <div className="mb-3 flex items-start gap-2 text-[11px] leading-5 text-warning"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>{blocker.reason}</span></div>}
          {blocker?.action && <button type="button" onClick={() => onRecover(blocker.action!)} className="glass-control motion-press mb-2 flex h-9 w-full items-center justify-center gap-2 rounded-comfortable text-[11px] font-medium text-ink/75">{blocker.action === 'reconstruction' ? <Database className="h-3.5 w-3.5" /> : blocker.action === 'results' ? <HardDrive className="h-3.5 w-3.5" /> : <Gauge className="h-3.5 w-3.5" />}{recoverLabel}</button>}
          <button type="button" onClick={onStart} disabled={Boolean(blocker) || checking} className="motion-press flex h-10 w-full items-center justify-center gap-2 rounded-comfortable bg-brand px-4 text-[12px] font-semibold text-white shadow-sm shadow-brand/20 disabled:cursor-not-allowed disabled:opacity-35"><Play className="h-4 w-4 fill-current" />开始训练</button>
        </div>
      </aside>
    </section>
  )
}
