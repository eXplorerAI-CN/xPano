import { CheckCircle2, Info, AlertTriangle, XCircle, X } from 'lucide-react'
import { cn } from '../../lib/utils'
import type { Toast as ToastType } from '../../hooks/useToast'

type ToastKind = ToastType['type']

const config: Record<ToastKind, { icon: typeof Info; accent: string; ring: string }> = {
  info: { icon: Info, accent: 'text-brand', ring: 'border-brand/25' },
  success: { icon: CheckCircle2, accent: 'text-success', ring: 'border-success/25' },
  warning: { icon: AlertTriangle, accent: 'text-warning', ring: 'border-warning/25' },
  error: { icon: XCircle, accent: 'text-danger', ring: 'border-danger/25' },
}

function ToastItem({ toast, onRemove }: { toast: ToastType; onRemove: (id: string) => void }) {
  const { icon: Icon, accent, ring } = config[toast.type]
  return (
    <div
      className={cn(
        'toast-surface flex max-w-full items-start gap-3 overflow-hidden rounded-card px-4 py-3 ring-1',
        ring,
        'animate-in slide-in-from-right-4 fade-in duration-300'
      )}
    >
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', accent)} />
      <p className="min-w-0 flex-1 break-words text-[13px] leading-relaxed text-ink/82">{toast.message}</p>
      <button
        onClick={() => onRemove(toast.id)}
        className="motion-press -mr-1 -mt-0.5 shrink-0 rounded p-1 text-ink/30 transition-colors hover:bg-ink/5 hover:text-ink/70"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

function ToastContainer({
  toasts,
  onRemove,
}: {
  toasts: ToastType[]
  onRemove: (id: string) => void
}) {
  if (toasts.length === 0) return null

  return (
    <div className="flex w-full max-w-full flex-col gap-2 overflow-hidden">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onRemove={onRemove} />
      ))}
    </div>
  )
}

export { ToastContainer }
