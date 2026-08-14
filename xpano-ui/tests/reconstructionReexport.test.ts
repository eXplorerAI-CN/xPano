import assert from 'node:assert/strict'
import test from 'node:test'
import { psxReexportAvailability } from '../src/features/reconstruction/reconstructionReexport.ts'

test('completed Metashape project with a PSX can be re-exported', () => {
  assert.deepEqual(psxReexportAvailability({
    backend: 'metashape',
    projectCurrent: true,
    projectPath: 'work/xpano.psx',
    manifestPath: 'work/manifests/alignment.json',
    dirty: false,
    running: false,
    backendAvailable: true,
  }), { allowed: true, reason: '' })
})

test('re-export is blocked when the PSX or current reconstruction state is missing', () => {
  assert.equal(psxReexportAvailability({
    backend: 'metashape',
    projectCurrent: true,
    projectPath: null,
    manifestPath: 'work/manifests/alignment.json',
    dirty: false,
    running: false,
    backendAvailable: true,
  }).allowed, false)
  assert.equal(psxReexportAvailability({
    backend: 'colmap',
    projectCurrent: true,
    projectPath: 'work/xpano.psx',
    manifestPath: 'work/manifests/alignment.json',
    dirty: false,
    running: false,
    backendAvailable: true,
  }).allowed, false)
  assert.equal(psxReexportAvailability({
    backend: 'metashape',
    projectCurrent: true,
    projectPath: 'work/xpano.psx',
    manifestPath: 'work/manifests/alignment.json',
    dirty: true,
    running: false,
    backendAvailable: true,
  }).allowed, false)
  assert.equal(psxReexportAvailability({
    backend: 'metashape',
    projectCurrent: false,
    projectPath: 'work/xpano.psx',
    manifestPath: 'work/manifests/alignment.json',
    dirty: false,
    running: false,
    backendAvailable: true,
  }).allowed, false)
})
