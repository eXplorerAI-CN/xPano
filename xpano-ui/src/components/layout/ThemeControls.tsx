import { Monitor, Moon, Sun } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'
import type { ThemeMode } from '../../lib/types'

interface ThemeControlsProps {
  themeMode: ThemeMode
  onThemeModeChange: (mode: ThemeMode) => void
  className?: string
}

const modeItems: Array<{ mode: ThemeMode; label: string; icon: ReactNode }> = [
  { mode: 'system', label: '跟随系统', icon: <Monitor className="h-3.5 w-3.5" /> },
  { mode: 'light', label: '浅色模式', icon: <Sun className="h-3.5 w-3.5" /> },
  { mode: 'dark', label: '深色模式', icon: <Moon className="h-3.5 w-3.5" /> },
]

export function ThemeControls({
  themeMode,
  onThemeModeChange,
  className,
}: ThemeControlsProps) {
  return (
    <div className={cn('flex items-center', className)}>
      {modeItems.map((item) => {
        const active = themeMode === item.mode
        return (
          <button
            key={item.mode}
            aria-label={item.label}
            title={item.label}
            type="button"
            onClick={() => onThemeModeChange(item.mode)}
            className={cn(
              'topbar-icon-button motion-press grid h-7 w-7 place-items-center rounded-subtle transition-all',
              active ? 'is-active' : ''
            )}
          >
            {item.icon}
          </button>
        )
      })}
    </div>
  )
}
