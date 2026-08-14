import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveAssetSource } from '../src/lib/assetSource.ts'

test('browser previews do not call the Tauri asset converter', () => {
  let calls = 0
  const source = resolveAssetSource('D:/project/work/thumb.jpg', false, (path) => {
    calls += 1
    return `asset://${path}`
  })

  assert.equal(source, '')
  assert.equal(calls, 0)
})

test('Tauri previews convert local files but preserve direct URLs', () => {
  const convert = (path: string) => `asset://${path}`

  assert.equal(resolveAssetSource('D:/thumb.jpg', true, convert), 'asset://D:/thumb.jpg')
  assert.equal(resolveAssetSource('data:image/png;base64,AA==', true, convert), 'data:image/png;base64,AA==')
})
