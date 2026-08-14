import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, FolderOpen, Images, Box, ScanLine, Sparkles } from 'lucide-react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import gsap from 'gsap'
import { ThemeControls } from '../components/layout/ThemeControls'
import { BrandAboutButton } from '../components/layout/BrandAboutButton'
import { WindowControls } from '../components/layout/WindowControls'
import { RuntimeReadinessBadge } from '../components/layout/RuntimeReadinessBadge'
import { JobBar } from '../features/jobs/JobBar'
import { ResultsWorkspace } from '../features/results/ResultsWorkspace'
import { normalizeDisplayPath } from '../lib/paths'
import type { ProjectWorkspace } from '../lib/contracts'
import type { ResolvedTheme, ThemeMode } from '../lib/types'
import { useProject } from './useProject'
import { resultsWorkspaceState } from './workspaceRetention'

interface AppShellProps {
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
  onThemeModeChange: (mode: ThemeMode) => void
}

const workspaceItems: Array<{ workspace: ProjectWorkspace; path: string; label: string; icon: typeof Images }> = [
  { workspace: 'media', path: '/project/media', label: '素材与处理', icon: Images },
  { workspace: 'reconstruction', path: '/project/reconstruction', label: '对齐与重建', icon: ScanLine },
  { workspace: 'results', path: '/project/results', label: '成果与后处理', icon: Box },
  { workspace: 'training', path: '/project/training', label: '高斯训练', icon: Sparkles },
]

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function AppShell({ themeMode, resolvedTheme, onThemeModeChange }: AppShellProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const workspaceRef = useRef<HTMLDivElement>(null)
  const {
    project,
    displayName,
    displayPath,
    saveState,
    error,
    openProject,
    renameProject,
    setWorkspace,
    setViewerOnlyPath,
    queueDropPaths,
  } = useProject()
  const [nameDraft, setNameDraft] = useState(displayName)
  const [resultsMounted, setResultsMounted] = useState(location.pathname === '/project/results')
  const resultsState = resultsWorkspaceState(resultsMounted, location.pathname)
  const batchTaskId = new URLSearchParams(location.search).get('batchTask')

  useEffect(() => {
    if (resultsState.mounted && !resultsMounted) setResultsMounted(true)
  }, [resultsMounted, resultsState.mounted])

  useEffect(() => setNameDraft(displayName), [displayName])

  useEffect(() => {
    const node = workspaceRef.current
    if (!node) return
    const context = gsap.context(() => {
      gsap.fromTo(node, { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, duration: 0.3, ease: 'power2.out', clearProps: 'opacity,visibility,transform' })
    }, node)
    return () => context.revert()
  }, [location.pathname])

  useEffect(() => {
    if (!isTauriRuntime()) return
    let disposed = false
    let unlisten: (() => void) | undefined
    getCurrentWindow().onDragDropEvent(async (event) => {
      if (disposed || event.payload.type !== 'drop') return
      const paths = event.payload.paths.map(normalizeDisplayPath).filter(Boolean)
      if (!paths.length) return
      const opened = await openProject(paths[0])
      if (opened) {
        navigate(`/project/${opened.project.activeWorkspace}`)
        return
      }
      const previewDir = await invoke<string | null>('resolve_colmap_preview_dir', { paths }).catch(() => null)
      if (previewDir) {
        setViewerOnlyPath(normalizeDisplayPath(previewDir))
        navigate('/project/results')
        return
      }
      queueDropPaths(paths)
      navigate('/project/media')
    }).then((stop) => {
      if (disposed) stop()
      else unlisten = stop
    }).catch(() => {})
    return () => {
      disposed = true
      unlisten?.()
    }
  }, [navigate, openProject, queueDropPaths, setViewerOnlyPath])

  const browseProject = async () => {
    const selected = await openDialog({ directory: true })
    if (!selected || Array.isArray(selected)) return
    const opened = await openProject(normalizeDisplayPath(selected))
    if (opened) navigate(`/project/${opened.project.activeWorkspace}`)
  }

  const navigateWorkspace = async (workspace: ProjectWorkspace, path: string) => {
    navigate(batchTaskId ? `${path}?batchTask=${encodeURIComponent(batchTaskId)}` : path)
    await setWorkspace(workspace)
  }

  const saveName = async () => {
    const next = nameDraft.trim()
    if (!project || !next || next === project.name) {
      setNameDraft(displayName)
      return
    }
    await renameProject(next)
  }

  let projectStateLabel = '未打开工程'
  let projectStateClass = 'bg-ink/25'
  if (project) {
    projectStateLabel = saveState === 'saving' ? '正在保存' : saveState === 'error' ? error || '保存失败' : '工程已保存'
    projectStateClass = saveState === 'saving' ? 'bg-warning' : saveState === 'error' ? 'bg-danger' : 'bg-success'
  }

  return (
    <div className="app-shell relative z-10 h-screen min-h-[720px] min-w-[1024px] overflow-hidden text-ink">
      <header className="liquid-topbar app-titlebar drag-region flex min-w-0 items-center justify-between px-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandAboutButton />
          <span className="titlebar-section-divider" />
          <div className="no-drag flex min-w-0 items-center gap-2" title={displayPath ? `${projectStateLabel} · ${displayPath}` : projectStateLabel}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${projectStateClass}`} />
            <input
              value={nameDraft}
              disabled={!project}
              onChange={(event) => setNameDraft(event.target.value)}
              onBlur={saveName}
              onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }}
              className="titlebar-project-name min-w-0 bg-transparent text-[11px] font-semibold text-ink outline-none disabled:text-muted"
              aria-label="工程名称"
            />
          </div>
          {batchTaskId && (
            <button
              type="button"
              onClick={() => navigate('/batch')}
              className="no-drag glass-control motion-press flex h-7 shrink-0 items-center gap-1 rounded-subtle px-2 text-[10px] text-muted hover:text-brand"
            >
              <ArrowLeft className="h-3 w-3" />
              返回批量任务
            </button>
          )}
        </div>
        <div className="topbar-control-group no-drag flex shrink-0 items-center gap-1">
          <RuntimeReadinessBadge />
          <button type="button" onClick={browseProject} className="glass-control motion-press flex h-8 shrink-0 items-center gap-1.5 rounded-comfortable px-3 text-[11px] font-medium text-ink/70 hover:text-brand">
            <FolderOpen className="h-3.5 w-3.5" /> 打开工程
          </button>
          <span className="topbar-control-divider" />
          <ThemeControls themeMode={themeMode} onThemeModeChange={onThemeModeChange} />
          <span className="topbar-control-divider" />
          <WindowControls />
        </div>
      </header>

      <main ref={workspaceRef} className="app-workspace min-h-0 overflow-hidden">
        {resultsState.mounted && (
          <div className={resultsState.active ? 'h-full' : 'hidden'} aria-hidden={!resultsState.active}>
            <ResultsWorkspace resolvedTheme={resolvedTheme} active={resultsState.active} />
          </div>
        )}
        {!resultsState.active && <Outlet />}
      </main>

      <footer className="app-bottom-dock min-w-0" aria-label="工作区与任务状态">
        <nav className="app-workspace-nav flex min-w-0 items-center gap-1" aria-label="工程工作区">
          {workspaceItems.map((item) => {
            const active = location.pathname === item.path
            const Icon = item.icon
            return (
              <button
                key={item.workspace}
                type="button"
                onClick={() => navigateWorkspace(item.workspace, item.path)}
                className={`workspace-switcher-button motion-press flex min-w-0 flex-1 items-center justify-center gap-2 text-[11px] font-medium ${active ? 'is-active' : ''}`}
                aria-current={active ? 'page' : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{item.label}</span>
              </button>
            )
          })}
        </nav>
        <span className="bottom-dock-divider" />
        <JobBar />
      </footer>
    </div>
  )
}
