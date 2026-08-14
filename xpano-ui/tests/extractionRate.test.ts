import assert from 'node:assert/strict'
import test from 'node:test'
import { framesPerSecondForLimit, frameTimestamp } from '../src/lib/extractionRate.ts'

test('frame limit is converted to frames per second', () => {
  assert.equal(framesPerSecondForLimit(5, 10, 1), 2)
  assert.equal(framesPerSecondForLimit(0, 10, 1), 1)
  assert.equal(framesPerSecondForLimit(5, 0, 1), 1)
})

test('timestamps advance by the reciprocal of frames per second', () => {
  assert.equal(frameTimestamp(3, 3, 2), 4)
})
