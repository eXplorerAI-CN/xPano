import assert from 'node:assert/strict'
import test from 'node:test'
import { resultsWorkspaceState } from '../src/app/workspaceRetention.ts'

test('results workspace stays mounted after the user leaves it', () => {
  const firstVisit = resultsWorkspaceState(false, '/project/results')
  const afterLeaving = resultsWorkspaceState(firstVisit.mounted, '/project/media')
  const afterReturning = resultsWorkspaceState(afterLeaving.mounted, '/project/results')

  assert.deepEqual(firstVisit, { active: true, mounted: true })
  assert.deepEqual(afterLeaving, { active: false, mounted: true })
  assert.deepEqual(afterReturning, { active: true, mounted: true })
})

test('results workspace does not load before its first visit', () => {
  assert.deepEqual(resultsWorkspaceState(false, '/project/media'), { active: false, mounted: false })
})
