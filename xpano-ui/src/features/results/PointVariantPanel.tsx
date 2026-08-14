import { Check, CircleDot, LoaderCircle, Trash2 } from 'lucide-react'
import type { PointCloudVariant } from '../../lib/contracts'
import { canActivateVariant, canDeleteVariant } from './pointVariants'

const statusText: Record<PointCloudVariant['status'], string> = {
  ready: '就绪',
  stale: '已过期',
  missing: '缺失',
  corrupt: '损坏',
}

function pointCount(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

interface PointVariantPanelProps {
  variants: PointCloudVariant[]
  activeVariantId: string
  previewVariantId: string | null
  busyVariantId: string | null
  onPreview: (variant: PointCloudVariant) => void
  onActivate: (variant: PointCloudVariant) => void
  onDelete: (variant: PointCloudVariant) => void
}

export function PointVariantPanel({
  variants,
  activeVariantId,
  previewVariantId,
  busyVariantId,
  onPreview,
  onActivate,
  onDelete,
}: PointVariantPanelProps) {
  if (!variants.length) return null
  return (
    <aside className="pointer-events-auto absolute right-4 top-16 z-30 w-[300px] overflow-hidden rounded-comfortable border border-white/10 bg-[#07131f]/88 text-white shadow-2xl shadow-black/30 backdrop-blur-xl">
      <div className="border-b border-white/8 px-4 py-3">
        <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/38">训练点云</p>
        <p className="mt-1 text-[11px] text-white/56">单击先预览，确认后再设为训练输入</p>
      </div>
      <div className="max-h-[320px] space-y-1 overflow-y-auto p-2">
        {variants.map((variant) => {
          const active = variant.id === activeVariantId
          const previewing = variant.id === previewVariantId
          const busy = variant.id === busyVariantId
          return (
            <div key={variant.id} className={`rounded-subtle border px-3 py-2 transition-colors ${previewing ? 'border-aurora/45 bg-aurora/10' : 'border-transparent hover:bg-white/5'}`}>
              <button
                type="button"
                disabled={variant.status !== 'ready' || busy}
                onClick={() => onPreview(variant)}
                className="flex w-full items-start gap-2 text-left disabled:cursor-not-allowed disabled:opacity-45"
              >
                {busy ? <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-aurora" /> : <CircleDot className={`mt-0.5 h-4 w-4 shrink-0 ${previewing ? 'text-aurora' : 'text-white/28'}`} />}
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[12px] font-medium text-white/88">{variant.label}</span>
                    {active && <span className="rounded-full bg-success/15 px-1.5 py-0.5 text-[9px] text-success">当前</span>}
                  </span>
                  <span className="mt-0.5 flex gap-2 text-[10px] text-white/38">
                    <span>{pointCount(variant.pointCount)} 点</span>
                    <span>{statusText[variant.status]}</span>
                  </span>
                </span>
              </button>
              <div className="mt-2 flex justify-end gap-1.5">
                {canDeleteVariant(variant, activeVariantId) && (
                  <button type="button" disabled={busy} onClick={() => onDelete(variant)} className="motion-press inline-flex h-7 items-center gap-1 rounded-subtle px-2 text-[10px] text-danger/80 hover:bg-danger/10">
                    <Trash2 className="h-3 w-3" /> 删除
                  </button>
                )}
                {canActivateVariant(variant, activeVariantId) && (
                  <button type="button" disabled={busy} onClick={() => onActivate(variant)} className="motion-press inline-flex h-7 items-center gap-1 rounded-subtle bg-aurora/14 px-2 text-[10px] text-aurora hover:bg-aurora/20">
                    <Check className="h-3 w-3" /> 设为训练点云
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
