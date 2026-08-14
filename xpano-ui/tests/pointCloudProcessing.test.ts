import assert from 'node:assert/strict'
import test from 'node:test'
import type { Matrix4 } from '../src/lib/contracts.ts'
import { decodePointCloudPacket, processPointCloudPacket } from '../src/lib/pointCloudProcessing.ts'

function makePacket() {
  const pointCount = 2
  const cameraCount = 1
  const buffer = new ArrayBuffer(64 + pointCount * 12 + pointCount * 3 + cameraCount * 48)
  const view = new DataView(buffer)
  new Uint8Array(buffer, 0, 8).set(new TextEncoder().encode('XPCLD001'))
  view.setUint32(8, 1, true)
  view.setUint32(12, 0, true)
  view.setUint32(16, pointCount, true)
  view.setUint32(20, cameraCount, true)
  view.setBigUint64(24, BigInt(pointCount), true)
  view.setUint32(32, pointCount * 3, true)
  view.setUint32(36, pointCount * 3, true)
  view.setUint32(40, 48, true)
  view.setBigUint64(48, BigInt(buffer.byteLength - 64), true)
  new Float32Array(buffer, 64, 6).set([1, 2, 3, -4, 5, -6])
  new Uint8Array(buffer, 64 + 24, 6).set([10, 20, 30, 200, 210, 220])
  const cameraOffset = 64 + 24 + 6
  view.setUint32(cameraOffset, 7, true)
  ;[8, 9, 10, 1, 0, 0, 0, 1.1, 1.5, 0.2, 50].forEach((value, index) => {
    view.setFloat32(cameraOffset + 4 + index * 4, value, true)
  })
  return buffer
}

function makeEmptyPacket() {
  const buffer = new ArrayBuffer(64)
  const view = new DataView(buffer)
  new Uint8Array(buffer, 0, 8).set(new TextEncoder().encode('XPCLD001'))
  view.setUint32(8, 1, true)
  view.setUint32(40, 48, true)
  return buffer
}

test('decodes the compact point cloud packet without JSON-shaped arrays', () => {
  const decoded = decodePointCloudPacket(makePacket())

  assert.equal(decoded.numPoints, 2)
  assert.equal(decoded.totalPoints, 2)
  assert.deepEqual([...decoded.points], [1, 2, 3, -4, 5, -6])
  assert.deepEqual([...decoded.colors], [10, 20, 30, 200, 210, 220])
  assert.equal(decoded.cameras[0].id, 7)
  assert.deepEqual(decoded.cameras[0].position, [8, 9, 10])
})

test('processes every point off-thread without changing point count or coordinate precision', () => {
  const transform: Matrix4 = [
    1, 0, 0, 10,
    0, 1, 0, 20,
    0, 0, 1, 30,
    0, 0, 0, 1,
  ]

  const processed = processPointCloudPacket(makePacket(), 'dark', transform)

  assert.equal(processed.numPoints, 2)
  assert.equal(processed.points.length, 6)
  assert.deepEqual([...processed.points], [11, -22, -33, 6, -25, -24])
  assert.deepEqual(processed.viewBounds.all.min, [6, -25, -33])
  assert.deepEqual(processed.viewBounds.all.max, [11, -22, -24])
  assert.equal(processed.colors.length, 6)
})

test('returns an empty result for a valid point cloud packet with no points', () => {
  const processed = processPointCloudPacket(makeEmptyPacket(), 'dark', null)

  assert.equal(processed.numPoints, 0)
  assert.equal(processed.points.length, 0)
  assert.equal(processed.colors.length, 0)
  assert.equal(processed.viewBounds, undefined)
})
