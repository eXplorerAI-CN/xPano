import assert from 'node:assert/strict'
import test from 'node:test'
import {
  clearPointCloudSessionCache,
  invalidatePointCloudSessionEntries,
  loadPointCloudForSession,
} from '../src/lib/pointCloudSessionCache.ts'
import type { PointCloudData } from '../src/lib/types.ts'

function cloud(numPoints: number): PointCloudData {
  return {
    points: new Float32Array(numPoints * 3),
    colors: new Float32Array(numPoints * 3),
    numPoints,
    totalPoints: numPoints,
    sampled: false,
    cameras: [],
    preparedForThree: true,
  }
}

test('concurrent requests for the same cloud share one in-flight load', async () => {
  clearPointCloudSessionCache()
  let loads = 0
  let release!: (value: PointCloudData) => void
  const pending = new Promise<PointCloudData>((resolve) => { release = resolve })
  const loader = async () => {
    loads++
    return pending
  }

  const first = loadPointCloudForSession('project::standard', loader)
  const second = loadPointCloudForSession('project::standard', loader)
  release(cloud(631_855))

  const [firstResult, secondResult] = await Promise.all([first, second])
  assert.equal(loads, 1)
  assert.equal(firstResult, secondResult)
})

test('a resolved cloud remains cached until it is explicitly invalidated', async () => {
  clearPointCloudSessionCache()
  let loads = 0
  const loader = async () => cloud(++loads)

  const first = await loadPointCloudForSession('project::dense', loader)
  const retained = await loadPointCloudForSession('project::dense', loader)
  invalidatePointCloudSessionEntries((key) => key.endsWith('::dense'))
  const refreshed = await loadPointCloudForSession('project::dense', loader)

  assert.equal(first, retained)
  assert.equal(loads, 2)
  assert.notEqual(refreshed, first)
})
