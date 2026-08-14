import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { ShieldCheck, ShieldQuestion, ShieldX } from 'lucide-react'
import { runtimeReadinessPresentation, type RuntimeReadinessStatus } from '../../lib/runtimeReadiness'

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function RuntimeReadinessBadge() {
  const [environment, setEnvironment] = useState<RuntimeReadinessStatus | null>(null)

  useEffect(() => {
    if (!isTauriRuntime()) {
      setEnvironment({ bundled: 'ready', metashape: 'ready', densification: 'downloadable', detail: '' })
      return
    }
    invoke<RuntimeReadinessStatus>('probe_runtime_readiness')
      .then(setEnvironment)
      .catch((error) => setEnvironment({ bundled: 'corrupt', metashape: 'error', densification: 'downloadable', detail: String(error) }))
  }, [])

  const presentation = environment ? runtimeReadinessPresentation(environment) : null
  const meta = presentation?.tone === 'ready'
    ? { label: presentation.label, icon: ShieldCheck, className: 'text-success' }
    : presentation?.tone === 'error'
      ? { label: presentation.label, icon: ShieldX, className: 'text-danger' }
      : { label: presentation?.label || '环境检查中', icon: ShieldQuestion, className: 'text-warning' }
  const Icon = meta.icon

  return (
    <span
      className={`titlebar-environment flex h-7 items-center gap-1.5 px-2 text-[10px] font-medium ${meta.className}`}
      title={environment?.detail || `${meta.label}；致密化环境${environment?.densification === 'ready' ? '已就绪' : '可按需下载'}`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span className="hidden xl:inline">{meta.label}</span>
    </span>
  )
}
