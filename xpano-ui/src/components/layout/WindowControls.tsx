import { useState, useEffect } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function WindowControls() {
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    if (!isTauriRuntime()) return
    try {
      const win = getCurrentWindow()
      win.isMaximized().then(setMaximized).catch(() => {})
      const p = listen('tauri://resize', () => { win.isMaximized().then(setMaximized).catch(() => {}) })
      return () => { p.then((fn: () => void) => fn()).catch(() => {}) }
    } catch {
      return
    }
  }, [])

  const runWindowCommand = (command: string) => {
    if (!isTauriRuntime()) return
    invoke(command).catch(() => {})
  }

  return (
    <div className="flex items-center">
      {/* Minimize */}
      <button
        onClick={() => runWindowCommand('window_minimize')}
        className="topbar-icon-button motion-press grid h-7 w-7 place-items-center rounded-subtle transition-all"
        aria-label="最小化"
        title="最小化"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <rect x="3" y="6" width="6" height="1.4" rx="0.7" fill="currentColor" />
        </svg>
      </button>

      {/* Maximize / Restore */}
      <button
        onClick={() => runWindowCommand('window_toggle_maximize')}
        className="topbar-icon-button motion-press grid h-7 w-7 place-items-center rounded-subtle transition-all"
        aria-label={maximized ? '还原' : '最大化'}
        title={maximized ? '还原' : '最大化'}
      >
        {maximized ? (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <rect x="4.25" y="2.5" width="5" height="5" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
            <rect x="2.75" y="4" width="5" height="5" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <rect x="3.25" y="3.25" width="5.5" height="5.5" rx="1.1" fill="none" stroke="currentColor" strokeWidth="1.3" />
          </svg>
        )}
      </button>

      {/* Close */}
      <button
        onClick={() => runWindowCommand('window_close')}
        className="topbar-icon-button topbar-icon-button-danger motion-press grid h-7 w-7 place-items-center rounded-subtle transition-all"
        aria-label="关闭"
        title="关闭"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <line x1="3.5" y1="3.5" x2="8.5" y2="8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          <line x1="8.5" y1="3.5" x2="3.5" y2="8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  )
}
