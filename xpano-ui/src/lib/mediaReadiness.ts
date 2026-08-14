import type { ProjectTrack, ProjectTrackStatus } from './contracts'

const READY_STATUSES = new Set<ProjectTrackStatus>(['prepared', 'ready'])

export interface MediaReadiness {
  readyTrackCount: number
  selectedItemCount: number
  unreadyTracks: Array<{ id: string; label: string; status: ProjectTrackStatus }>
  canContinue: boolean
  blockReason: string
}

export function evaluateMediaReadiness(
  tracks: readonly ProjectTrack[],
  alignmentManifestPath: unknown,
): MediaReadiness {
  const unreadyTracks = tracks
    .filter((track) => !READY_STATUSES.has(track.status))
    .map(({ id, label, status }) => ({ id, label, status }))
  const readyTrackCount = tracks.length - unreadyTracks.length
  const selectedItemCount = tracks.reduce(
    (total, track) => total + track.items.reduce((count, item) => count + Number(item.selected), 0),
    0,
  )
  const hasManifest = typeof alignmentManifestPath === 'string' && alignmentManifestPath.trim().length > 0

  let blockReason = ''
  if (tracks.length === 0) {
    blockReason = '工程中还没有素材轨道'
  } else if (unreadyTracks.length > 0) {
    const details = unreadyTracks.map((track) => `${track.label}（${track.status}）`).join('、')
    blockReason = `以下轨道尚未准备完成：${details}`
  } else if (selectedItemCount === 0) {
    blockReason = '至少选择一个参与对齐的素材项'
  } else if (!hasManifest) {
    blockReason = '缺少对齐清单（alignment manifest），请重新准备素材'
  }

  return {
    readyTrackCount,
    selectedItemCount,
    unreadyTracks,
    canContinue: blockReason === '',
    blockReason,
  }
}
