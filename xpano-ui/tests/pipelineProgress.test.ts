import assert from 'node:assert/strict'
import test from 'node:test'
import type { PipelineProgress } from '../src/lib/types.ts'
import { sanitizeProgress } from '../src/lib/pipelineProgress.ts'

const exportProgress: PipelineProgress = {
  phase: 'export',
  stage: 'export.images',
  trackId: 'training-images',
  percent: 97,
  message: '正在导出 176/472 相机',
  elapsed: 120,
  phasePercents: { extract: 100, align: 100, export: 37 },
  current: 176,
  total: 472,
  etaSeconds: 201,
}

test('same-phase scalar progress keeps the active stage and counted progress', () => {
  const next = sanitizeProgress({
    phase: 'export',
    percent: 98,
    message: '正在导出 COLMAP 数据',
    elapsed: 121,
    phasePercents: { extract: 100, align: 100, export: 38 },
  }, exportProgress)

  assert.equal(next.stage, 'export.images')
  assert.equal(next.trackId, 'training-images')
  assert.equal(next.current, 176)
  assert.equal(next.total, 472)
  assert.equal(next.etaSeconds, 201)
  assert.equal(next.percent, 98)
})

test('an explicit stage transition clears counters from the previous node', () => {
  const next = sanitizeProgress({
    phase: 'export',
    stage: 'export.colmap',
    percent: 99,
    message: '正在写出 COLMAP 模型',
    elapsed: 130,
    phasePercents: { extract: 100, align: 100, export: 80 },
  }, exportProgress)

  assert.equal(next.stage, 'export.colmap')
  assert.equal(next.trackId, undefined)
  assert.equal(next.current, undefined)
  assert.equal(next.total, undefined)
  assert.equal(next.etaSeconds, undefined)
})
