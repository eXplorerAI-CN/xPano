import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyTrainingPreset,
  DEFAULT_TRAINING_CONFIG,
  deriveTrainingPreset,
  trainingCanStart,
  trainingDisplayPercent,
  trainingStartBlocker,
  trainingWorkspaceMode,
} from '../src/features/training/trainingConfig.ts'

test('uses LichtFeld v0.5.3 defaults and keeps the GUI enabled', () => {
  assert.equal(DEFAULT_TRAINING_CONFIG.iterations, 30000)
  assert.equal(DEFAULT_TRAINING_CONFIG.strategy, 'mrnf')
  assert.equal(DEFAULT_TRAINING_CONFIG.shDegree, 3)
  assert.equal(DEFAULT_TRAINING_CONFIG.maxGaussians, 1_000_000)
  assert.equal(DEFAULT_TRAINING_CONFIG.resizeFactor, 'auto')
  assert.equal(DEFAULT_TRAINING_CONFIG.maxWidth, 3840)
  assert.equal(DEFAULT_TRAINING_CONFIG.bilateralGrid, true)
  assert.equal(DEFAULT_TRAINING_CONFIG.gui, true)
})

test('applies a single-stage preset without hiding advanced user choices', () => {
  const configured = { ...DEFAULT_TRAINING_CONFIG, backgroundColor: '#123456', enableMip: true }
  const fast = applyTrainingPreset(configured, 'fast')

  assert.equal(fast.iterations, 10000)
  assert.equal(fast.maxGaussians, 500000)
  assert.equal(fast.resizeFactor, '2')
  assert.equal(fast.backgroundColor, '#123456')
  assert.equal(fast.enableMip, true)
})

test('requires both the bundled runtime and a reconstructed COLMAP dataset', () => {
  assert.equal(trainingCanStart({ runtimeAvailable: true, datasetAvailable: true, geometryAvailable: true }, false), true)
  assert.equal(trainingCanStart({ runtimeAvailable: false, datasetAvailable: true, geometryAvailable: true }, false), false)
  assert.equal(trainingCanStart({ runtimeAvailable: true, datasetAvailable: false, geometryAvailable: true }, false), false)
  assert.equal(trainingCanStart({ runtimeAvailable: true, datasetAvailable: true, geometryAvailable: false }, false), false)
  assert.equal(trainingCanStart({ runtimeAvailable: true, datasetAvailable: true, geometryAvailable: true }, true), false)
})

test('blocks training when the dedicated output location is not writable and preserves the runtime failure category', () => {
  const ready = { runtimeAvailable: true, datasetAvailable: true, geometryAvailable: true, outputAvailable: true }

  assert.equal(trainingCanStart({ ...ready, outputAvailable: false }, false), false)
  assert.deepEqual(
    trainingStartBlocker(true, { ...ready, runtimeAvailable: false, runtimeMessage: 'NVIDIA display driver is unavailable' }, false),
    { reason: 'NVIDIA display driver is unavailable', action: 'recheck' },
  )
  assert.deepEqual(
    trainingStartBlocker(true, { ...ready, outputAvailable: false, outputMessage: 'Training output directory is not writable' }, false),
    { reason: 'Training output directory is not writable', action: 'recheck' },
  )
})

test('does not display another workspace completion as training progress', () => {
  assert.equal(trainingDisplayPercent('idle', 0, 30000, false, 100), 0)
  assert.equal(trainingDisplayPercent('running', 120, 30000, true, 25), 25)
  assert.equal(trainingDisplayPercent('failed', 15000, 30000, false, 100), 50)
  assert.equal(trainingDisplayPercent('complete', 30000, 30000, false, 0), 100)
})

test('derives the visible preset from the actual submitted values', () => {
  assert.equal(deriveTrainingPreset(DEFAULT_TRAINING_CONFIG), 'balanced')
  assert.equal(deriveTrainingPreset(applyTrainingPreset(DEFAULT_TRAINING_CONFIG, 'fast')), 'fast')
  assert.equal(deriveTrainingPreset({ ...DEFAULT_TRAINING_CONFIG, iterations: 31_000 }), 'custom')
  assert.equal(deriveTrainingPreset({ ...DEFAULT_TRAINING_CONFIG, enableMip: true }), 'balanced')
})

test('maps persisted and live training state to one workspace mode', () => {
  assert.equal(trainingWorkspaceMode('idle', false), 'setup')
  assert.equal(trainingWorkspaceMode('ready', false), 'setup')
  assert.equal(trainingWorkspaceMode('stale', false), 'setup')
  assert.equal(trainingWorkspaceMode('running', false), 'running')
  assert.equal(trainingWorkspaceMode('idle', true), 'running')
  assert.equal(trainingWorkspaceMode('complete', false), 'complete')
  assert.equal(trainingWorkspaceMode('failed', false), 'failed')
  assert.equal(trainingWorkspaceMode('interrupted', false), 'interrupted')
})

test('explains why training cannot start and where the user should recover', () => {
  const ready = { runtimeAvailable: true, datasetAvailable: true, geometryAvailable: true }
  assert.deepEqual(trainingStartBlocker(false, ready, false), { reason: '请先打开并准备一个工程', action: 'media' })
  assert.deepEqual(trainingStartBlocker(true, ready, true), { reason: '当前有任务正在运行', action: null })
  assert.deepEqual(trainingStartBlocker(true, { ...ready, runtimeAvailable: false }, false), { reason: 'LichtFeld 运行环境不可用', action: 'recheck' })
  assert.deepEqual(trainingStartBlocker(true, { ...ready, datasetAvailable: false }, false), { reason: '训练数据尚未导出', action: 'reconstruction' })
  assert.deepEqual(trainingStartBlocker(true, { ...ready, geometryAvailable: false }, false), { reason: '尚未选择有效训练点云', action: 'results' })
  assert.equal(trainingStartBlocker(true, ready, false), null)
})
