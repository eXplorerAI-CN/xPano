import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Axis3d, CheckCircle2, Layers, RefreshCw, RotateCcw, Undo2, X, XCircle } from 'lucide-react'
import * as THREE from 'three'
import gsap from 'gsap'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'
import { invoke } from '@tauri-apps/api/core'
import type { CameraPose, PointCloudBounds, PointCloudData, ResolvedTheme } from '../../lib/types'
import type { Matrix4 } from '../../lib/contracts'
import { invalidatePointCloudSessionEntries, loadPointCloudForSession } from '../../lib/pointCloudSessionCache'
import { composeWorldFromThreePreview } from '../../features/results/geometryPreview'
import { DensificationPanel, type ResultNotice } from '../../features/results/DensificationPanel'

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))
const isTauriRuntime = () => typeof window !== 'undefined' && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
type PoseDisplayMode = 'frustum' | 'hidden'
type ViewFitMode = 'subject' | 'all'
type AxisNotice = ResultNotice
const EMPTY_CAMERAS: CameraPose[] = []

const viewerThemes: Record<ResolvedTheme, {
  sceneBackground: string
  fog: string
  gridMain: string
  gridSecondary: string
  gridOpacity: number
  exposure: number
  contextOpacity: number
  primaryOpacity: number
  pointSizeBoost: number
  alphaTest: number
  overlayBackground: string
  mountBackground: string
}> = {
  dark: {
    sceneBackground: '#0f1316',
    fog: '#0f1316',
    gridMain: '#536772',
    gridSecondary: '#222a30',
    gridOpacity: 0.34,
    exposure: 1.16,
    contextOpacity: 0.18,
    primaryOpacity: 0.84,
    pointSizeBoost: 0,
    alphaTest: 0.05,
    overlayBackground:
      'linear-gradient(180deg, rgba(255,255,255,0.045) 0%, rgba(255,255,255,0) 24%), radial-gradient(ellipse at center, rgba(255,255,255,0) 46%, rgba(0,0,0,0.28) 100%)',
    mountBackground: '#0f1316',
  },
  light: {
    sceneBackground: '#dfeaf2',
    fog: '#dfeaf2',
    gridMain: '#7f98aa',
    gridSecondary: '#c4d3de',
    gridOpacity: 0.36,
    exposure: 0.94,
    contextOpacity: 0.42,
    primaryOpacity: 1,
    pointSizeBoost: 0.34,
    alphaTest: 0.025,
    overlayBackground:
      'linear-gradient(180deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0) 24%), radial-gradient(ellipse at center, rgba(255,255,255,0) 54%, rgba(12,56,104,0.075) 100%)',
    mountBackground: '#dfeaf2',
  },
}

function makePointTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 48
  canvas.height = 48

  const ctx = canvas.getContext('2d')!
  const glow = ctx.createRadialGradient(24, 24, 0, 24, 24, 24)
  glow.addColorStop(0, 'rgba(255,255,255,0.98)')
  glow.addColorStop(0.46, 'rgba(255,255,255,0.9)')
  glow.addColorStop(0.72, 'rgba(255,255,255,0.28)')
  glow.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = glow
  ctx.fillRect(0, 0, 48, 48)

  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  texture.generateMipmaps = false
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

function makeAxisLabelSprite(label: string, color: string, resolvedTheme: ResolvedTheme) {
  const canvas = document.createElement('canvas')
  canvas.width = 160
  canvas.height = 160

  const ctx = canvas.getContext('2d')!
  const isDark = resolvedTheme === 'dark'
  ctx.clearRect(0, 0, 160, 160)
  ctx.fillStyle = isDark ? 'rgba(8, 12, 15, 0.82)' : 'rgba(255, 255, 255, 0.72)'
  ctx.beginPath()
  ctx.roundRect(38, 38, 84, 84, 24)
  ctx.fill()
  ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.18)' : 'rgba(12, 56, 104, 0.16)'
  ctx.lineWidth = 4
  ctx.stroke()
  ctx.shadowColor = isDark ? 'rgba(0, 0, 0, 0.22)' : 'rgba(12, 56, 104, 0.12)'
  ctx.shadowBlur = isDark ? 8 : 10
  ctx.shadowOffsetY = 2
  ctx.fillStyle = color
  ctx.font = '800 64px Inter, system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, 80, 83)

  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  texture.generateMipmaps = false
  texture.colorSpace = THREE.SRGBColorSpace

  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  })
  const sprite = new THREE.Sprite(material)
  sprite.scale.set(0.56, 0.56, 0.56)
  return sprite
}

function makeAxis(direction: THREE.Vector3, color: string, label: string, resolvedTheme: ResolvedTheme) {
  const group = new THREE.Group()
  const axisMaterial = new THREE.MeshBasicMaterial({ color })
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.72, 16), axisMaterial)
  const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.062, 0.16, 24), axisMaterial)
  const up = new THREE.Vector3(0, 1, 0)
  const q = new THREE.Quaternion().setFromUnitVectors(up, direction.clone().normalize())

  shaft.quaternion.copy(q)
  shaft.position.copy(direction).multiplyScalar(0.36)
  arrow.quaternion.copy(q)
  arrow.position.copy(direction).multiplyScalar(0.8)

  const labelSprite = makeAxisLabelSprite(label, color, resolvedTheme)
  labelSprite.position.copy(direction).multiplyScalar(1.12)

  group.add(shaft, arrow, labelSprite)
  return group
}

function makeAxisWidget(resolvedTheme: ResolvedTheme) {
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 20)
  const group = new THREE.Group()

  group.add(makeAxis(new THREE.Vector3(1, 0, 0), '#ff8a6a', 'X', resolvedTheme))
  group.add(makeAxis(new THREE.Vector3(0, 1, 0), '#79d6a3', 'Y', resolvedTheme))
  group.add(makeAxis(new THREE.Vector3(0, 0, 1), '#7fb5ff', 'Z', resolvedTheme))

  const origin = new THREE.Mesh(
    new THREE.SphereGeometry(0.07, 24, 16),
    new THREE.MeshBasicMaterial({ color: '#dbe7ef' }),
  )
  group.add(origin)
  scene.add(group)

  return { scene, camera, group }
}

function enhanceColors(source: Float32Array, count: number) {
  const display = new Float32Array(count * 3)

  for (let i = 0; i < count * 3; i += 3) {
    const r = source[i] ?? 0.78
    const g = source[i + 1] ?? 0.78
    const b = source[i + 2] ?? 0.78
    const average = (r + g + b) / 3

    const saturatedR = average + (r - average) * 1.28
    const saturatedG = average + (g - average) * 1.24
    const saturatedB = average + (b - average) * 1.3

    display[i] = clamp01((saturatedR - 0.5) * 1.04 + 0.515)
    display[i + 1] = clamp01((saturatedG - 0.5) * 1.04 + 0.515)
    display[i + 2] = clamp01((saturatedB - 0.5) * 1.04 + 0.515)
  }

  return display
}

function adaptColorsForTheme(source: Float32Array, resolvedTheme: ResolvedTheme) {
  if (resolvedTheme === 'dark') return source

  const display = new Float32Array(source.length)
  for (let i = 0; i < source.length; i += 3) {
    const r = source[i] ?? 0
    const g = source[i + 1] ?? 0
    const b = source[i + 2] ?? 0
    const average = (r + g + b) / 3

    const contrastR = average + (r - average) * 1.22
    const contrastG = average + (g - average) * 1.2
    const contrastB = average + (b - average) * 1.28

    display[i] = clamp01(Math.pow(clamp01(contrastR), 1.22) * 0.58 + 0.018)
    display[i + 1] = clamp01(Math.pow(clamp01(contrastG), 1.22) * 0.6 + 0.02)
    display[i + 2] = clamp01(Math.pow(clamp01(contrastB), 1.12) * 0.72 + 0.026)
  }

  return display
}

function boxMaxDimension(box: THREE.Box3) {
  const size = box.getSize(new THREE.Vector3())
  return Math.max(size.x, size.y, size.z, 0)
}

function isUsableBox(box: THREE.Box3) {
  const maxDim = boxMaxDimension(box)
  return Number.isFinite(maxDim) && maxDim > 1e-6
}

