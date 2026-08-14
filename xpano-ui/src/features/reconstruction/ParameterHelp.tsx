import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CircleHelp, X } from 'lucide-react'

interface ParameterHelpProps {
  title: string
  description: string
  recommendation: string
  tradeoff: string
}

export function ParameterHelp({ title, description, recommendation, tradeoff }: ParameterHelpProps) {
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [position, setPosition] = useState({ top: 0, left: 0 })

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const width = 312
    const left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.right + 8))
    const top = Math.min(window.innerHeight - 300, Math.max(12, rect.top - 18))
    setPosition({ top, left })
  }, [open])

  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [open])

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="motion-press grid h-6 w-6 shrink-0 place-items-center rounded-full text-muted hover:bg-brand/8 hover:text-brand focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand/40"
        aria-label={`${title} 参数说明`}
        aria-expanded={open}
      >
        <CircleHelp className="h-3.5 w-3.5" />
      </button>
      {open && createPortal(
        <div
          className="parameter-help-popover fixed z-[180] max-h-[280px] w-[312px] overflow-y-auto rounded-comfortable border border-[var(--xp-line-strong)] bg-[rgb(var(--xp-surface-rgb))] p-3.5 text-[11px] shadow-[var(--xp-shadow-hover)]"
          style={position}
          role="dialog"
          aria-label={`${title} 参数说明`}
        >
          <div className="flex items-start justify-between gap-3">
            <h3 className="text-[12px] font-semibold text-ink">{title}</h3>
            <button type="button" onClick={() => setOpen(false)} className="motion-press grid h-6 w-6 place-items-center rounded-subtle text-muted hover:text-ink" aria-label="关闭说明"><X className="h-3.5 w-3.5" /></button>
          </div>
          <p className="mt-2 leading-5 text-muted">{description}</p>
          <dl className="mt-3 space-y-2 border-t border-[var(--xp-line)] pt-3">
            <div><dt className="font-medium text-ink">当前工程建议</dt><dd className="mt-0.5 leading-5 text-muted">{recommendation}</dd></div>
            <div><dt className="font-medium text-ink">调整影响</dt><dd className="mt-0.5 leading-5 text-muted">{tradeoff}</dd></div>
          </dl>
        </div>,
        document.body,
      )}
    </>
  )
}
