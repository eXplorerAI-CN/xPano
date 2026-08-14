import { getVersion } from '@tauri-apps/api/app'
import { Info } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { brandAbout } from '../../app/brandAbout'

type PopoverPosition = { left: number; top: number }

export function BrandAboutButton() {
  const anchorRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState<string>(brandAbout.fallbackVersion)
  const [position, setPosition] = useState<PopoverPosition>({ left: 0, top: 0 })

  useLayoutEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const rect = anchorRef.current?.getBoundingClientRect()
      if (!rect) return
      setPosition({ left: rect.left, top: rect.bottom + 8 })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    return () => window.removeEventListener('resize', updatePosition)
  }, [open])

  useEffect(() => {
    if (!open) return
    void getVersion().then(setVersion).catch(() => {})
  }, [open])

  useEffect(() => {
    if (!open) return
    const closeWhenOutside = (event: PointerEvent) => {
      const target = event.target as Node
      if (anchorRef.current?.contains(target) || popoverRef.current?.contains(target)) return
      setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeWhenOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeWhenOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="no-drag motion-press flex h-8 shrink-0 items-center gap-2 rounded-subtle px-1 text-ink hover:bg-ink/[0.05]"
        aria-label="关于 xPano"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <img src="/icon.png" alt="" className="h-6 w-6 rounded-subtle" />
        <span className="text-[13px] font-semibold">xPano</span>
      </button>
      {open && createPortal(
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="关于 xPano"
          style={{ left: position.left, top: position.top }}
          className="parameter-help-popover fixed z-[100] w-72 rounded-subtle border border-ink/[0.12] bg-[rgb(var(--xp-surface-rgb)/0.97)] p-3.5 shadow-[0_16px_40px_rgb(8_20_36/0.24)]"
        >
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-subtle bg-brand/10 text-brand"><Info className="h-3.5 w-3.5" /></span>
            <div className="min-w-0">
              <p className="text-[12px] font-semibold text-ink">关于 xPano</p>
              <p className="mt-0.5 font-mono text-[10px] text-muted">版本 {version}</p>
            </div>
          </div>
          <div className="mt-3 border-t border-ink/[0.08] pt-2.5 text-[11px] leading-5 text-muted">
            <p className="font-medium text-ink">{brandAbout.publisher}</p>
            <p className="mt-0.5">{brandAbout.contributors}</p>
            <p className="mt-0.5">{brandAbout.thirdPartyContributors}</p>
            <p className="mt-1.5 text-[10px] leading-4 text-muted/85">{brandAbout.license}</p>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
