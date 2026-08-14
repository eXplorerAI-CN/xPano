import { Layers3, X } from 'lucide-react'
import type { ComponentInspection } from './reconstructionComponents'

interface ComponentSelectionDialogProps {
  inspection: ComponentInspection
  currentExportedComponentKey: string
  selectedComponentKey: string
  onSelect: (key: string) => void
  onCancel: () => void
  onConfirm: () => void
}

export function ComponentSelectionDialog({ inspection, currentExportedComponentKey, selectedComponentKey, onSelect, onCancel, onConfirm }: ComponentSelectionDialogProps) {
  return (
    <div className="fixed inset-0 z-[180] grid place-items-center bg-black/35 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="选择导出 Component" onMouseDown={onCancel}>
      <section className="liquid-panel flex max-h-[min(620px,calc(100vh-32px))] w-[min(560px,calc(100vw-32px))] flex-col overflow-hidden p-0" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--xp-line)] px-4">
          <div className="flex min-w-0 items-center gap-2.5"><Layers3 className="h-4 w-4 shrink-0 text-brand" /><div className="min-w-0"><h2 className="text-[13px] font-semibold text-ink">选择导出 Component</h2><p className="truncate text-[10px] text-muted">当前结果：{currentExportedComponentKey ? `Component #${currentExportedComponentKey}` : '尚未记录'}</p></div></div>
          <button type="button" onClick={onCancel} className="motion-press grid h-8 w-8 place-items-center rounded-comfortable text-muted hover:bg-ink/[0.04] hover:text-ink" aria-label="取消"><X className="h-4 w-4" /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="space-y-2">
            {inspection.components.map((component) => {
              const selected = component.componentKey === selectedComponentKey
              return <label key={component.componentKey} className={`flex cursor-pointer items-center gap-3 rounded-comfortable border px-3 py-3 transition-colors ${selected ? 'border-brand/45 bg-brand/[0.06]' : 'border-[var(--xp-line)] hover:bg-ink/[0.025]'}`}>
                <input type="radio" name="metashape-component" value={component.componentKey} checked={selected} onChange={() => onSelect(component.componentKey)} className="h-4 w-4 accent-[rgb(var(--xp-brand-rgb))]" />
                <span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-3"><span className="truncate text-[12px] font-medium text-ink">{component.label || `Component #${component.componentKey}`}</span><span className="shrink-0 font-mono text-[10px] text-muted">#{component.componentKey}</span></span><span className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted"><span>{component.alignedCameraCount} 台已对齐相机</span><span>{component.tiePointCount.toLocaleString()} 个连接点</span></span></span>
              </label>
            })}
          </div>
          <p className="mt-3 text-[10px] leading-4 text-warning">仅导出所选 Component。存在多个 Component 通常表示对齐未完全连通，建议在 Metashape 中检查并保存后再导出。</p>
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-[var(--xp-line)] p-3">
          <button type="button" onClick={onCancel} className="glass-control motion-press h-9 px-4 text-[11px] font-medium text-ink/70">取消</button>
          <button type="button" onClick={onConfirm} disabled={!selectedComponentKey} className="motion-press h-9 rounded-comfortable bg-brand px-4 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45">导出所选 Component</button>
        </footer>
      </section>
    </div>
  )
}
