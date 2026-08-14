import type { PointCloudData } from './types'

const resolvedClouds = new Map<string, PointCloudData>()
const activeLoads = new Map<string, Promise<PointCloudData | null>>()
const keyVersions = new Map<string, number>()
let sessionVersion = 0

export function loadPointCloudForSession(
  key: string,
  loader: () => Promise<PointCloudData | null>,
  bypassResolved = false,
) {
  if (!bypassResolved) {
    const resolved = resolvedClouds.get(key)
    if (resolved) return Promise.resolve(resolved)
  }
  const active = activeLoads.get(key)
  if (active) return active

  const expectedSession = sessionVersion
  const expectedKey = keyVersions.get(key) ?? 0
  const request = loader()
    .then((data) => {
      if (
        data
        && expectedSession === sessionVersion
        && expectedKey === (keyVersions.get(key) ?? 0)
      ) {
        resolvedClouds.set(key, data)
      }
      return data
    })
    .finally(() => {
      if (activeLoads.get(key) === request) activeLoads.delete(key)
    })
  activeLoads.set(key, request)
  return request
}

export function invalidatePointCloudSessionEntries(matches: (key: string) => boolean) {
  const keys = new Set([...resolvedClouds.keys(), ...activeLoads.keys()])
  for (const key of keys) {
    if (!matches(key)) continue
    resolvedClouds.delete(key)
    activeLoads.delete(key)
    keyVersions.set(key, (keyVersions.get(key) ?? 0) + 1)
  }
}

export function clearPointCloudSessionCache() {
  sessionVersion++
  resolvedClouds.clear()
  activeLoads.clear()
  keyVersions.clear()
}
