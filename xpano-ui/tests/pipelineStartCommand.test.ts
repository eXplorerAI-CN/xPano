import assert from 'node:assert/strict'
import test from 'node:test'
import { pipelineInputTracks, pipelineStartCommand } from '../src/lib/pipelineStartCommand.ts'

test('reconstruction jobs use the plan-validated backend command', () => {
  assert.equal(pipelineStartCommand({
    reconstruction: {
      projectRoot: 'D:/project',
      expectedRevision: 7,
      planId: 'plan-1',
    },
  }), 'start_reconstruction_job')
})

test('legacy non-project pipeline starts retain the generic command', () => {
  assert.equal(pipelineStartCommand({}), 'start_pipeline')
})

test('manifest-based pipeline starts do not repeat source tracks on the command line', () => {
  const tracks = [{ id: 'pano-1' }, { id: 'photos-1' }]

  assert.deepEqual(pipelineInputTracks(tracks, { manifestPath: 'D:/project/work/alignment.json' }), [])
})

test('pipeline starts without a manifest retain source tracks', () => {
  const tracks = [{ id: 'pano-1' }]

  assert.equal(pipelineInputTracks(tracks, {}), tracks)
})
