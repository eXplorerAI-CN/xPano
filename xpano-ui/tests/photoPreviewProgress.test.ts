import assert from 'node:assert/strict'
import test from 'node:test'
import { initialPhotoPreviewCount, nextPhotoPreviewCount } from '../src/features/media/photoPreviewProgress.ts'

test('photo preview starts with a bounded viewport batch', () => {
  assert.equal(initialPhotoPreviewCount(8), 8)
  assert.equal(initialPhotoPreviewCount(5000), 24)
})

test('photo preview only grows and eventually exposes every path', () => {
  assert.equal(nextPhotoPreviewCount(24, 100, 24), 48)
  assert.equal(nextPhotoPreviewCount(96, 100, 24), 100)
  assert.equal(nextPhotoPreviewCount(100, 100, 24), 100)
})
