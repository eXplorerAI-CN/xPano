import { useEffect, useRef } from 'react'
import { AlertTriangle } from 'lucide-react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Lightweight modal confirmation dialog.
 *
 * A Tauri desktop app shouldn't use the native window.confirm() — it looks
 * alien next to the glass UI. This renders an in-app modal that matches the
 * design system, traps focus on the confirm button, and closes on Escape.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    confirmRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div
      className="app-modal-backdrop fixed inset-0 z-[60] flex items-center justify-center overflow-hidden p-4 sm:p-6"
      onClick={onCancel}
    >
      <div
        className="app-modal-panel relative max-h-[calc(100vh-2rem)] w-full max-w-sm overflow-y-auto p-5 animate-in fade-in zoom-in-95 duration-200 sm:max-h-[calc(100vh-3rem)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <span
            className={`app-modal-icon grid h-9 w-9 shrink-0 place-items-center rounded-comfortable ${danger ? 'is-danger' : ''}`}
            style={danger ? ({ '--icon-proximity': 0 } as React.CSSProperties) : undefined}
          >
            <AlertTriangle className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[14px] font-semibold text-ink">{title}</h3>
            <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted">{message}</p>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            onClick={onCancel}
            className="app-modal-secondary motion-press rounded-comfortable px-4 py-2 text-[13px] font-medium transition-all hover:-translate-y-0.5"
          >
            {cancelText}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={`theme-action-shadow motion-press inline-flex items-center gap-1.5 rounded-comfortable px-4 py-2 text-[13px] font-semibold text-white transition-all hover:-translate-y-0.5 ${
              danger ? 'bg-danger hover:brightness-110' : 'bg-brand hover:bg-brand-hover'
            }`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
