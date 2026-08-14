import assert from 'node:assert/strict'
import test from 'node:test'
import { composeWorldFromThreePreview } from '../src/features/results/geometryPreview.ts'

const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] as const

test('three preview rotation is converted to COLMAP coordinates around the selected pivot', () => {
  const rotation180Z = [-1, 0, 0, 0, -1, 0, 0, 0, 1] as const
  const result = composeWorldFromThreePreview([...identity], [...rotation180Z], [1, 2, 3])

  assert.deepEqual(result, [
    -1, 0, 0, 2,
    0, -1, 0, -4,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ])
})

test('preview increment composes in front of the persisted world transform', () => {
  const current = [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
  const rotation180Z = [-1, 0, 0, 0, -1, 0, 0, 0, 1]

  assert.deepEqual(composeWorldFromThreePreview(current, rotation180Z, [0, 0, 0]), [
    -1, 0, 0, -5,
    0, -1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ])
})
