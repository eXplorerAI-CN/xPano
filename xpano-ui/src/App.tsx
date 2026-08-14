import { useEffect, useRef, useState } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ParticleBackground } from './components/effects/ParticleBackground'
import { DragTrail } from './components/effects/DragTrail'
import { MediaWorkspace } from './features/media/MediaWorkspace'
import { ReconstructionWorkspace } from './features/reconstruction/ReconstructionWorkspace'
import { TrainingWorkspace } from './features/training/TrainingWorkspace'
import { AppShell } from './app/AppShell'
import { JobProvider } from './app/JobProvider'
import { useJob } from './app/useJob'
import { ProjectProvider } from './app/ProjectProvider'
import { BatchProvider } from './app/BatchProvider'
import { DEFAULT_PROJECT_PATH } from './app/routes'
import type { ResolvedTheme, ThemeMode } from './lib/types'

const themeModes: ThemeMode[] = ['system', 'light', 'dark']

function readThemeMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  const saved = window.localStorage.getItem('xpano-theme-mode') as ThemeMode | null
  return saved && themeModes.includes(saved) ? saved : 'system'
}

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

interface AppContentProps {
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
  onThemeModeChange: (mode: ThemeMode) => void
}

function AppContent({ themeMode, resolvedTheme, onThemeModeChange }: AppContentProps) {
  const { running, progress } = useJob()
  return (
    <>
      <ParticleBackground active={running} phase={progress.phase} />
      <DragTrail />
      <Routes>
        <Route path="/batch/*" element={<Navigate to={DEFAULT_PROJECT_PATH} replace />} />
        <Route element={<AppShell themeMode={themeMode} resolvedTheme={resolvedTheme} onThemeModeChange={onThemeModeChange} />}>
          <Route path="/project/media" element={<MediaWorkspace />} />
          <Route path="/project/reconstruction" element={<ReconstructionWorkspace />} />
          <Route path="/project/results" element={null} />
          <Route path="/project/training" element={<TrainingWorkspace />} />
        </Route>
        <Route path="*" element={<Navigate to={DEFAULT_PROJECT_PATH} replace />} />
      </Routes>
    </>
  )
}

function App() {
  const [themeMode, setThemeMode] = useState<ThemeMode>(readThemeMode)
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(getSystemTheme)
  const firstThemeApply = useRef(true)
  const resolvedTheme = themeMode === 'system' ? systemTheme : themeMode

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystemTheme(media.matches ? 'dark' : 'light')
    onChange()
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = resolvedTheme
    root.dataset.themeMode = themeMode
    root.style.colorScheme = resolvedTheme
    window.localStorage.setItem('xpano-theme-mode', themeMode)

    if (firstThemeApply.current) {
      firstThemeApply.current = false
      return
    }

    root.classList.add('theme-is-switching')
    const timeout = window.setTimeout(() => root.classList.remove('theme-is-switching'), 460)
    return () => {
      window.clearTimeout(timeout)
      root.classList.remove('theme-is-switching')
    }
  }, [resolvedTheme, themeMode])

  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => event.preventDefault()
    document.addEventListener('contextmenu', onContextMenu)
    return () => document.removeEventListener('contextmenu', onContextMenu)
  }, [])

  useEffect(() => {
    const timers = new WeakMap<HTMLElement, number>()
    const activeTimers = new Set<number>()
    const onPointerDown = (event: PointerEvent) => {
      const rawTarget = event.target
      if (!(rawTarget instanceof Element)) return
      const target = rawTarget.closest('button:not(:disabled), [role="button"]') as HTMLElement | null
      if (!target) return
      const existing = timers.get(target)
      if (existing) {
        window.clearTimeout(existing)
        activeTimers.delete(existing)
      }
      target.classList.remove('is-pressing')
      void target.offsetWidth
      target.classList.add('is-pressing')
      const timeout = window.setTimeout(() => {
        target.classList.remove('is-pressing')
        timers.delete(target)
        activeTimers.delete(timeout)
      }, 320)
      timers.set(target, timeout)
      activeTimers.add(timeout)
    }
    window.addEventListener('pointerdown', onPointerDown, { passive: true })
    return () => {
      window.removeEventListener('pointerdown', onPointerDown)
      activeTimers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [])

  return (
    <HashRouter>
      <ProjectProvider>
        <JobProvider>
          <BatchProvider>
            <AppContent themeMode={themeMode} resolvedTheme={resolvedTheme} onThemeModeChange={setThemeMode} />
          </BatchProvider>
        </JobProvider>
      </ProjectProvider>
    </HashRouter>
  )
}

export default App