function countPointsInBox(positions: Float32Array, box: THREE.Box3, count: number) {
  let inside = 0
  const point = new THREE.Vector3()
  for (let i = 0; i < count; i++) {
    const source = i * 3
    point.set(positions[source], positions[source + 1], positions[source + 2])
    if (box.containsPoint(point)) inside++
  }
  return inside
}

function makePercentileFocusBox(xs: Float32Array, ys: Float32Array, zs: Float32Array, count: number) {
  xs.sort()
  ys.sort()
  zs.sort()

  const lower = Math.max(0, Math.floor(count * 0.015))
  const upper = Math.min(count - 1, Math.ceil(count * 0.985))
  return new THREE.Box3(
    new THREE.Vector3(xs[lower], ys[lower], zs[lower]),
    new THREE.Vector3(xs[upper], ys[upper], zs[upper]),
  )
}

function getAxisIndex(value: number, min: number, size: number, bins: number) {
  if (size <= 1e-9) return 0
  return THREE.MathUtils.clamp(Math.floor(((value - min) / size) * bins), 0, bins - 1)
}

function makeDenseFocusBox(positions: Float32Array, baseBox: THREE.Box3, count: number) {
  const baseSize = baseBox.getSize(new THREE.Vector3())
  const baseMax = Math.max(baseSize.x, baseSize.y, baseSize.z, 1)
  if (!Number.isFinite(baseMax) || baseMax <= 0) return baseBox.clone()

  const bins = 24
  const binCounts = new Uint32Array(bins * bins * bins)
  let bestIndex = 0
  let bestCount = 0
  let validCount = 0
  const point = new THREE.Vector3()

  for (let i = 0; i < count; i++) {
    const source = i * 3
    point.set(positions[source], positions[source + 1], positions[source + 2])
    if (!baseBox.containsPoint(point)) continue

    const ix = getAxisIndex(point.x, baseBox.min.x, baseSize.x, bins)
    const iy = getAxisIndex(point.y, baseBox.min.y, baseSize.y, bins)
    const iz = getAxisIndex(point.z, baseBox.min.z, baseSize.z, bins)
    const index = ix + iy * bins + iz * bins * bins
    const nextCount = ++binCounts[index]
    validCount++

    if (nextCount > bestCount) {
      bestCount = nextCount
      bestIndex = index
    }
  }

  if (validCount < 32 || bestCount < 4) return baseBox.clone()

  const bestIz = Math.floor(bestIndex / (bins * bins))
  const bestIy = Math.floor((bestIndex - bestIz * bins * bins) / bins)
  const bestIx = bestIndex % bins
  const denseCenter = new THREE.Vector3(
    baseBox.min.x + ((bestIx + 0.5) / bins) * baseSize.x,
    baseBox.min.y + ((bestIy + 0.5) / bins) * baseSize.y,
    baseBox.min.z + ((bestIz + 0.5) / bins) * baseSize.z,
  )
  const safeSize = new THREE.Vector3(
    Math.max(baseSize.x, baseMax * 0.02),
    Math.max(baseSize.y, baseMax * 0.02),
    Math.max(baseSize.z, baseMax * 0.02),
  )
  const distances = new Float32Array(validCount)
  let distanceCount = 0

  for (let i = 0; i < count; i++) {
    const source = i * 3
    point.set(positions[source], positions[source + 1], positions[source + 2])
    if (!baseBox.containsPoint(point)) continue

    const dx = (point.x - denseCenter.x) / safeSize.x
    const dy = (point.y - denseCenter.y) / safeSize.y
    const dz = (point.z - denseCenter.z) / safeSize.z
    distances[distanceCount++] = dx * dx + dy * dy + dz * dz
  }

  const sortedDistances = distances.slice(0, distanceCount)
  sortedDistances.sort()
  const thresholdIndex = THREE.MathUtils.clamp(Math.floor(distanceCount * 0.28), 16, distanceCount - 1)
  const distanceThreshold = sortedDistances[thresholdIndex]
  const denseBox = new THREE.Box3()
  let denseCount = 0

  for (let i = 0; i < count; i++) {
    const source = i * 3
    point.set(positions[source], positions[source + 1], positions[source + 2])
    if (!baseBox.containsPoint(point)) continue

    const dx = (point.x - denseCenter.x) / safeSize.x
    const dy = (point.y - denseCenter.y) / safeSize.y
    const dz = (point.z - denseCenter.z) / safeSize.z
    if (dx * dx + dy * dy + dz * dz > distanceThreshold) continue

    denseBox.expandByPoint(point)
    denseCount++
  }

  const denseSize = denseBox.getSize(new THREE.Vector3())
  const denseMax = Math.max(denseSize.x, denseSize.y, denseSize.z)
  if (denseCount < Math.max(64, validCount * 0.04) || !Number.isFinite(denseMax) || denseMax < baseMax * 0.01) {
    return baseBox.clone()
  }

  denseBox.expandByScalar(Math.max(denseMax * 0.18, baseMax * 0.015))
  return denseBox
}

function makePointFocusBox(
  fullBox: THREE.Box3,
  positions: Float32Array,
  xs: Float32Array,
  ys: Float32Array,
  zs: Float32Array,
  count: number,
) {
  const fullMax = Math.max(boxMaxDimension(fullBox), 1)
  const percentileBox = makePercentileFocusBox(xs, ys, zs, count)
  if (!isUsableBox(percentileBox)) return fullBox.clone()

  const percentileMax = Math.max(boxMaxDimension(percentileBox), 1)
  const expandedPercentileBox = percentileBox.clone().expandByScalar(Math.max(percentileMax * 0.12, 0.05))
  const percentileCoverage = countPointsInBox(positions, expandedPercentileBox, count)
  const minCoverage = Math.max(500, Math.floor(count * 0.55))

  // WARN: A tiny percentile box compared with the full box usually means far outliers, not an invalid subject.
  if (percentileCoverage >= minCoverage) return expandedPercentileBox

  const denseBox = makeDenseFocusBox(positions, expandedPercentileBox, count)
  if (isUsableBox(denseBox) && countPointsInBox(positions, denseBox, count) >= minCoverage) return denseBox

  const fullCoverageRatio = percentileMax / fullMax
  if (percentileCoverage >= Math.max(200, Math.floor(count * 0.32)) && fullCoverageRatio < 0.75) {
    return expandedPercentileBox
  }

  return fullBox.clone()
}

function makeCameraBox(cameras: CameraPose[]) {
  const cameraBox = new THREE.Box3()
  for (const camera of cameras) {
    cameraBox.expandByPoint(new THREE.Vector3(camera.position[0], -camera.position[1], -camera.position[2]))
  }
  return isUsableBox(cameraBox) ? cameraBox : null
}

function expandCameraBoxForFit(cameraBox: THREE.Box3, focusBox: THREE.Box3) {
  const cameraMax = Math.max(boxMaxDimension(cameraBox), 1)
  const focusMax = Math.max(boxMaxDimension(focusBox), 1)
  return cameraBox.clone().expandByScalar(Math.max(cameraMax * 0.08, focusMax * 0.08, 0.1))
}

function makeViewFitBox(
  mode: ViewFitMode,
  fullBox: THREE.Box3,
  positions: Float32Array,
  xs: Float32Array,
  ys: Float32Array,
  zs: Float32Array,
  count: number,
  cameras: CameraPose[],
) {
  const cameraBox = cameras.length >= 3 ? makeCameraBox(cameras) : null
  if (mode === 'all') {
    const allBox = fullBox.clone()
    if (cameraBox) allBox.union(expandCameraBoxForFit(cameraBox, allBox))
    return allBox
  }

  const subjectBox = makePointFocusBox(fullBox, positions, xs, ys, zs, count)
  if (cameraBox) subjectBox.union(expandCameraBoxForFit(cameraBox, subjectBox))
  return subjectBox
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((node) => {
    const item = node as THREE.Mesh | THREE.Points | THREE.LineSegments | THREE.Sprite
    item.geometry?.dispose()

    const materials = Array.isArray(item.material) ? item.material : item.material ? [item.material] : []
    for (const material of materials) {
      const withMap = material as THREE.Material & { map?: THREE.Texture }
      withMap.map?.dispose()
      material.dispose()
    }
  })
}

// COLMAP convention (+X=right, +Y=down, +Z=forward) → Three.js (+X=right, +Y=up, +Z=backward)
function seededRandom() {
  let seed = 23
  return () => {
    seed = (seed * 1664525 + 1013904223) >>> 0
    return seed / 0xffffffff
  }
}

