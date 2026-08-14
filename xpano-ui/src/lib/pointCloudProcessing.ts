import type { Matrix4 } from './contracts'
import type { CameraPose, PointCloudData, PointCloudViewBounds, ResolvedTheme } from './types'

const HEADER_BYTES = 64
const CAMERA_BYTES = 48
const MAGIC = 'XPCLD001'

interface DecodedPointCloudPacket {
  points: Float32Array
  colors: Uint8Array
  numPoints: number
  totalPoints: number
  sampled: boolean
  cameras: CameraPose[]
}

type Bounds = PointCloudViewBounds['point']

function readMagic(buffer: ArrayBuffer) {
  return new TextDecoder().decode(new Uint8Array(buffer, 0, 8))
}

function checkedNumber(value: bigint, label: string) {
  const number = Number(value)
  if (!Number.isSafeInteger(number)) throw new Error(`${label} exceeds the JavaScript safe integer range`)
  return number
}

export function decodePointCloudPacket(buffer: ArrayBuffer): DecodedPointCloudPacket {
  if (buffer.byteLength < HEADER_BYTES) throw new Error('Point cloud packet is truncated')
  if (readMagic(buffer) !== MAGIC) throw new Error('Point cloud packet magic is invalid')
  const view = new DataView(buffer)
  const version = view.getUint32(8, true)
  if (version !== 1) throw new Error(`Unsupported point cloud packet version: ${version}`)

  const flags = view.getUint32(12, true)
  const numPoints = view.getUint32(16, true)
  const cameraCount = view.getUint32(20, true)
  const totalPoints = checkedNumber(view.getBigUint64(24, true), 'totalPoints')
  const pointFloatCount = view.getUint32(32, true)
  const colorByteCount = view.getUint32(36, true)
  const cameraRecordBytes = view.getUint32(40, true)
  const payloadBytes = checkedNumber(view.getBigUint64(48, true), 'payloadBytes')
  if (pointFloatCount !== numPoints * 3 || colorByteCount !== numPoints * 3) {
    throw new Error('Point cloud packet counts are inconsistent')
  }
  if (cameraRecordBytes !== CAMERA_BYTES) throw new Error('Point cloud camera record size is invalid')
  const expectedPayload = pointFloatCount * 4 + colorByteCount + cameraCount * cameraRecordBytes
  if (payloadBytes !== expectedPayload || buffer.byteLength !== HEADER_BYTES + expectedPayload) {
    throw new Error('Point cloud packet payload size is invalid')
  }

  const points = new Float32Array(buffer, HEADER_BYTES, pointFloatCount)
  const colorOffset = HEADER_BYTES + pointFloatCount * 4
  const colors = new Uint8Array(buffer, colorOffset, colorByteCount)
  const cameras: CameraPose[] = []
  let cameraOffset = colorOffset + colorByteCount
  for (let index = 0; index < cameraCount; index++) {
    const id = view.getUint32(cameraOffset, true)
    const values = Array.from({ length: 11 }, (_, valueIndex) => view.getFloat32(cameraOffset + 4 + valueIndex * 4, true))
    cameras.push({
      id,
      position: [values[0], values[1], values[2]],
      rotation: [values[3], values[4], values[5], values[6]],
      frustum: { fov: values[7], aspect: values[8], near: values[9], far: values[10] },
    })
    cameraOffset += cameraRecordBytes
  }

  return { points, colors, numPoints, totalPoints, sampled: Boolean(flags & 1), cameras }
}

