import assert from 'node:assert/strict'
import test from 'node:test'
import { configFromProject, persistedReconstructionConfig } from '../src/features/reconstruction/reconstructionTypes.ts'

test('Metashape executable path round-trips through project configuration', () => {
  const path = 'E:/工具/Metashape Pro/metashape.exe'
  const config = configFromProject('metashape', { metashapePath: path })

  assert.equal(config.metashapePath, path)
  assert.equal(persistedReconstructionConfig(config).metashapePath, path)
})

test('missing executable path remains empty until backend probing resolves it', () => {
  const config = configFromProject('metashape', {})
  assert.equal(config.metashapePath, '')
})

test('pasted quoted executable path is normalized before persistence', () => {
  const config = configFromProject('metashape', { metashapePath: '  "E:/工具/Metashape Pro/metashape.exe"  ' })
  assert.equal(config.metashapePath, 'E:/工具/Metashape Pro/metashape.exe')
  assert.equal(persistedReconstructionConfig(config).metashapePath, 'E:/工具/Metashape Pro/metashape.exe')
})
