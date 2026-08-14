import assert from 'node:assert/strict'
import test from 'node:test'
import { shouldAutoOpenResults } from '../src/features/reconstruction/reconstructionCompletion.ts'

test('opens results only after the reconstruction started in this workspace is complete', () => {
  assert.equal(shouldAutoOpenResults(true, false, 'complete', 4, 4), true)
  assert.equal(shouldAutoOpenResults(false, false, 'complete', 4, 4), false)
  assert.equal(shouldAutoOpenResults(true, true, 'complete', 4, 4), false)
  assert.equal(shouldAutoOpenResults(true, false, 'stale', 0, 4), false)
  assert.equal(shouldAutoOpenResults(true, false, 'complete', 3, 4), false)
})
