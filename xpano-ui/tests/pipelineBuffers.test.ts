import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { appendBoundedLog, mergeMediaItemBatch } from '../src/lib/pipelineBuffers.ts'
import { DEFAULT_PROJECT_PATH } from '../src/app/routes.ts'

function item(id: string) {
  return {
    id,
    timestamp: null,
    selected: true,
  }
}

test('the application defaults to the manual media workspace', () => {
  assert.equal(DEFAULT_PROJECT_PATH, '/project/media')
})

test('release routing redirects legacy batch URLs to the manual workspace', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')

  assert.match(app, /<Route path="\/batch\/\*" element={<Navigate to={DEFAULT_PROJECT_PATH} replace \/>} \/>/)
  assert.doesNotMatch(app, /element={<BatchShell/)
  assert.doesNotMatch(app, /element={<BatchTaskEditor/)
})

test('mergeMediaItemBatch deduplicates updates and keeps a bounded live window', () => {
  const current = Array.from({ length: 4 }, (_, index) => item(`photo_${index + 1}`))
  const incoming = [item('photo_4'), item('photo_5'), item('photo_6')]

  const merged = mergeMediaItemBatch(current, incoming, 4)

  assert.deepEqual(merged.map((entry) => entry.id), ['photo_3', 'photo_4', 'photo_5', 'photo_6'])
})

test('appendBoundedLog deduplicates consecutive messages and caps retained lines', () => {
  let logs: string[] = []
  logs = appendBoundedLog(logs, '00:00 · extract · scanning', 3)
  logs = appendBoundedLog(logs, '00:01 · extract · scanning', 3)
  logs = appendBoundedLog(logs, '00:02 · extract · decoding', 3)
  logs = appendBoundedLog(logs, '00:03 · extract · thumbnail', 3)
  logs = appendBoundedLog(logs, '00:04 · extract · ready', 3)

  assert.deepEqual(logs, [
    '00:02 · extract · decoding',
    '00:03 · extract · thumbnail',
    '00:04 · extract · ready',
  ])
})
