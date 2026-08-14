import assert from 'node:assert/strict'
import test from 'node:test'
import type { JobEvent, JobSnapshot } from '../src/lib/contracts.ts'
import { recoverJobView } from '../src/lib/jobRecovery.ts'

const snapshot: JobSnapshot = {
  jobId: 'job-reconstruction-1',
  workspace: 'reconstruction',
  state: 'cancelling',
  stageId: 'metashape.pano.match',
  sequence: 4,
  startedAt: '2026-07-11T00:00:00.000Z',
  updatedAt: '2026-07-11T00:00:12.000Z',
}

const events: JobEvent[] = [
  {
    schemaVersion: 1,
    sequence: 1,
    timestamp: '2026-07-11T00:00:00.000Z',
    projectId: 'project-1',
    jobId: snapshot.jobId,
    workspace: 'reconstruction',
    kind: 'job.started',
    stageId: null,
    trackId: null,
    state: 'running',
    current: null,
    total: null,
    unit: null,
    percent: null,
    etaSeconds: null,
    message: '任务已启动',
    payload: {},
  },
  {
    schemaVersion: 1,
    sequence: 2,
    timestamp: '2026-07-11T00:00:01.000Z',
    projectId: 'project-1',
    jobId: snapshot.jobId,
    workspace: 'reconstruction',
    kind: 'stage.started',
    stageId: snapshot.stageId,
    trackId: null,
    state: 'running',
    current: null,
    total: null,
    unit: null,
    percent: null,
    etaSeconds: null,
    message: '开始匹配全景特征',
    payload: {},
  },
  {
    schemaVersion: 1,
    sequence: 3,
    timestamp: '2026-07-11T00:00:10.000Z',
    projectId: 'project-1',
    jobId: snapshot.jobId,
    workspace: 'reconstruction',
    kind: 'stage.progress',
    stageId: snapshot.stageId,
    trackId: null,
    state: 'running',
    current: 25,
    total: 100,
    unit: null,
    percent: 25,
    etaSeconds: 30,
    message: '正在匹配全景特征',
    payload: {},
  },
  {
    schemaVersion: 1,
    sequence: 4,
    timestamp: '2026-07-11T00:00:12.000Z',
    projectId: 'project-1',
    jobId: snapshot.jobId,
    workspace: 'reconstruction',
    kind: 'log.line',
    stageId: snapshot.stageId,
    trackId: null,
    state: 'cancelling',
    current: null,
    total: null,
    unit: null,
    percent: null,
    etaSeconds: null,
    message: '正在取消任务',
    payload: {},
  },
]

test('restores an in-flight reconstruction without treating cancelling as stopped', () => {
  const recovered = recoverJobView([snapshot], events, Date.parse('2026-07-11T00:00:15.000Z'))

  assert.equal(recovered.activeJobId, snapshot.jobId)
  assert.equal(recovered.running, true)
  assert.equal(recovered.progress.phase, 'align')
  assert.equal(recovered.progress.stage, snapshot.stageId)
  assert.equal(recovered.progress.percent, 25)
  assert.equal(recovered.progress.current, 25)
  assert.equal(recovered.progress.total, 100)
  assert.equal(recovered.progress.message, '正在取消任务')
  assert.deepEqual(recovered.logs, ['任务已启动', '开始匹配全景特征', '正在匹配全景特征', '正在取消任务'])
})

test('completed durable job restores as complete and inactive', () => {
  const completed = { ...snapshot, state: 'completed' as const, sequence: 5, updatedAt: '2026-07-11T00:00:20.000Z' }
  const completedEvent: JobEvent = {
    ...events[3],
    sequence: 5,
    timestamp: completed.updatedAt,
    kind: 'job.completed',
    state: 'completed',
    message: '任务已完成',
    percent: 100,
  }

  const recovered = recoverJobView([completed], [...events, completedEvent], Date.parse(completed.updatedAt))

  assert.equal(recovered.activeJobId, null)
  assert.equal(recovered.running, false)
  assert.equal(recovered.progress.phase, 'complete')
  assert.equal(recovered.progress.percent, 100)
})