function emptyBounds(): Bounds {
  return {
    min: [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
    max: [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
  }
}

function cloneBounds(bounds: Bounds): Bounds {
  return { min: [...bounds.min], max: [...bounds.max] }
}

function expandPoint(bounds: Bounds, x: number, y: number, z: number) {
  bounds.min[0] = Math.min(bounds.min[0], x)
  bounds.min[1] = Math.min(bounds.min[1], y)
  bounds.min[2] = Math.min(bounds.min[2], z)
  bounds.max[0] = Math.max(bounds.max[0], x)
  bounds.max[1] = Math.max(bounds.max[1], y)
  bounds.max[2] = Math.max(bounds.max[2], z)
}

function size(bounds: Bounds): [number, number, number] {
  return [bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1], bounds.max[2] - bounds.min[2]]
}

function maxDimension(bounds: Bounds) {
  const dimensions = size(bounds)
  return Math.max(dimensions[0], dimensions[1], dimensions[2], 0)
}

function usable(bounds: Bounds) {
  const maximum = maxDimension(bounds)
  return Number.isFinite(maximum) && maximum > 1e-6
}

function expandScalar(bounds: Bounds, amount: number) {
  bounds.min[0] -= amount
  bounds.min[1] -= amount
  bounds.min[2] -= amount
  bounds.max[0] += amount
  bounds.max[1] += amount
  bounds.max[2] += amount
  return bounds
}

function union(target: Bounds, source: Bounds) {
  expandPoint(target, source.min[0], source.min[1], source.min[2])
  expandPoint(target, source.max[0], source.max[1], source.max[2])
  return target
}

function contains(bounds: Bounds, x: number, y: number, z: number) {
  return x >= bounds.min[0] && x <= bounds.max[0]
    && y >= bounds.min[1] && y <= bounds.max[1]
    && z >= bounds.min[2] && z <= bounds.max[2]
}

function countInBounds(positions: Float32Array, bounds: Bounds, count: number) {
  let inside = 0
  for (let index = 0; index < count; index++) {
    const source = index * 3
    if (contains(bounds, positions[source], positions[source + 1], positions[source + 2])) inside++
  }
  return inside
}

function percentileBounds(xs: Float32Array, ys: Float32Array, zs: Float32Array, count: number): Bounds {
  xs.sort()
  ys.sort()
  zs.sort()
  const lower = Math.max(0, Math.floor(count * 0.015))
  const upper = Math.min(count - 1, Math.ceil(count * 0.985))
  return { min: [xs[lower], ys[lower], zs[lower]], max: [xs[upper], ys[upper], zs[upper]] }
}

function axisIndex(value: number, min: number, axisSize: number, bins: number) {
  if (axisSize <= 1e-9) return 0
  return Math.min(bins - 1, Math.max(0, Math.floor(((value - min) / axisSize) * bins)))
}

function denseBounds(positions: Float32Array, baseBounds: Bounds, count: number): Bounds {
  const baseSize = size(baseBounds)
  const baseMax = Math.max(baseSize[0], baseSize[1], baseSize[2], 1)
  if (!Number.isFinite(baseMax) || baseMax <= 0) return cloneBounds(baseBounds)

  const bins = 24
  const binCounts = new Uint32Array(bins * bins * bins)
  let bestIndex = 0
  let bestCount = 0
  let validCount = 0
  for (let index = 0; index < count; index++) {
    const source = index * 3
    const x = positions[source]
    const y = positions[source + 1]
    const z = positions[source + 2]
    if (!contains(baseBounds, x, y, z)) continue
    const ix = axisIndex(x, baseBounds.min[0], baseSize[0], bins)
    const iy = axisIndex(y, baseBounds.min[1], baseSize[1], bins)
    const iz = axisIndex(z, baseBounds.min[2], baseSize[2], bins)
    const bucket = ix + iy * bins + iz * bins * bins
    const nextCount = ++binCounts[bucket]
    validCount++
    if (nextCount > bestCount) {
      bestCount = nextCount
      bestIndex = bucket
    }
  }
  if (validCount < 32 || bestCount < 4) return cloneBounds(baseBounds)

  const bestZ = Math.floor(bestIndex / (bins * bins))
  const bestY = Math.floor((bestIndex - bestZ * bins * bins) / bins)
  const bestX = bestIndex % bins
  const center: [number, number, number] = [
    baseBounds.min[0] + ((bestX + 0.5) / bins) * baseSize[0],
    baseBounds.min[1] + ((bestY + 0.5) / bins) * baseSize[1],
    baseBounds.min[2] + ((bestZ + 0.5) / bins) * baseSize[2],
  ]
  const safeSize: [number, number, number] = [
    Math.max(baseSize[0], baseMax * 0.02),
    Math.max(baseSize[1], baseMax * 0.02),
    Math.max(baseSize[2], baseMax * 0.02),
  ]
  const distances = new Float32Array(validCount)
  let distanceCount = 0
  for (let index = 0; index < count; index++) {
    const source = index * 3
    const x = positions[source]
    const y = positions[source + 1]
    const z = positions[source + 2]
    if (!contains(baseBounds, x, y, z)) continue
    const dx = (x - center[0]) / safeSize[0]
    const dy = (y - center[1]) / safeSize[1]
    const dz = (z - center[2]) / safeSize[2]
    distances[distanceCount++] = dx * dx + dy * dy + dz * dz
  }
  const sortedDistances = distances.slice(0, distanceCount)
  sortedDistances.sort()
  const thresholdIndex = Math.min(distanceCount - 1, Math.max(16, Math.floor(distanceCount * 0.28)))
  const distanceThreshold = sortedDistances[thresholdIndex]
  const result = emptyBounds()
  let denseCount = 0
  for (let index = 0; index < count; index++) {
    const source = index * 3
    const x = positions[source]
    const y = positions[source + 1]
    const z = positions[source + 2]
    if (!contains(baseBounds, x, y, z)) continue
    const dx = (x - center[0]) / safeSize[0]
    const dy = (y - center[1]) / safeSize[1]
    const dz = (z - center[2]) / safeSize[2]
    if (dx * dx + dy * dy + dz * dz > distanceThreshold) continue
    expandPoint(result, x, y, z)
    denseCount++
  }
  const denseMax = maxDimension(result)
  if (denseCount < Math.max(64, validCount * 0.04) || !Number.isFinite(denseMax) || denseMax < baseMax * 0.01) {
    return cloneBounds(baseBounds)
  }
  return expandScalar(result, Math.max(denseMax * 0.18, baseMax * 0.015))
}

function pointFocusBounds(fullBounds: Bounds, positions: Float32Array, xs: Float32Array, ys: Float32Array, zs: Float32Array, count: number) {
  const fullMax = Math.max(maxDimension(fullBounds), 1)
  const percentile = percentileBounds(xs, ys, zs, count)
  if (!usable(percentile)) return cloneBounds(fullBounds)
  const percentileMax = Math.max(maxDimension(percentile), 1)
  const expanded = expandScalar(cloneBounds(percentile), Math.max(percentileMax * 0.12, 0.05))
  const coverage = countInBounds(positions, expanded, count)
  const minCoverage = Math.max(500, Math.floor(count * 0.55))
  if (coverage >= minCoverage) return expanded
  const dense = denseBounds(positions, expanded, count)
  if (usable(dense) && countInBounds(positions, dense, count) >= minCoverage) return dense
  if (coverage >= Math.max(200, Math.floor(count * 0.32)) && percentileMax / fullMax < 0.75) return expanded
  return cloneBounds(fullBounds)
}

function cameraBounds(cameras: CameraPose[]) {
  if (cameras.length < 3) return null
  const bounds = emptyBounds()
  cameras.forEach((camera) => expandPoint(bounds, camera.position[0], -camera.position[1], -camera.position[2]))
  return usable(bounds) ? bounds : null
}

function expandedCameraBounds(camera: Bounds, focus: Bounds) {
  const cameraMax = Math.max(maxDimension(camera), 1)
  const focusMax = Math.max(maxDimension(focus), 1)
  return expandScalar(cloneBounds(camera), Math.max(cameraMax * 0.08, focusMax * 0.08, 0.1))
}

function clamp01(value: number) {
  return Math.min(1, Math.max(0, value))
}

function displayColor(rByte: number, gByte: number, bByte: number, theme: ResolvedTheme): [number, number, number] {
  const r = rByte / 255
  const g = gByte / 255
  const b = bByte / 255
  const average = (r + g + b) / 3
  const enhanced: [number, number, number] = [
    clamp01(((average + (r - average) * 1.28) - 0.5) * 1.04 + 0.515),
    clamp01(((average + (g - average) * 1.24) - 0.5) * 1.04 + 0.515),
    clamp01(((average + (b - average) * 1.3) - 0.5) * 1.04 + 0.515),
  ]
  if (theme === 'dark') return enhanced
  const adaptedAverage = (enhanced[0] + enhanced[1] + enhanced[2]) / 3
  return [
    clamp01(Math.pow(clamp01(adaptedAverage + (enhanced[0] - adaptedAverage) * 1.22), 1.22) * 0.58 + 0.018),
    clamp01(Math.pow(clamp01(adaptedAverage + (enhanced[1] - adaptedAverage) * 1.2), 1.22) * 0.6 + 0.02),
    clamp01(Math.pow(clamp01(adaptedAverage + (enhanced[2] - adaptedAverage) * 1.28), 1.12) * 0.72 + 0.026),
  ]
}

export function processPointCloudPacket(buffer: ArrayBuffer, theme: ResolvedTheme, transform: Matrix4 | null): PointCloudData {
  const decoded = decodePointCloudPacket(buffer)
  if (decoded.numPoints === 0) {
    return {
      points: new Float32Array(),
      colors: new Float32Array(),
      numPoints: 0,
      totalPoints: decoded.totalPoints,
      sampled: decoded.sampled,
      cameras: decoded.cameras,
      preparedForThree: true,
    }
  }
  const positions = new Float32Array(decoded.numPoints * 3)
  const colors = new Float32Array(decoded.numPoints * 3)
  const xs = new Float32Array(decoded.numPoints)
  const ys = new Float32Array(decoded.numPoints)
  const zs = new Float32Array(decoded.numPoints)
  const pointBounds = emptyBounds()

  for (let index = 0; index < decoded.numPoints; index++) {
    const source = index * 3
    const rawX = decoded.points[source]
    const rawY = decoded.points[source + 1]
    const rawZ = decoded.points[source + 2]
    const transformedX = transform ? transform[0] * rawX + transform[1] * rawY + transform[2] * rawZ + transform[3] : rawX
    const transformedY = transform ? transform[4] * rawX + transform[5] * rawY + transform[6] * rawZ + transform[7] : rawY
    const transformedZ = transform ? transform[8] * rawX + transform[9] * rawY + transform[10] * rawZ + transform[11] : rawZ
    const x = transformedX
    const y = -transformedY
    const z = -transformedZ
    positions[source] = x
    positions[source + 1] = y
    positions[source + 2] = z
    xs[index] = x
    ys[index] = y
    zs[index] = z
    expandPoint(pointBounds, x, y, z)
    const display = displayColor(decoded.colors[source], decoded.colors[source + 1], decoded.colors[source + 2], theme)
    colors[source] = display[0]
    colors[source + 1] = display[1]
    colors[source + 2] = display[2]
  }

  const subject = pointFocusBounds(pointBounds, positions, xs, ys, zs, decoded.numPoints)
  const all = cloneBounds(pointBounds)
  const camera = cameraBounds(decoded.cameras)
  if (camera) {
    union(subject, expandedCameraBounds(camera, subject))
    union(all, expandedCameraBounds(camera, all))
  }

  return {
    points: positions,
    colors,
    numPoints: decoded.numPoints,
    totalPoints: decoded.totalPoints,
    sampled: decoded.sampled,
    cameras: decoded.cameras,
    preparedForThree: true,
    viewBounds: { point: pointBounds, subject, all },
  }
}
