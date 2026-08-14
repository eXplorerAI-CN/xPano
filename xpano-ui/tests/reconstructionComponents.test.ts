import assert from 'node:assert/strict'
import test from 'node:test'
import { prepareComponentSelection } from '../src/features/reconstruction/reconstructionComponents.ts'

const inspection = {
  schemaVersion: 2,
  inventoryComplete: true,
  totalCameras: 880,
  alignedCameras: 856,
  unalignedCameras: 24,
  defaultComponentKey: 'main',
  components: [
    { componentKey: 'secondary', label: 'Secondary', alignedCameraCount: 221, totalCameraCount: 221, tiePointCount: 94262, isInitiallyActive: true },
    { componentKey: 'main', label: 'Main', alignedCameraCount: 458, totalCameraCount: 458, tiePointCount: 132270, isInitiallyActive: false },
  ],
  warnings: ['Multiple Components'],
}

test('live Component inspection preselects the backend default without replacing the exported key', () => {
  const decision = prepareComponentSelection(inspection, 'secondary')

  assert.equal(decision.mode, 'confirm')
  assert.equal(decision.selectedComponentKey, 'main')
  assert.equal(decision.currentExportedComponentKey, 'secondary')
})

test('a single usable Component can proceed without a selection dialog', () => {
  const decision = prepareComponentSelection({
    ...inspection,
    defaultComponentKey: 'only',
    components: [{ componentKey: 'only', label: 'Only', alignedCameraCount: 12, totalCameraCount: 12, tiePointCount: 800, isInitiallyActive: true }],
  }, 'old')

  assert.equal(decision.mode, 'direct')
  assert.equal(decision.selectedComponentKey, 'only')
  assert.equal(decision.currentExportedComponentKey, 'old')
})

test('an unusable or inconsistent live inventory cannot start re-export', () => {
  assert.throws(
    () => prepareComponentSelection({ ...inspection, defaultComponentKey: 'missing' }, 'main'),
    /可导出的 Component/,
  )
})
