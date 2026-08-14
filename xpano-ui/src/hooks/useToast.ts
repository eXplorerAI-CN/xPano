import { useState, useCallback, useEffect, useMemo, useRef } from 'react'

export interface Toast {
  id: string
  type: 'info' | 'success' | 'error' | 'warning'
  message: string
}

let toastId = 0

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef<Map<string, number>>(new Map())

  const addToast = useCallback((type: Toast['type'], message: string) => {
    const id = String(++toastId)
    setToasts((prev) => [...prev, { id, type, message }])
    const timer = window.setTimeout(() => {
      timers.current.delete(id)
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
    timers.current.set(id, timer)
  }, [])

  const removeToast = useCallback((id: string) => {
    const timer = timers.current.get(id)
    if (timer) window.clearTimeout(timer)
    timers.current.delete(id)
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  useEffect(() => () => {
    timers.current.forEach((timer) => window.clearTimeout(timer))
    timers.current.clear()
  }, [])

  const toast = useMemo(() => ({
    info: (msg: string) => addToast('info', msg),
    success: (msg: string) => addToast('success', msg),
    error: (msg: string) => addToast('error', msg),
    warning: (msg: string) => addToast('warning', msg),
  }), [addToast])

  return { toasts, removeToast, toast }
}
