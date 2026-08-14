import assert from 'node:assert/strict'
import test from 'node:test'
import { commandErrorMessage } from '../src/lib/commandError.ts'

test('extracts structured Tauri command errors instead of rendering object Object', () => {
  assert.equal(commandErrorMessage({ code: 'revision_conflict', message: 'project revision changed from 50 to 51' }), 'project revision changed from 50 to 51')
  assert.equal(commandErrorMessage({ error: 'GPU unavailable' }), 'GPU unavailable')
  assert.equal(commandErrorMessage(new Error('runtime missing')), 'runtime missing')
})

test('falls back safely for primitive and unknown errors', () => {
  assert.equal(commandErrorMessage('cancelled'), 'cancelled')
  assert.equal(commandErrorMessage(null), '未知错误')
})
