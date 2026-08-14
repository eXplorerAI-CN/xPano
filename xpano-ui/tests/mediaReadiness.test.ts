import assert from 'node:assert/strict'
import test from 'node:test'
import type { ProjectTrack } from '../src/lib/contracts.ts'
import { evaluateMediaReadiness } from '../src/lib/mediaReadiness.ts'

function track(label: string, status: ProjectTrack['status'], selected = true): ProjectTrack {
  return {
    id: label,
    type: 'standard_photos',
    label,
    sourcePath: `D:/${label}`,
    sourceFingerprint: { size: 1, mtimeNs: 1 },
    cameraProfile: null,
    trim: null,
    extraction: { framesPerSecond: 1, frameLimit: 0 },
    status,
    items: [{ id: `${label}-1`, selected }],
  }
}

test('ready tracks with selected items and an alignment manifest can continue', () => {
  const readiness = evaluateMediaReadiness(
    [track('photos', 'ready'), track('aerial', 'prepared')],
    'work/manifests/alignment_00000001.json',
  )

  assert.equal(readiness.canContinue, true)
  assert.equal(readiness.readyTrackCount, 2)
  assert.equal(readiness.selectedItemCount, 2)
  assert.equal(readiness.blockReason, '')
})

test('a failed track blocks alignment and names the exact track', () => {
  const readiness = evaluateMediaReadiness(
    [track('photos', 'ready'), track('old panorama', 'failed')],
    'work/manifests/alignment_00000001.json',
  )

  assert.equal(readiness.canContinue, false)
  assert.match(readiness.blockReason, /old panorama/)
  assert.match(readiness.blockReason, /failed/)
})

test('a missing alignment manifest blocks the next step even when tracks are ready', () => {
  const readiness = evaluateMediaReadiness([track('photos', 'ready')], '')

  assert.equal(readiness.canContinue, false)
  assert.match(readiness.blockReason, /manifest/)
})
