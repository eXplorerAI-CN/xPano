import assert from 'node:assert/strict'
import test from 'node:test'
import type { PointCloudVariant } from '../src/lib/contracts.ts'
import { canActivateVariant, canDeleteVariant, reconcilePreviewVariantId, shouldMaterializeStandardVariant } from '../src/features/results/pointVariants.ts'

function variant(overrides: Partial<PointCloudVariant> = {}): PointCloudVariant {
  return {
    id: 'dense-1',
    label: '致密化 #1',
    kind: 'densified',
    canonicalPath: 'work/geometry/variants/dense-1/points3D.bin',
    pointCount: 200,
    createdAt: '2026-07-11T00:00:00Z',
    sourceJobId: null,
    protected: false,
    checksumSha256: 'a'.repeat(64),
    transformRevision: 2,
    status: 'ready',
    ...overrides,
  }
}

test('preview selection stays independent from the active training variant', () => {
  const variants = [variant({ id: 'standard', kind: 'standard', protected: true }), variant()]

  assert.equal(reconcilePreviewVariantId(variants, 'standard', 'dense-1'), 'dense-1')
  assert.equal(reconcilePreviewVariantId(variants, 'standard', 'deleted'), 'standard')
})

test('only ready non-active variants can become the training input', () => {
  assert.equal(canActivateVariant(variant(), 'standard'), true)
  assert.equal(canActivateVariant(variant({ status: 'corrupt' }), 'standard'), false)
  assert.equal(canActivateVariant(variant(), 'dense-1'), false)
})

test('standard, protected, and active variants cannot be deleted', () => {
  assert.equal(canDeleteVariant(variant(), 'standard'), true)
  assert.equal(canDeleteVariant(variant({ id: 'standard', kind: 'standard' }), 'dense-1'), false)
  assert.equal(canDeleteVariant(variant({ protected: true }), 'standard'), false)
  assert.equal(canDeleteVariant(variant(), 'dense-1'), false)
})

test('legacy completed projects materialize a missing standard variant on results entry', () => {
  const missingStandard = variant({ id: 'standard', kind: 'standard', protected: true, status: 'missing' })

  assert.equal(shouldMaterializeStandardVariant([missingStandard], 'complete', 'standard'), true)
  assert.equal(shouldMaterializeStandardVariant([{ ...missingStandard, status: 'ready' }], 'complete', 'standard'), false)
  assert.equal(shouldMaterializeStandardVariant([missingStandard], 'running', 'standard'), false)
  assert.equal(shouldMaterializeStandardVariant([missingStandard], 'complete', 'dense-1'), false)
})