function createDemoPointCloud(): PointCloudData {
  const count = 7400
  const points = new Float32Array(count * 3)
  const colors = new Float32Array(count * 3)
  const rand = seededRandom()

  for (let i = 0; i < count; i++) {
    const angle = rand() * Math.PI * 2
    const radius = Math.pow(rand(), 0.56) * 4.8
    const ridge = Math.sin(angle * 3.2 + radius * 1.65) * 0.42
    const x = Math.cos(angle) * radius + (rand() - 0.5) * 0.18
    const z = Math.sin(angle) * radius * 0.72 + (rand() - 0.5) * 0.18
    const y = ridge + Math.sin(radius * 2.45) * 0.2 + (rand() - 0.5) * 0.24
    points[i * 3] = x
    points[i * 3 + 1] = y
    points[i * 3 + 2] = z

    const warm = Math.max(0, Math.sin(angle + 1.1))
    const cyan = Math.max(0, Math.cos(angle - 0.5))
    colors[i * 3] = 0.2 + warm * 0.75
    colors[i * 3 + 1] = 0.32 + cyan * 0.62 + warm * 0.12
    colors[i * 3 + 2] = 0.7 + cyan * 0.25
  }

  const cameras: CameraPose[] = Array.from({ length: 18 }, (_, index) => {
    const angle = (index / 18) * Math.PI * 2
    const pos = new THREE.Vector3(Math.cos(angle) * 5.2, 1.1 + Math.sin(index * 0.8) * 0.24, Math.sin(angle) * 3.9)
    // Compute rotation so camera +Z (COLMAP forward) points toward origin
    const forward = new THREE.Vector3().sub(pos).normalize()
    const qCamToWorld = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), forward)
    const qColmap = qCamToWorld.clone().invert() // world→camera
    return {
      id: index,
      position: [pos.x, pos.y, pos.z],
      rotation: [qColmap.w, qColmap.x, qColmap.y, qColmap.z],
      frustum: { fov: Math.PI / 3, aspect: 1.55, near: 0.25, far: 10 },
    }
  })

  return { points, colors, numPoints: count, cameras }
}

function addCameraFrustums(
  container: THREE.Object3D,
  cameras: CameraPose[],
  _center: THREE.Vector3,
  radius: number,
  _floorY: number,
  resolvedTheme: ResolvedTheme,
) {
  if (!cameras.length) return

  const isLight = resolvedTheme === 'light'
  const planeColor = isLight ? '#2c8a43' : '#3fe765'
  const rayColor = isLight ? '#a45a34' : '#ff8758'
  const directionColor = isLight ? '#b3482f' : '#ff7048'
  const edgeColor = isLight ? '#167a3b' : '#63f47f'

  const overlay = new THREE.Group()
  overlay.renderOrder = 4
  const planeVertices: number[] = []
  const edgeVertices: number[] = []
  const rayVertices: number[] = []
  const directionVertices: number[] = []

  const cameraPathBox = new THREE.Box3()
  const cameraPositions = cameras.map((camera) => new THREE.Vector3(camera.position[0], -camera.position[1], -camera.position[2]))
  for (const cameraPosition of cameraPositions) {
    cameraPathBox.expandByPoint(cameraPosition)
  }
  const cameraPathSize = cameraPathBox.getSize(new THREE.Vector3())
  const cameraPathMaxDim = Math.max(cameraPathSize.x, cameraPathSize.y, cameraPathSize.z)
  const stableSceneScale = Number.isFinite(cameraPathMaxDim) && cameraPathMaxDim > 1e-6
    ? cameraPathMaxDim
    : radius
  const neighborDistances: number[] = []
  for (let i = 1; i < cameraPositions.length; i++) {
    const distance = cameraPositions[i].distanceTo(cameraPositions[i - 1])
    if (Number.isFinite(distance) && distance > 1e-6) neighborDistances.push(distance)
  }
  neighborDistances.sort((a, b) => a - b)
  const medianNeighborDistance = neighborDistances.length
    ? neighborDistances[Math.floor(neighborDistances.length * 0.5)]
    : 0
  const densityScale = stableSceneScale / Math.sqrt(Math.max(cameras.length, 1))
  // Keep camera glyphs stable and compact when densification changes the cloud bounds.
  const frustumReference = medianNeighborDistance > 1e-6
    ? Math.max(medianNeighborDistance * 1.8, densityScale * 0.35)
    : densityScale
  const frustumSize = THREE.MathUtils.clamp(frustumReference * 0.16, stableSceneScale * 0.0008, stableSceneScale * 0.0045)

  const pushVertex = (arr: number[], v: THREE.Vector3) => arr.push(v.x, v.y, v.z)
  const pushSegment = (arr: number[], a: THREE.Vector3, b: THREE.Vector3) => { pushVertex(arr, a); pushVertex(arr, b) }

  for (const camera of cameras) {
    const frustum = camera.frustum || { fov: Math.PI / 3, aspect: 1.55, near: 0.25, far: 10 }
    const d = frustumSize

    // COLMAP camera space: +X right, +Y down, +Z forward
    // Image top = -Y, image bottom = +Y
    const ch = d * Math.tan(frustum.fov / 2)
    const cw = ch * frustum.aspect
    // Corners in image order: top-left, top-right, bottom-right, bottom-left
    const localCorners = [
      new THREE.Vector3(-cw, -ch, d),  // top-left     (-X, -Y)
      new THREE.Vector3( cw, -ch, d),  // top-right    (+X, -Y)
      new THREE.Vector3( cw,  ch, d),  // bottom-right (+X, +Y)
      new THREE.Vector3(-cw,  ch, d),  // bottom-left  (-X, +Y)
    ]

    // COLMAP camera position in world
    const posW = new THREE.Vector3(camera.position[0], camera.position[1], camera.position[2])

    // COLMAP quaternion (qw,qx,qy,qz) = world→camera.
    // Invert to get camera→world for transforming local corners to world space.
    const qColmap = new THREE.Quaternion(camera.rotation[1], camera.rotation[2], camera.rotation[3], camera.rotation[0])
    const qCamToWorld = qColmap.clone().invert()

    const toWorld = (v: THREE.Vector3) => v.clone().applyQuaternion(qCamToWorld).add(posW)
    // COLMAP world → Three.js viewer: flip Y and Z
    const toViewer = (v: THREE.Vector3) => new THREE.Vector3(v.x, -v.y, -v.z)

    const cornersV = localCorners.map(toWorld).map(toViewer)
    const originV = toViewer(posW)
    const imageCenter = new THREE.Vector3(0, 0, d)
    const imgCenterV = toViewer(toWorld(imageCenter))

    // Semi-transparent frustum plane (two triangles)
    pushVertex(planeVertices, cornersV[0]); pushVertex(planeVertices, cornersV[1]); pushVertex(planeVertices, cornersV[2])
    pushVertex(planeVertices, cornersV[0]); pushVertex(planeVertices, cornersV[2]); pushVertex(planeVertices, cornersV[3])

    // Edge frame
    for (let i = 0; i < 4; i++) {
      pushSegment(edgeVertices, cornersV[i], cornersV[(i + 1) % 4])
    }

    // Rays from camera origin to each corner
    for (const c of cornersV) {
      pushSegment(rayVertices, originV, c)
    }

    // Direction indicator (from origin through image center, slightly extended)
    const dirTip = originV.clone().lerp(imgCenterV, 1.18)
    pushSegment(directionVertices, originV, dirTip)
  }

  // Semi-transparent planes
  if (planeVertices.length > 0) {
    const geom = new THREE.BufferGeometry()
    geom.setAttribute('position', new THREE.Float32BufferAttribute(planeVertices, 3))
    const mat = new THREE.MeshBasicMaterial({ color: planeColor, transparent: true, opacity: isLight ? 0.14 : 0.07, side: THREE.DoubleSide, depthTest: true, depthWrite: false })
    const mesh = new THREE.Mesh(geom, mat)
    mesh.renderOrder = 2
    overlay.add(mesh)
  }

  const addLines = (vertices: number[], color: string, opacity: number, renderOrder: number, depthTest = true) => {
    if (!vertices.length) return
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
    const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthTest, depthWrite: false })
    const lines = new THREE.LineSegments(geometry, material)
    lines.renderOrder = renderOrder
    overlay.add(lines)
  }

  addLines(rayVertices, rayColor, isLight ? 0.36 : 0.24, 3, false)       // rays from origin to corners
  addLines(directionVertices, directionColor, isLight ? 0.58 : 0.44, 4, false)  // forward direction indicator
  addLines(edgeVertices, edgeColor, isLight ? 0.74 : 0.5, 5, false)        // frustum edge frame

  container.add(overlay)
}

