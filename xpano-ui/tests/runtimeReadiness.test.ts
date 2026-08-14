import assert from 'node:assert/strict'
import test from 'node:test'
import { runtimeReadinessPresentation } from '../src/lib/runtimeReadiness.ts'

test('distinguishes fully ready, repairable Metashape, and corrupt bundled runtime states', () => {
  assert.deepEqual(runtimeReadinessPresentation({
    bundled: 'ready', metashape: 'ready', densification: 'downloadable', detail: '',
  }), { tone: 'ready', label: '环境就绪' })
  assert.deepEqual(runtimeReadinessPresentation({
    bundled: 'ready', metashape: 'dependencies_missing', densification: 'downloadable', detail: '',
  }), { tone: 'warning', label: 'Metashape 待配置' })
  assert.deepEqual(runtimeReadinessPresentation({
    bundled: 'corrupt', metashape: 'missing', densification: 'downloadable', detail: '',
  }), { tone: 'error', label: '内置环境损坏' })
})
