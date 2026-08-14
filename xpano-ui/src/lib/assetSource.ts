import { convertFileSrc } from '@tauri-apps/api/core'

type AssetConverter = (path: string) => string

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
}

export function resolveAssetSource(path: string, tauriRuntime: boolean, converter: AssetConverter) {
  if (!path || /^(?:https?:|asset:|blob:|data:)/i.test(path)) return path
  if (!tauriRuntime) return /^[a-z]:[\\/]/i.test(path) ? '' : path
  return converter(path)
}

export function assetSource(path: string) {
  return resolveAssetSource(path, isTauriRuntime(), convertFileSrc)
}
