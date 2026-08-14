import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { FolderOpen, X } from 'lucide-react'
import { normalizeColorLutPath } from './colorLut'

interface ColorLutFieldProps {
  value?: string | null
  preset?: string | null
  builtinPreset?: string | null
  onChange: (path: string | null) => void
  onPresetChange?: (preset: string | null) => void
  label?: string
  disabled?: boolean
}

function fileName(path?: string | null) {
  return normalizeColorLutPath(path)?.split(/[/\\]/).pop() || '未使用'
}

export function ColorLutField({
  value,
  preset,
  builtinPreset,
  onChange,
  onPresetChange,
  label = '风格 LUT',
  disabled = false,
}: ColorLutFieldProps) {
  const selectLut = async () => {
    const selected = await openDialog({
      multiple: false,
      directory: false,
      filters: [{ name: '3D LUT', extensions: ['cube'] }],
    })
    if (!selected || Array.isArray(selected)) return
    onChange(normalizeColorLutPath(selected))
  }

  if (builtinPreset) {
    const enabled = preset === builtinPreset
    return (
      <label className="flex cursor-pointer items-center justify-between gap-3 rounded-comfortable border border-[var(--xp-line)] px-3 py-2.5">
        <span className="min-w-0">
          <span className="block text-[11px] font-medium text-ink">色彩还原</span>
          <span className="block truncate text-[10px] text-muted">DJI Osmo 360 D-Log M to Rec.709</span>
        </span>
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(event) => onPresetChange?.(event.target.checked ? builtinPreset : null)}
          className="h-4 w-4 shrink-0 accent-[var(--xp-brand)]"
          aria-label="启用内置色彩还原"
        />
      </label>
    )
  }

  return (
    <div>
      <p className="mb-1.5 text-[11px] font-medium text-muted">{label}</p>
      <div className="theme-input flex h-10 min-w-0 items-center gap-2 rounded-comfortable border px-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink" title={value || ''}>{fileName(value)}</span>
        {value && (
          <button type="button" onClick={() => onChange(null)} disabled={disabled} className="motion-press grid h-7 w-7 shrink-0 place-items-center rounded-comfortable text-muted hover:text-danger disabled:opacity-40" title="清除 LUT" aria-label="清除 LUT">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <button type="button" onClick={selectLut} disabled={disabled} className="motion-press grid h-7 w-7 shrink-0 place-items-center rounded-comfortable text-muted hover:text-brand disabled:opacity-40" title="选择 LUT" aria-label="选择 LUT">
          <FolderOpen className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
