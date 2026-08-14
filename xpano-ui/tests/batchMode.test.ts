import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  batchEditorProjectRoot,
  batchOverallPercent,
  batchQueueElapsedSeconds,
  batchTaskInputLocked,
  enabledStageCount,
  latestBatchJobId,
  moveBatchTaskIds,
  setBatchStage,
  validateStagePrefix,
  type BatchTask,
} from '../src/features/batch/batchTypes.ts'

test('batch stages only allow a contiguous prefix', () => {
  assert.equal(validateStagePrefix({ media: true, reconstruction: true, training: true }), null)
  assert.equal(validateStagePrefix({ media: true, reconstruction: false, training: true }), '开启训练前必须先开启对齐')
  assert.equal(
    validateStagePrefix({
      media: false,
      reconstruction: true,
      training: false,
    }),
    '开启对齐前必须先开启素材准备',
  )
  assert.equal(
    validateStagePrefix({
      media: false,
      reconstruction: false,
      training: false,
    }),
    '请至少开启素材准备阶段',
  )
  assert.equal(enabledStageCount({ media: true, reconstruction: false, training: false }), 1)
})

test('turning off an earlier batch stage also turns off every dependent stage', () => {
  assert.deepEqual(setBatchStage({ media: true, reconstruction: true, training: true }, 'media', false), {
    media: false,
    reconstruction: false,
    training: false,
  })
  assert.deepEqual(setBatchStage({ media: true, reconstruction: true, training: true }, 'reconstruction', false), {
    media: true,
    reconstruction: false,
    training: false,
  })
})

test('terminal tasks count as consumed queue work even when a stage failed early', () => {
  const task = (state: BatchTask['state'], percent: number) => ({ state, progress: { percent } }) as BatchTask
  assert.equal(batchOverallPercent([task('completed', 100), task('failed', 22), task('queued', 0)]), 200 / 3)
  assert.equal(batchOverallPercent([]), 0)
})

test('a new batch editor never inherits the globally open manual project', () => {
  assert.equal(batchEditorProjectRoot(undefined, null), '')
  assert.equal(batchEditorProjectRoot('D:/projects/existing', null), 'D:/projects/existing')
  assert.equal(batchEditorProjectRoot(undefined, 'D:/projects/requested'), 'D:/projects/requested')
})

test('keyboard task reordering moves exactly one task and respects boundaries', () => {
  const ids = ['one', 'two', 'three']
  assert.deepEqual(moveBatchTaskIds(ids, 'two', -1), ['two', 'one', 'three'])
  assert.deepEqual(moveBatchTaskIds(ids, 'two', 1), ['one', 'three', 'two'])
  assert.deepEqual(moveBatchTaskIds(ids, 'one', -1), ids)
  assert.deepEqual(moveBatchTaskIds(ids, 'missing', 1), ids)
})

test('batch elapsed time sums finished work and the live task without counting waiting tasks', () => {
  const task = (state: BatchTask['state'], elapsedSeconds: number) => ({ state, progress: { elapsedSeconds } }) as BatchTask
  assert.equal(batchQueueElapsedSeconds([task('completed', 90), task('running', 15), task('queued', 800)]), 105)
})

test('the batch provider receives pushed queue snapshots without a polling timer', () => {
  const source = readFileSync(new URL('../src/app/BatchProvider.tsx', import.meta.url), 'utf8')
  assert.match(source, /listen<BatchQueueFile>\(["']batch:queue["']/)
  assert.doesNotMatch(source, /setInterval\(/)
})

test('saving a batch task uses one backend command that also enqueues it', () => {
  const editor = readFileSync(new URL('../src/features/batch/BatchTaskEditor.tsx', import.meta.url), 'utf8')
  assert.match(editor, /saveAndEnqueueTask\(/)
  assert.doesNotMatch(editor, /saveTask\(/)
  assert.doesNotMatch(editor, /enqueueTask\(/)
})

test('a completed task can be copied into a new editable task', () => {
  const shell = readFileSync(new URL('../src/features/batch/BatchShell.tsx', import.meta.url), 'utf8')
  const editor = readFileSync(new URL('../src/features/batch/BatchTaskEditor.tsx', import.meta.url), 'utf8')
  assert.match(shell, /edit\?duplicate=1/)
  assert.match(editor, /duplicateSource/)
  assert.match(editor, /duplicateBatchTask/)
  assert.match(editor, /emptyBatchTask\(\)/)
})

test('the expanded task row opens the latest enabled stage log', () => {
  const task = {
    stageJobIds: { media: 'job-media', reconstruction: 'job-reconstruction' },
  } as unknown as BatchTask
  assert.equal(latestBatchJobId(task), 'job-reconstruction')
  assert.equal(latestBatchJobId({ stageJobIds: {} } as BatchTask), null)
})

test('queued and running task details lock project inputs while completed details remain inspectable', () => {
  const task = (taskId: string, state: BatchTask['state']) => ({ taskId, state }) as BatchTask
  const tasks = [task('queued', 'queued'), task('running', 'running'), task('done', 'completed')]
  assert.equal(batchTaskInputLocked(tasks, 'queued'), true)
  assert.equal(batchTaskInputLocked(tasks, 'running'), true)
  assert.equal(batchTaskInputLocked(tasks, 'done'), false)
  assert.equal(batchTaskInputLocked(tasks, null), false)
})

test('the batch editor owns a compact, overflow-safe form layout', () => {
  const editor = readFileSync(new URL('../src/features/batch/BatchTaskEditor.tsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
  assert.match(editor, /className="app-workspace batch-editor-workspace/)
  assert.match(editor, /className="theme-input batch-form-control/)
  assert.match(editor, /batch-stage-toggle/)
  assert.match(editor, /lg:grid-cols-\[minmax\(0,1\.25fr\)_minmax\(360px,0\.9fr\)\]/)
  assert.match(styles, /\.batch-task-editor-panel \.batch-form-control/)
  assert.match(styles, /height:\s*36px/)
  assert.match(styles, /font-size:\s*12px/)
  assert.doesNotMatch(styles, /\.liquid-panel\.batch-task-editor-panel\s*\{[^}]*overflow-y:\s*auto/s)
})