interface ViewerProps {
  dataPath: string | null
  resolvedTheme: ResolvedTheme
  active?: boolean
  variantPreview?: { id: string; pointsPath: string; worldFromCanonical: Matrix4 } | null
  reloadToken?: number
  projectRoot?: string | null
  worldFromCanonical?: Matrix4 | null
  transformRevision?: number | null
  onGeometryChanged?: () => void
}

function boxFromBounds(bounds: PointCloudBounds) {
  return new THREE.Box3(
    new THREE.Vector3(bounds.min[0], bounds.min[1], bounds.min[2]),
    new THREE.Vector3(bounds.max[0], bounds.max[1], bounds.max[2]),
  )
}

function processPointCloudOffThread(buffer: ArrayBuffer, theme: ResolvedTheme, transform: Matrix4 | null) {
  return new Promise<PointCloudData>((resolve, reject) => {
    const worker = new Worker(new URL('../../workers/pointCloudWorker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<{ data?: PointCloudData; error?: string }>) => {
      worker.terminate()
      if (event.data.error) reject(new Error(event.data.error))
      else if (event.data.data) resolve(event.data.data)
      else reject(new Error('点云后台处理未返回数据'))
    }
    worker.onerror = (event) => {
      worker.terminate()
      reject(new Error(event.message || '点云后台处理失败'))
    }
    worker.postMessage({ buffer, theme, transform }, [buffer])
  })
}

export function PointCloudViewer({
  dataPath,
  resolvedTheme,
  active = true,
  variantPreview = null,
  reloadToken = 0,
  projectRoot = null,
  worldFromCanonical = null,
  transformRevision = null,
  onGeometryChanged,
}: ViewerProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const initRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const resetAnimationRef = useRef<number>(0)
  const viewerActiveRef = useRef(active)
  const cloudTransitionTimerRef = useRef<number>(0)
  const cloudSwitchingRef = useRef(false)
  const viewSnapshotRef = useRef<{ sceneKey: string; position: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const poseOverlayRef = useRef<THREE.Group | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRootRef = useRef<THREE.Group | null>(null)
  const scenePivotRef = useRef(new THREE.Vector3())
  const levelingUndoRef = useRef<THREE.Quaternion[]>([])
  const levelingDragStartRef = useRef<THREE.Quaternion | null>(null)
  const frustumCtxRef = useRef<{ center: THREE.Vector3; radius: number; floorY: number }>({ center: new THREE.Vector3(), radius: 1, floorY: 0 })
  const [poseDisplayMode, setPoseDisplayMode] = useState<PoseDisplayMode>('frustum')
  const [viewFitMode, setViewFitMode] = useState<ViewFitMode>('subject')
  const [loadedData, setLoadedData] = useState<PointCloudData | null>(null)
  const [cloudSwitching, setCloudSwitching] = useState(false)
  const [levelingEnabled, setLevelingEnabled] = useState(false)
  const [levelingApplying, setLevelingApplying] = useState(false)
  const [rotationSnapDegrees, setRotationSnapDegrees] = useState(5)
  const [levelingEuler, setLevelingEuler] = useState<[number, number, number]>([0, 0, 0])
  const [axisMessage, setAxisMessage] = useState<AxisNotice | null>(null)
  const [visibleMsg, setVisibleMsg] = useState<AxisNotice | null>(null)
  const [msgLeaving, setMsgLeaving] = useState(false)
  const [loadingOverlayVisible, setLoadingOverlayVisible] = useState(false)
  const [loadingOverlayLeaving, setLoadingOverlayLeaving] = useState(false)
  const [densifyOpen, setDensifyOpen] = useState(false)
  const cloudPointsRef = useRef<THREE.Points[]>([])
  const sceneMaterialsRef = useRef<Array<THREE.Material & { opacity: number }>>([])
  const demoData = useMemo(() => createDemoPointCloud(), [])
  const browserPhase4Preview = import.meta.env.DEV && typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('preview') === 'phase4'
  const viewerTheme = viewerThemes[resolvedTheme]
  const isDark = resolvedTheme === 'dark'

  useEffect(() => {
    viewerActiveRef.current = active
  }, [active])

  const fadeOutCloud = useCallback(() => {
    return new Promise<void>((resolve) => {
      const materials = sceneMaterialsRef.current.filter((m) => m.opacity > 0)
      if (!materials.length) { resolve(); return }
      let done = 0
      materials.forEach((m) => {
        gsap.to(m, { opacity: 0, duration: 0.72, ease: 'power2.inOut', onComplete: () => {
          done++
          if (done >= materials.length) resolve()
        }})
      })
    })
  }, [])

  const setCloudTransition = useCallback((value: boolean) => {
    cloudSwitchingRef.current = value
    setCloudSwitching(value)
  }, [])

  const finishCloudTransition = useCallback(() => {
    if (!cloudSwitchingRef.current) return
    if (cloudTransitionTimerRef.current) window.clearTimeout(cloudTransitionTimerRef.current)
    cloudTransitionTimerRef.current = window.setTimeout(() => {
      cloudTransitionTimerRef.current = 0
      setCloudTransition(false)
    }, 160)
  }, [setCloudTransition])

  const pointCloudCacheKey = useCallback((pointsPath?: string | null, transformKey = '') => `${dataPath ?? 'demo'}::${pointsPath || 'base'}::${transformKey}::${resolvedTheme}::${reloadToken}`, [dataPath, reloadToken, resolvedTheme])

  const loadPointCloudData = useCallback(async (pointsPath?: string | null, useCache = true, pointTransform?: Matrix4, transformKey = '') => {
    if (!dataPath) return null
    const cacheKey = pointCloudCacheKey(pointsPath, transformKey)
    return loadPointCloudForSession(cacheKey, async () => {
      const response = await invoke<ArrayBuffer>('read_colmap_points', { dir: dataPath, pointsPath: pointsPath ?? null, maxPoints: 0 })
      if (!(response instanceof ArrayBuffer) || response.byteLength === 0) return null
      const data = await processPointCloudOffThread(response, resolvedTheme, pointTransform ?? null)
      return data.numPoints ? data : null
    }, !useCache)
  }, [dataPath, pointCloudCacheKey, resolvedTheme])

  const closeDensification = useCallback(() => setDensifyOpen(false), [])
  const clearDensificationCache = useCallback((pointsPath: string) => {
    invalidatePointCloudSessionEntries((key) => key.includes(`::${pointsPath}::`))
  }, [])
  const replacePointCloud = useCallback(async (data: PointCloudData) => {
    setCloudTransition(true)
    await fadeOutCloud()
    setLoadedData(data)
  }, [fadeOutCloud, setCloudTransition])
  const cancelCloudTransition = useCallback(() => setCloudTransition(false), [setCloudTransition])

  useEffect(() => {
    if (!dataPath) return
    if (browserPhase4Preview) {
      setLoadedData(demoData)
      finishCloudTransition()
      return
    }
    let cancelled = false
    const pointsPath = variantPreview?.pointsPath ?? null
    loadPointCloudData(pointsPath, true, variantPreview?.worldFromCanonical, variantPreview?.id)
        .then((result) => {
          if (cancelled) return
          if (result?.numPoints) setLoadedData(result)
          finishCloudTransition()
        })
        .catch(() => {
          if (!cancelled) setCloudTransition(false)
        })
    return () => { cancelled = true }
  }, [browserPhase4Preview, dataPath, demoData, variantPreview, reloadToken, loadPointCloudData, finishCloudTransition, setCloudTransition])

  useEffect(() => () => {
    if (cloudTransitionTimerRef.current) window.clearTimeout(cloudTransitionTimerRef.current)
  }, [])

  useEffect(() => {
    if (!dataPath) {
      setLoadingOverlayVisible(false)
      setLoadingOverlayLeaving(false)
      return
    }
    if (!loadedData) {
      setLoadingOverlayVisible(true)
      setLoadingOverlayLeaving(false)
      return
    }
    if (!loadingOverlayVisible) return
    setLoadingOverlayLeaving(true)
    const timer = window.setTimeout(() => {
      setLoadingOverlayVisible(false)
      setLoadingOverlayLeaving(false)
    }, 420)
    return () => window.clearTimeout(timer)
  }, [dataPath, loadedData, loadingOverlayVisible])

  useEffect(() => {
    setLoadedData(null)
    setDensifyOpen(false)
    if (!dataPath) return
    if (!isTauriRuntime()) {
      if (browserPhase4Preview) setLoadedData(demoData)
    }
  }, [browserPhase4Preview, dataPath, demoData])

  // Only show demo when no dataPath; show nothing until real data loads
  const visualData = loadedData ?? (dataPath && !browserPhase4Preview ? null : demoData)
  const points = visualData?.points ?? null
  const colors = visualData?.colors ?? null
  const numPoints = visualData?.numPoints ?? 0
  const cameras = visualData?.cameras ?? EMPTY_CAMERAS
  const preparedForThree = visualData?.preparedForThree ?? false
  const viewBounds = visualData?.viewBounds ?? null
  const sceneKey = `${dataPath ?? 'demo'}:${numPoints}:${cameras.length}`
  const viewKey = `${dataPath ?? 'demo'}:${viewFitMode}`

  const resetView = useCallback(() => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    const initial = initRef.current
    if (!camera || !controls || !initial) return

    if (resetAnimationRef.current) cancelAnimationFrame(resetAnimationRef.current)

    const startPosition = camera.position.clone()
    const startTarget = controls.target.clone()
    const startedAt = performance.now()

    const loop = (now: number) => {
      const progress = Math.min((now - startedAt) / 620, 1)
      const eased = 1 - Math.pow(1 - progress, 3)

      camera.position.lerpVectors(startPosition, initial.position, eased)
      controls.target.lerpVectors(startTarget, initial.target, eased)
      controls.update()

      if (progress < 1) resetAnimationRef.current = requestAnimationFrame(loop)
      else resetAnimationRef.current = 0
    }

    resetAnimationRef.current = requestAnimationFrame(loop)
  }, [])

  const syncLevelingEuler = useCallback(() => {
    const root = sceneRootRef.current
    if (!root) return
    const euler = new THREE.Euler().setFromQuaternion(root.quaternion, 'XYZ')
    setLevelingEuler([
      THREE.MathUtils.radToDeg(euler.x),
      THREE.MathUtils.radToDeg(euler.y),
      THREE.MathUtils.radToDeg(euler.z),
    ])
  }, [])

  const resetLevelingPreview = useCallback(() => {
    const root = sceneRootRef.current
    if (root) root.quaternion.identity()
    levelingUndoRef.current = []
    setLevelingEuler([0, 0, 0])
  }, [])

  const cancelLeveling = useCallback(() => {
    resetLevelingPreview()
    setLevelingEnabled(false)
  }, [resetLevelingPreview])

  const undoLeveling = useCallback(() => {
    const root = sceneRootRef.current
    const previous = levelingUndoRef.current.pop()
    if (!root || !previous) return
    root.quaternion.copy(previous)
    syncLevelingEuler()
  }, [syncLevelingEuler])

  const previewAxisRotation = useCallback((axis: 'x' | 'y' | 'z') => {
    const root = sceneRootRef.current
    if (!root) return
    levelingUndoRef.current.push(root.quaternion.clone())
    const direction = axis === 'x'
      ? new THREE.Vector3(1, 0, 0)
      : axis === 'y'
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(0, 0, 1)
    root.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(direction, Math.PI))
    root.quaternion.normalize()
    setLevelingEnabled(true)
    syncLevelingEuler()
  }, [syncLevelingEuler])

  const applyLeveling = useCallback(async () => {
    const root = sceneRootRef.current
    if (!root || !projectRoot || !worldFromCanonical || transformRevision === null || levelingApplying) return
    if (root.quaternion.angleTo(new THREE.Quaternion()) <= 1e-10) {
      setLevelingEnabled(false)
      return
    }
    setLevelingApplying(true)
    setAxisMessage({ text: '正在原子写入水平校正...', tone: 'pending' })
    try {
      const matrix = new THREE.Matrix4().makeRotationFromQuaternion(root.quaternion).elements
      const rotationThree = [
        matrix[0], matrix[4], matrix[8],
        matrix[1], matrix[5], matrix[9],
        matrix[2], matrix[6], matrix[10],
      ]
      const pivot = scenePivotRef.current
      const nextTransform = composeWorldFromThreePreview(
        worldFromCanonical,
        rotationThree,
        [pivot.x, pivot.y, pivot.z],
      )
      await invoke('apply_world_transform', {
        projectRoot,
        expectedTransformRevision: transformRevision,
        worldFromCanonical: nextTransform,
      })
      resetLevelingPreview()
      setLevelingEnabled(false)
      if (dataPath) invalidatePointCloudSessionEntries((key) => key.startsWith(`${dataPath}::`))
      setLoadedData(null)
      onGeometryChanged?.()
      setAxisMessage({ text: '水平校正已应用，相机外参与活动点云已同步更新', tone: 'success' })
    } catch (error) {
      setAxisMessage({ text: `水平校正失败：${String(error)}`, tone: 'error' })
    } finally {
      setLevelingApplying(false)
    }
  }, [dataPath, levelingApplying, onGeometryChanged, projectRoot, resetLevelingPreview, transformRevision, worldFromCanonical])

  useEffect(() => {
    if (axisMessage) {
      setVisibleMsg(axisMessage)
      setMsgLeaving(false)
      if (axisMessage.tone !== 'pending') {
        const timer = window.setTimeout(() => setAxisMessage(null), 3600)
        return () => window.clearTimeout(timer)
      }
    } else if (visibleMsg) {
      setMsgLeaving(true)
      const timer = window.setTimeout(() => setVisibleMsg(null), 700)
      return () => window.clearTimeout(timer)
    }
  }, [axisMessage, visibleMsg])

  // Main Three.js scene setup
  useEffect(() => {
    const el = mountRef.current
    if (!el || !points || !colors || numPoints === 0) return
    el.innerHTML = ''
    const sceneTheme = viewerThemes[resolvedTheme]

    let viewWidth = el.clientWidth || 960
    let viewHeight = el.clientHeight || 640

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(sceneTheme.sceneBackground)
    sceneRef.current = scene

    const sceneRoot = new THREE.Group()
    sceneRoot.name = 'geometry-preview-root'
    const contentRoot = new THREE.Group()
    sceneRoot.add(contentRoot)
    scene.add(sceneRoot)
    sceneRootRef.current = sceneRoot

    const poseOverlay = new THREE.Group()
    poseOverlay.name = 'camera-pose-overlay'
    poseOverlay.visible = true
    contentRoot.add(poseOverlay)
    poseOverlayRef.current = poseOverlay

    const camera = new THREE.PerspectiveCamera(50, viewWidth / viewHeight, 0.01, 5000)
    cameraRef.current = camera

    const largeCloud = numPoints > 450_000
    const hugeCloud = numPoints > 700_000
    const renderer = new THREE.WebGLRenderer({ antialias: !largeCloud, powerPreference: 'high-performance' })
    rendererRef.current = renderer
    renderer.setSize(viewWidth, viewHeight)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, hugeCloud ? 1.15 : largeCloud ? 1.35 : 1.75))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = sceneTheme.exposure
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    el.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    const viewScale = Math.min(viewWidth, viewHeight) / 720
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.rotateSpeed = 0.62 * viewScale
    controls.zoomSpeed = 0.72 * viewScale
    controls.panSpeed = 0.86 * viewScale
    controls.screenSpacePanning = true
    controlsRef.current = controls

    // Binary-backed loads arrive fully prepared by a Web Worker; demo data keeps the legacy local path.
    let positions: Float32Array
    let box: THREE.Box3
    let focusBox: THREE.Box3
    if (preparedForThree && viewBounds) {
      positions = points
      box = boxFromBounds(viewBounds.point)
      focusBox = boxFromBounds(viewFitMode === 'all' ? viewBounds.all : viewBounds.subject)
    } else {
      positions = new Float32Array(numPoints * 3)
      const xs = new Float32Array(numPoints)
      const ys = new Float32Array(numPoints)
      const zs = new Float32Array(numPoints)
      box = new THREE.Box3()
      const probe = new THREE.Vector3()
      for (let i = 0; i < numPoints; i++) {
        const x = points[i * 3]
        const y = -points[i * 3 + 1]
        const z = -points[i * 3 + 2]
        positions[i * 3] = x
        positions[i * 3 + 1] = y
        positions[i * 3 + 2] = z
        xs[i] = x
        ys[i] = y
        zs[i] = z
        probe.set(x, y, z)
        box.expandByPoint(probe)
      }
      focusBox = makeViewFitBox(viewFitMode, box, positions, xs, ys, zs, numPoints, cameras)
    }
    const center = focusBox.getCenter(new THREE.Vector3())
    const size = focusBox.getSize(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, size.z, 1)
    const sphere = focusBox.getBoundingSphere(new THREE.Sphere())
    const radius = Math.max(sphere.radius, maxDim * 0.5, 1)
    const fullSphere = box.getBoundingSphere(new THREE.Sphere())
    const fullRadius = Math.max(fullSphere.radius, radius)
    const floorY = focusBox.min.y - radius * 0.045
    frustumCtxRef.current = { center: center.clone(), radius, floorY }
    scenePivotRef.current.copy(center)
    sceneRoot.position.copy(center)
    contentRoot.position.copy(center).multiplyScalar(-1)

    scene.fog = new THREE.Fog(sceneTheme.fog, radius * 4.2, radius * 10.5)

    type FadeMaterial = THREE.Material & { opacity: number }
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const sceneEnteredAt = performance.now()
    const sceneFadeMs = reduceMotion ? 0 : 720
    const fadeMaterials: Array<{ material: FadeMaterial; targetOpacity: number }> = []
    cloudPointsRef.current = []
    sceneMaterialsRef.current = []
    const registerFadeMaterial = (material: THREE.Material | THREE.Material[]) => {
      if (sceneFadeMs === 0) return
      const materials = Array.isArray(material) ? material : [material]
      materials.forEach((mat) => {
        if (!('opacity' in mat) || typeof mat.opacity !== 'number') return
        const fadeMaterial = mat as FadeMaterial
        const targetOpacity = fadeMaterial.opacity
        fadeMaterial.transparent = true
        fadeMaterial.opacity = 0
        fadeMaterials.push({ material: fadeMaterial, targetOpacity })
        sceneMaterialsRef.current.push(fadeMaterial)
      })
    }

    // Grid
    const gridSize = Math.pow(2, Math.ceil(Math.log2(maxDim * 1.35)))
    const divisions = THREE.MathUtils.clamp(Math.round(gridSize / Math.max(maxDim / 36, 0.01)), 24, 96)
    const grid = new THREE.GridHelper(gridSize, divisions, sceneTheme.gridMain, sceneTheme.gridSecondary)
    grid.position.set(center.x, floorY, center.z)
    const gridMaterial = grid.material as THREE.LineBasicMaterial
    gridMaterial.transparent = true
    gridMaterial.opacity = sceneTheme.gridOpacity
    gridMaterial.depthWrite = false
    scene.add(grid)

    // Point cloud
    const displayColors = preparedForThree ? colors : adaptColorsForTheme(enhanceColors(colors, numPoints), resolvedTheme)
    const pointTexture = makePointTexture()
    const pixelRatio = renderer.getPixelRatio()
    const pointSize = THREE.MathUtils.clamp(
      1.36 + Math.log10(Math.max(numPoints, 10)) * 0.045 + sceneTheme.pointSizeBoost - (largeCloud ? 0.16 : 0),
      1.34,
      largeCloud ? 1.72 : 2.08,
    ) * pixelRatio

    const cloudGeometry = new THREE.BufferGeometry()
    cloudGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    cloudGeometry.setAttribute('color', new THREE.BufferAttribute(displayColors, 3))
    cloudGeometry.computeBoundingSphere()

    const cloudMaterial = new THREE.PointsMaterial({
      size: pointSize,
      vertexColors: true,
      sizeAttenuation: false,
      map: pointTexture,
      transparent: true,
      toneMapped: false,
      opacity: sceneTheme.primaryOpacity,
      alphaTest: sceneTheme.alphaTest,
      depthWrite: true,
    })
    registerFadeMaterial(cloudMaterial)
    const cloudPoints = new THREE.Points(cloudGeometry, cloudMaterial)
    contentRoot.add(cloudPoints)
    cloudPointsRef.current.push(cloudPoints)

    // Add camera frustums
    addCameraFrustums(poseOverlay, cameras, center, radius, floorY, resolvedTheme)
    poseOverlay.traverse((object) => {
      const material = (object as THREE.Object3D & { material?: THREE.Material | THREE.Material[] }).material
      if (material) registerFadeMaterial(material)
    })

    // Axis widget
    const axisWidget = makeAxisWidget(resolvedTheme)
    const fov = THREE.MathUtils.degToRad(camera.fov)
    const distance = Math.max(radius / Math.sin(fov / 2), maxDim) * 1.12
    const viewDirection = new THREE.Vector3(0.72, 0.42, 0.88).normalize()
    const initialPosition = center.clone().addScaledVector(viewDirection, distance)
    const savedView = viewSnapshotRef.current?.sceneKey === viewKey ? viewSnapshotRef.current : null

    camera.near = Math.max(radius / 1200, 0.01)
    const farReferenceRadius = viewFitMode === 'all' || fullRadius / Math.max(radius, 1) < 20 ? fullRadius : radius
    camera.far = Math.max(farReferenceRadius * 8, radius * 80, 5000)
    camera.position.copy(savedView?.position ?? initialPosition)
    camera.updateProjectionMatrix()

    controls.target.copy(savedView?.target ?? center)
    controls.minDistance = Math.max(radius * 0.12, 0.05)
    controls.maxDistance = Math.max(farReferenceRadius * 8, radius * 22, 100)
    controls.update()

    initRef.current = {
      position: initialPosition.clone(),
      target: center.clone(),
    }

    const pressedKeys = new Set<string>()
    const ignoreKeyboardMove = (target: EventTarget | null) => {
      const element = target as HTMLElement | null
      if (!element) return false
      return Boolean(element.closest('input, textarea, select, button, [contenteditable="true"]'))
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if (!['w', 'a', 's', 'd', 'q', 'e'].includes(key) || ignoreKeyboardMove(event.target)) return
      pressedKeys.add(key)
      event.preventDefault()
    }

    const onKeyUp = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if (!['w', 'a', 's', 'd', 'q', 'e'].includes(key)) return
      pressedKeys.delete(key)
      event.preventDefault()
    }

    const applyKeyboardMove = (deltaMs: number) => {
      if (pressedKeys.size === 0) return
      const forward = controls.target.clone().sub(camera.position)
      forward.y = 0
      if (forward.lengthSq() < 0.000001) return
      forward.normalize()
      const right = new THREE.Vector3().crossVectors(forward, camera.up).normalize()
      const move = new THREE.Vector3()
      if (pressedKeys.has('w')) move.add(forward)
      if (pressedKeys.has('s')) move.sub(forward)
      if (pressedKeys.has('d')) move.add(right)
      if (pressedKeys.has('a')) move.sub(right)
      if (pressedKeys.has('e')) move.y += 1
      if (pressedKeys.has('q')) move.y -= 1
      if (move.lengthSq() < 0.000001) return
      move.normalize().multiplyScalar(radius * 0.58 * Math.min(deltaMs, 48) / 1000)
      camera.position.add(move)
      controls.target.add(move)
    }

    let animationFrame = 0
    let lastRenderAt = performance.now()
    const render = () => {
      animationFrame = requestAnimationFrame(render)
      if (!viewerActiveRef.current) return
      const now = performance.now()
      const deltaMs = now - lastRenderAt
      lastRenderAt = now
      applyKeyboardMove(deltaMs)
      controls.update()
      if (fadeMaterials.length > 0) {
        const progress = Math.min((now - sceneEnteredAt) / sceneFadeMs, 1)
        const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2
        fadeMaterials.forEach(({ material, targetOpacity }) => {
          material.opacity = targetOpacity * eased
        })
        if (progress >= 1) fadeMaterials.length = 0
      }

      renderer.setScissorTest(false)
      renderer.setViewport(0, 0, viewWidth, viewHeight)
      renderer.render(scene, camera)

      const widgetSize = Math.min(186, Math.max(146, Math.round(Math.min(viewWidth, viewHeight) * 0.28)))
      const widgetX = viewWidth - widgetSize - 8
      const widgetY = 10
      const direction = camera.position.clone().sub(controls.target).normalize()

      axisWidget.camera.position.copy(direction.multiplyScalar(5.6))
      axisWidget.camera.up.copy(camera.up)
      axisWidget.camera.lookAt(0, 0, 0)

      renderer.autoClear = false
      renderer.clearDepth()
      renderer.setScissor(widgetX, widgetY, widgetSize, widgetSize)
      renderer.setViewport(widgetX, widgetY, widgetSize, widgetSize)
      renderer.setScissorTest(true)
      renderer.render(axisWidget.scene, axisWidget.camera)
      renderer.setScissorTest(false)
      renderer.autoClear = true
    }
    render()

    const onResize = () => {
      if (!viewerActiveRef.current) return
      viewWidth = el.clientWidth || 960
      viewHeight = el.clientHeight || 640

      camera.aspect = viewWidth / viewHeight
      camera.updateProjectionMatrix()
      renderer.setSize(viewWidth, viewHeight)
    }

    const resizeObserver = new ResizeObserver(onResize)
    resizeObserver.observe(el)
    window.addEventListener('resize', onResize)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)

    return () => {
      viewSnapshotRef.current = {
        sceneKey: viewKey,
        position: camera.position.clone(),
        target: controls.target.clone(),
      }
      if (resetAnimationRef.current) {
        cancelAnimationFrame(resetAnimationRef.current)
        resetAnimationRef.current = 0
      }
      cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      window.removeEventListener('resize', onResize)
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)

      gsap.killTweensOf(sceneMaterialsRef.current)
      controls.dispose()
      disposeObject(scene)
      disposeObject(axisWidget.scene)
      renderer.dispose()
      el.innerHTML = ''
      cameraRef.current = null
      controlsRef.current = null
      initRef.current = null
      poseOverlayRef.current = null
      sceneRef.current = null
      rendererRef.current = null
      sceneRootRef.current = null
      cloudPointsRef.current = []
      sceneMaterialsRef.current = []
    }
  }, [points, colors, numPoints, cameras, sceneKey, viewKey, viewFitMode, resolvedTheme, preparedForThree, viewBounds])

  useEffect(() => {
    if (!levelingEnabled) return
    const scene = sceneRef.current
    const renderer = rendererRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    const root = sceneRootRef.current
    if (!scene || !renderer || !camera || !controls || !root) return

    const transformControls = new TransformControls(camera, renderer.domElement)
    transformControls.setMode('rotate')
    transformControls.setSpace('world')
    transformControls.setRotationSnap(rotationSnapDegrees > 0 ? THREE.MathUtils.degToRad(rotationSnapDegrees) : null)
    transformControls.attach(root)
    const helper = transformControls.getHelper()
    scene.add(helper)

    const onDraggingChanged = (event: { value?: unknown }) => {
      controls.enabled = !event.value
    }
    const onMouseDown = () => {
      levelingDragStartRef.current = root.quaternion.clone()
    }
    const onMouseUp = () => {
      const start = levelingDragStartRef.current
      if (start && start.angleTo(root.quaternion) > 1e-8) {
        levelingUndoRef.current.push(start)
        if (levelingUndoRef.current.length > 20) levelingUndoRef.current.shift()
      }
      levelingDragStartRef.current = null
      syncLevelingEuler()
    }
    const onObjectChange = () => syncLevelingEuler()
    transformControls.addEventListener('dragging-changed', onDraggingChanged)
    transformControls.addEventListener('mouseDown', onMouseDown)
    transformControls.addEventListener('mouseUp', onMouseUp)
    transformControls.addEventListener('objectChange', onObjectChange)

    return () => {
      controls.enabled = true
      transformControls.removeEventListener('dragging-changed', onDraggingChanged)
      transformControls.removeEventListener('mouseDown', onMouseDown)
      transformControls.removeEventListener('mouseUp', onMouseUp)
      transformControls.removeEventListener('objectChange', onObjectChange)
      transformControls.detach()
      scene.remove(helper)
      transformControls.dispose()
    }
  }, [levelingEnabled, rotationSnapDegrees, syncLevelingEuler])

  // Toggle frustum overlay visibility without rebuilding the scene
  useEffect(() => {
    const overlay = poseOverlayRef.current
    if (!overlay) return
    overlay.visible = poseDisplayMode !== 'hidden'
  }, [poseDisplayMode])

  const pointCount = numPoints
  const cameraCount = cameras.length
  const axisNoticeTone = axisMessage?.tone ?? 'pending'
  const axisNoticeClass = axisNoticeTone === 'error'
    ? isDark
      ? 'border-danger/25 bg-danger/12 text-danger'
      : 'border-danger/22 bg-danger/10 text-danger'
    : axisNoticeTone === 'success'
      ? isDark
        ? 'border-brand/22 bg-brand/12 text-brand'
        : 'border-brand/18 bg-brand/10 text-brand'
      : isDark
        ? 'border-white/[0.08] bg-black/36 text-white/62'
        : 'border-ink/10 bg-white/72 text-ink/62 shadow-brand/5'
  const compactNotice = Boolean(visibleMsg && visibleMsg.text.length <= 42 && !visibleMsg.text.includes('\n'))

  return (
    <div className="absolute inset-0 overflow-hidden">
      <div
        ref={mountRef}
        className="absolute inset-0"
        style={{
          background: viewerTheme.mountBackground,
        }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          zIndex: 4,
          background: viewerTheme.overlayBackground,
        }}
      />
      {loadingOverlayVisible && (
        <div className={`pointer-events-none absolute inset-0 z-10 grid place-items-center px-4 transition-all duration-[420ms] ease-out ${
          loadingOverlayLeaving ? 'opacity-0 scale-[0.985]' : 'opacity-100 scale-100'
        }`}>
          <div className={`flex min-h-[118px] w-[min(320px,calc(100%-2rem))] flex-col items-center justify-center gap-3 rounded-card px-5 py-5 text-center shadow-sm backdrop-blur-xl transition-all duration-[420ms] ease-out ${
          isDark
            ? 'border border-white/[0.08] bg-black/42 text-white/72'
            : 'border border-ink/10 bg-white/78 text-ink/70 shadow-brand/5'
        }`}>
            <span className={`grid h-11 w-11 place-items-center rounded-comfortable ${
              isDark ? 'bg-white/[0.06]' : 'bg-ink/[0.05]'
            }`}>
              <RefreshCw className="h-5 w-5 animate-spin text-brand" />
            </span>
            <div className="min-w-0">
              <p className="text-[13px] font-semibold text-ink">正在加载点云预览</p>
              <p className="mt-1 text-[11px] leading-4 text-muted">读取完整点云并准备 Three.js 场景</p>
            </div>
          </div>
        </div>
      )}
      {(loadedData || !dataPath) && (
      <div className="absolute bottom-4 left-4 z-10 flex items-center gap-2">
        <ViewerStat color="#ff8a6a" label="点数" value={pointCount.toLocaleString()} />
        {cameraCount > 0 && (
          <ViewerStat color={poseDisplayMode === 'hidden' ? '#7b8791' : '#35e05a'} label="相机" value={cameraCount.toLocaleString()} />
        )}
        <div className={`flex overflow-hidden rounded-comfortable p-0.5 text-[11px] font-mono backdrop-blur ${
          isDark
            ? 'border border-white/[0.08] bg-black/40 shadow-2xl'
            : 'border border-ink/10 bg-white/64 shadow-sm'
        }`}>
          {([
            ['subject', '主体'],
            ['all', '全部'],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => setViewFitMode(mode)}
              className={`motion-press rounded-subtle px-2.5 py-1.5 transition-colors ${
                viewFitMode === mode
                  ? isDark
                    ? 'bg-white/[0.12] text-white/80'
                    : 'bg-brand/12 text-brand'
                  : isDark
                    ? 'text-white/35 hover:bg-white/[0.06] hover:text-white/60'
                    : 'text-ink/38 hover:bg-ink/[0.06] hover:text-ink/64'
              }`}
              title={mode === 'subject' ? '按主体点云和相机轨迹取景，忽略极远飞点' : '按完整包围盒取景，包含所有离群点'}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        {cameraCount > 0 && (
          <div className={`flex overflow-hidden rounded-comfortable p-0.5 text-[11px] font-mono backdrop-blur ${
            isDark
              ? 'border border-white/[0.08] bg-black/40 shadow-2xl'
              : 'border border-ink/10 bg-white/64 shadow-sm'
          }`}>
            {([
              ['frustum', '视锥'],
              ['hidden', '隐藏'],
            ] as const).map(([mode, label]) => (
              <button
                key={mode}
                onClick={() => setPoseDisplayMode(mode)}
                className={`motion-press rounded-subtle px-2.5 py-1.5 transition-colors ${
                  poseDisplayMode === mode
                    ? isDark
                      ? 'bg-white/[0.12] text-white/80'
                      : 'bg-brand/12 text-brand'
                    : isDark
                      ? 'text-white/35 hover:bg-white/[0.06] hover:text-white/60'
                      : 'text-ink/38 hover:bg-ink/[0.06] hover:text-ink/64'
                }`}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <button onClick={resetView} className={`motion-press flex h-9 w-9 items-center justify-center rounded-comfortable backdrop-blur transition-colors ${
          isDark
            ? 'border border-white/[0.08] bg-black/40 text-white/45 shadow-2xl hover:border-white/[0.16] hover:text-white/75'
            : 'border border-ink/10 bg-white/64 text-ink/44 shadow-sm hover:border-brand/24 hover:text-ink/74'
        }`}>
          <RotateCcw className="h-4 w-4" />
        </button>
        {dataPath && loadedData && (
          <button
            type="button"
            disabled={!projectRoot || worldFromCanonical === null || transformRevision === null || levelingApplying}
            onClick={() => {
              if (levelingEnabled) cancelLeveling()
              else setLevelingEnabled(true)
            }}
            className={`motion-press flex h-9 items-center gap-2 rounded-comfortable px-3 text-[11px] font-medium backdrop-blur transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
              levelingEnabled
                ? 'border border-aurora/35 bg-aurora/14 text-aurora'
                : isDark
                  ? 'border border-white/[0.08] bg-black/40 text-white/55 shadow-2xl hover:text-white/80'
                  : 'border border-ink/10 bg-white/64 text-ink/55 shadow-sm hover:text-brand'
            }`}
            title="三轴水平校正：拖动只预览，点击应用后才原子写入工程"
          >
            <Axis3d className="h-4 w-4 text-brand" />
            <span>水平校正</span>
          </button>
        )}
        {dataPath && loadedData && (
          <button
            type="button"
            onClick={() => setDensifyOpen((value) => !value)}
            className={`motion-press flex h-9 items-center gap-2 rounded-comfortable px-3 text-[11px] font-semibold backdrop-blur transition-all ${
              densifyOpen
                ? isDark ? 'border border-brand/30 bg-brand/16 text-brand shadow-2xl' : 'border border-brand/24 bg-brand/12 text-brand shadow-sm'
                : isDark ? 'border border-white/[0.08] bg-black/40 text-white/54 shadow-2xl hover:text-white/78' : 'border border-ink/10 bg-white/64 text-ink/54 shadow-sm hover:text-ink/78'
            }`}
          >
            <Layers className="h-4 w-4" />
            <span>致密化</span>
          </button>
        )}
      </div>
      )}
      {dataPath && loadedData && levelingEnabled && (
        <div className="liquid-card-clear absolute bottom-[4.25rem] right-4 z-20 w-[300px] rounded-card p-3 text-[11px] animate-in fade-in slide-in-from-bottom-2 duration-200 max-[1500px]:bottom-auto max-[1500px]:left-4 max-[1500px]:right-auto max-[1500px]:top-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium text-ink">三轴水平校正</p>
              <p className="mt-0.5 text-[10px] text-muted">拖动旋转环仅预览，网格保持世界坐标</p>
            </div>
            <button type="button" onClick={cancelLeveling} className="motion-press grid h-7 w-7 place-items-center rounded-subtle text-muted hover:bg-ink/5 hover:text-ink"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-1.5 font-mono text-[10px]">
            {(['X', 'Y', 'Z'] as const).map((axis, index) => (
              <div key={axis} className="rounded-subtle bg-ink/[0.04] px-2 py-1.5 text-center">
                <span className={axis === 'X' ? 'text-danger' : axis === 'Y' ? 'text-success' : 'text-brand'}>{axis}</span>
                <span className="ml-1 text-ink/65">{levelingEuler[index].toFixed(1)}°</span>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-[10px] text-muted">吸附</span>
            <div className="flex gap-1">
              {[0, 1, 5, 15].map((value) => (
                <button key={value} type="button" onClick={() => setRotationSnapDegrees(value)} className={`motion-press h-7 rounded-subtle px-2 text-[10px] ${rotationSnapDegrees === value ? 'bg-brand/14 text-brand' : 'bg-ink/[0.04] text-muted hover:text-ink'}`}>
                  {value === 0 ? '自由' : `${value}°`}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-3 flex items-center gap-1.5">
            {(['x', 'y', 'z'] as const).map((axis) => (
              <button key={axis} type="button" onClick={() => previewAxisRotation(axis)} className="motion-press h-7 flex-1 rounded-subtle bg-ink/[0.04] text-[10px] text-muted hover:text-ink">绕 {axis.toUpperCase()} 180°</button>
            ))}
          </div>
          <div className="mt-3 flex justify-end gap-1.5 border-t border-ink/8 pt-3">
            <button type="button" onClick={undoLeveling} disabled={!levelingUndoRef.current.length || levelingApplying} className="motion-press inline-flex h-8 items-center gap-1 rounded-subtle px-2.5 text-[10px] text-muted hover:bg-ink/5 hover:text-ink disabled:opacity-35"><Undo2 className="h-3.5 w-3.5" /> 撤销</button>
            <button type="button" onClick={resetLevelingPreview} disabled={levelingApplying} className="motion-press h-8 rounded-subtle px-2.5 text-[10px] text-muted hover:bg-ink/5 hover:text-ink">重置预览</button>
            <button type="button" onClick={() => void applyLeveling()} disabled={levelingApplying} className="motion-press inline-flex h-8 items-center gap-1 rounded-subtle bg-brand px-3 text-[10px] font-medium text-white disabled:opacity-55">
              {levelingApplying && <RefreshCw className="h-3.5 w-3.5 animate-spin" />} 应用到工程
            </button>
          </div>
        </div>
      )}
      {dataPath && loadedData && (
        <DensificationPanel
          open={densifyOpen}
          dataPath={dataPath}
          isDark={isDark}
          cloudSwitching={cloudSwitching}
          resetToken={reloadToken}
          onClose={closeDensification}
          onNotice={setAxisMessage}
          loadPointCloudData={loadPointCloudData}
          clearPointCloudCache={clearDensificationCache}
          replacePointCloud={replacePointCloud}
          finishCloudTransition={finishCloudTransition}
          cancelCloudTransition={cancelCloudTransition}
        />
      )}
      {visibleMsg && (
        <div
          className={`pointer-events-none absolute left-1/2 top-[68px] z-20 flex max-h-[30svh] w-[min(560px,calc(100%-2rem))] -translate-x-1/2 gap-2 overflow-y-auto rounded-comfortable px-3 py-2 text-[12px] shadow-sm backdrop-blur-xl transition-all duration-700 ease-out ${
            msgLeaving ? '-translate-y-2 opacity-0' : 'translate-y-0 opacity-100'
          } ${compactNotice ? 'items-center justify-center' : 'items-start justify-start'} ${axisNoticeClass}`}
        >
          {visibleMsg.tone === 'pending' && <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin opacity-80" />}
          {visibleMsg.tone === 'success' && <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />}
          {visibleMsg.tone === 'error' && <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" />}
          <span className={`min-w-0 break-words ${compactNotice ? 'text-center' : 'text-left'}`}>{visibleMsg.text}</span>
        </div>
      )}
    </div>
  )
}

function ViewerStat({ color, label, value, title }: { color: string; label: string; value: string; title?: string }) {
  return (
    <div title={title} className="glass-control flex items-center gap-2 rounded-comfortable px-3 py-2 text-[11px] font-mono text-ink/45">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color, boxShadow: `0 0 14px ${color}` }} />
      <span className="text-ink/32">{label}</span>
      <span className="text-ink/72">{value}</span>
    </div>
  )
}
