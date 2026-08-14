import assert from 'node:assert/strict'
import test from 'node:test'
import { jobIdentityMatchesProject } from '../src/lib/jobIdentity.ts'

test('project-scoped job listeners reject missing and foreign project identities', () => {
  assert.equal(jobIdentityMatchesProject(undefined, ''), true)
  assert.equal(jobIdentityMatchesProject(undefined, 'C:\\projects\\one'), false)
  assert.equal(jobIdentityMatchesProject('\\\\?\\C:\\projects\\one', 'c:/projects/one/'), true)
  assert.equal(jobIdentityMatchesProject('C:\\projects\\two', 'C:\\projects\\one'), false)
})
