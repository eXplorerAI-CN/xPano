import type { ProjectMediaItem } from './contracts'

export function mergeMediaItemBatch(
  current: ProjectMediaItem[],
  incoming: ProjectMediaItem[],
  limit: number,
): ProjectMediaItem[] {
  if (incoming.length === 0) return current
  const byId = new Map(current.map((item) => [item.id, item]))
  incoming.forEach((item) => byId.set(item.id, item))
  const merged = Array.from(byId.values())
  const safeLimit = Math.max(1, Math.floor(limit))
  return merged.length > safeLimit ? merged.slice(-safeLimit) : merged
}

export function appendBoundedLog(previous: string[], line: string, limit: number): string[] {
  if (!line.trim()) return previous
  const body = line.replace(/^\d{2}:\d{2} · /, '')
  const previousBody = previous[previous.length - 1]?.replace(/^\d{2}:\d{2} · /, '') ?? ''
  if (body === previousBody) return previous
  const next = [...previous, line]
  const safeLimit = Math.max(1, Math.floor(limit))
  return next.length > safeLimit ? next.slice(-safeLimit) : next
}
