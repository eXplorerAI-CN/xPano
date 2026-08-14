import { useEffect, useRef } from 'react'
import type { PipelinePhase } from '../../lib/types'

interface ParticleBackgroundProps {
  /** When true the field intensifies: faster drift, denser links, brighter flow. */
  active?: boolean
  phase?: PipelinePhase
}

type Layer = 'dust' | 'mid' | 'front'

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  r: number
  baseAlpha: number
  seed: number
  layer: Layer
}

interface Pulse {
  x: number
  y: number
  startedAt: number
  power: number
  seed: number
}

const PULSE_MS = 620
const LINK_DIST = 132
const MOUSE_RADIUS = 180
const MOUSE_FORCE = 0.008
const CLICK_RADIUS = 72

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function viewportSize() {
  const viewport = window.visualViewport
  return {
    width: Math.round(viewport?.width || window.innerWidth),
    height: Math.round(viewport?.height || window.innerHeight),
  }
}

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3)
}

function seededUnit(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453
  return x - Math.floor(x)
}

function ringPoint(x: number, y: number, radius: number, angle: number, seed: number, wobble: number, phase: number) {
  const r =
    radius +
    Math.sin(angle * 4 + seed + phase * 1.35) * wobble * 0.46 +
    Math.sin(angle * 8 + seed * 1.7 - phase * 1.9) * wobble * 0.36 +
    Math.sin(angle * 15 + seed * 0.73 + phase * 2.45) * wobble * 0.18
  return { x: x + Math.cos(angle) * r, y: y + Math.sin(angle) * r }
}

function readRgb(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const raw = value || fallback
  return raw.replace(/,/g, ' ').trim().split(/\s+/).slice(0, 3).join(', ')
}

/**
 * Deep-space surveyor particle field.
 *
 * Three layers of drifting motes, a proximity-linked node network, light pulses
 * flowing along bezier "scan lanes", a soft mouse force-field and click-driven
 * shock rings. When `active` is true (a pipeline is running) the whole field
 * speeds up and brightens so the background visibly "knows" the system is busy.
 *
 * Performance: renders a single full-screen canvas; pauses the rAF loop when the
 * tab is hidden, and yields gracefully to `prefers-reduced-motion` by dropping to
 * a near-static low-density state.
 */
export function ParticleBackground({ active = false }: ParticleBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // Keep the latest `active` flag available inside the long-lived effect without
  // re-subscribing listeners or recreating the particle pool.
  const activeRef = useRef(active)
  activeRef.current = active

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    let frame = 0
    let width = 0
    let height = 0
    let dpr = 1
    let particles: Particle[] = []
    let pulses: Pulse[] = []
    let mouse = { x: -9999, y: -9999, tx: -9999, ty: -9999, active: false }
    let intensity = 0 // smoothed 0..1 blend toward `active`
    let visible = true
    let pulseRgb = '80,180,210'
    let pulseWidth = 1.2
    let lastDrawAt = 0
    const readThemeTokens = () => {
      pulseRgb = readRgb('--xp-particle-pulse-rgb', '80,180,210')
      const rawWidth = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--xp-particle-pulse-width').trim())
      if (rawWidth > 0) pulseWidth = rawWidth
    }
    readThemeTokens()

    const viewportScale = () => clamp(Math.sqrt((width * height) / (1280 * 800)), 0.9, 1.38)
    const pulseRadius = () => CLICK_RADIUS * viewportScale()
    const mouseRadius = () => MOUSE_RADIUS * clamp(viewportScale(), 0.9, 1.22)

    const densityFor = (area: number) => {
      const aspect = width / Math.max(height, 1)
      const compactScale = width < 760 ? 0.78 : width < 1180 ? 0.9 : 1
      const ultraWideScale = aspect > 2.2 ? 1.18 : aspect > 1.8 ? 1.08 : 1
      if (reduced) return Math.round(Math.min(44, (area / 42000) * compactScale * ultraWideScale))
      return Math.round(Math.min(96, Math.max(22, (area / 36000) * compactScale * ultraWideScale)))
    }

    const splitByLayer = (count: number) => {
      // 50% dust, 34% mid, 16% front — depth without drowning the foreground.
      return {
        dust: Math.round(count * 0.44),
        mid: Math.round(count * 0.38),
        front: count - Math.round(count * 0.44) - Math.round(count * 0.38),
      }
    }

    const makeParticle = (layer: Layer): Particle => {
      const speedByLayer: Record<Layer, number> = { dust: 0.038, mid: 0.078, front: 0.14 }
      const radiusByLayer: Record<Layer, [number, number]> = { dust: [1.0, 1.4], mid: [1.4, 2.2], front: [1.8, 2.8] }
      const alphaByLayer: Record<Layer, [number, number]> = { dust: [0.16, 0.28], mid: [0.28, 0.44], front: [0.38, 0.56] }
      const [rMin, rMax] = radiusByLayer[layer]
      const [aMin, aMax] = alphaByLayer[layer]
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * speedByLayer[layer],
        vy: (Math.random() - 0.5) * speedByLayer[layer],
        r: rMin + Math.random() * (rMax - rMin),
        baseAlpha: aMin + Math.random() * (aMax - aMin),
        seed: Math.random() * Math.PI * 2,
        layer,
      }
    }

    const create = () => {
      const counts = splitByLayer(densityFor(width * height))
      particles = [
        ...Array.from({ length: counts.dust }, () => makeParticle('dust')),
        ...Array.from({ length: counts.mid }, () => makeParticle('mid')),
        ...Array.from({ length: counts.front }, () => makeParticle('front')),
      ]
    }

    const resize = () => {
      const next = viewportSize()
      width = next.width
      height = next.height
      const pixelArea = width * height * Math.pow(window.devicePixelRatio || 1, 2)
      const dprLimit = pixelArea > 9_000_000 ? 1.75 : pixelArea > 5_500_000 ? 2.2 : 2.75
      dpr = Math.min(window.devicePixelRatio || 1, dprLimit)
      canvas.width = Math.floor(width * dpr)
      canvas.height = Math.floor(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      readThemeTokens()
    }

    /*
    const bezierPoint = (i: number, time: number, t: number): [number, number] => {
      const y = laneY(i, time)
      // Two cubic segments stitched across the screen — cheap, smooth scan lane.
      const x0 = -40
      const x3 = width + 40
      const mid = width * 0.5
      if (t < 0.5) {
        const u = t / 0.5
        const mt = 1 - u
        return [
          mt * mt * mt * x0 + 3 * mt * mt * u * (width * 0.22) + 3 * mt * u * u * (width * 0.36) + u * u * u * mid,
          mt * mt * mt * y + 3 * mt * mt * u * (y - 74) + 3 * mt * u * u * (y + 88) + u * u * u * (y + 10),
        ]
      }
      const u = (t - 0.5) / 0.5
      const mt = 1 - u
      return [
        mt * mt * mt * mid + 3 * mt * mt * u * (width * 0.74) + 3 * mt * u * u * (width * 0.86) + u * u * u * x3,
        mt * mt * mt * (y + 10) + 3 * mt * mt * u * (y - 42) + 3 * mt * u * u * (y + 44) + u * u * u * (y - 20),
      ]
    }

    const drawLanes = (time: number, alpha: number) => {
      const lineRgb = readRgb('--xp-particle-line-rgb', '203, 213, 225')
      ctx.save()
      ctx.lineWidth = 1.4
      ctx.strokeStyle = `rgba(${lineRgb},${0.20 + alpha * 0.10})`
      for (let i = 0; i < FLOW_COUNT; i++) {
        const y = laneY(i, time)
        ctx.beginPath()
        ctx.moveTo(-40, y)
        ctx.bezierCurveTo(width * 0.22, y - 74, width * 0.36, y + 88, width * 0.5, y + 10)
        ctx.bezierCurveTo(width * 0.74, y - 42, width * 0.86, y + 44, width + 60, y - 20)
        ctx.stroke()
      }
      ctx.restore()
    }

    const drawFlowPoints = (time: number, alpha: number) => {
      const pulseRgb = readRgb('--xp-particle-pulse-rgb', '201, 100, 69')
      ctx.save()
      for (let i = 0; i < flow.length; i++) {
        const line = flow[i]
        for (const fp of line) {
          fp.t += fp.speed * (0.72 + intensity * 0.42) * (reduced ? 0.3 : 1)
          if (fp.t > 1) fp.t -= 1
          const [x, y] = bezierPoint(i, time, fp.t)
          const a = 0.16 + alpha * 0.22
          ctx.shadowBlur = 10
          ctx.shadowColor = `rgba(${pulseRgb},${a})`
          ctx.fillStyle = `rgba(${pulseRgb},${a})`
          ctx.beginPath()
          ctx.arc(x, y, 1.25 + intensity * 0.7, 0, Math.PI * 2)
          ctx.fill()
        }
      }
      ctx.restore()
    }

    */

    const drawLinks = (alpha: number, calmMode: boolean) => {
      const lineRgb = readRgb('--xp-particle-line-rgb', '203, 213, 225')
      const maxDist = LINK_DIST + intensity * (calmMode ? 4 : 12)
      ctx.save()
      ctx.lineWidth = calmMode ? 0.85 : 1.05
      // Only the mid/front layers participate in linking — dust is too plentiful.
      const linkers = particles.filter((p) => p.layer !== 'dust')
      for (let i = 0; i < linkers.length; i++) {
        if (calmMode && i % 2 === 1) continue
        for (let j = i + 1; j < linkers.length; j++) {
          const a = linkers[i]
          const b = linkers[j]
          const dx = a.x - b.x
          const dy = a.y - b.y
          const dist2 = dx * dx + dy * dy
          if (dist2 > maxDist * maxDist) continue
          const dist = Math.sqrt(dist2)
          const fade = (1 - dist / maxDist) * (0.08 + alpha * 0.045 + intensity * (calmMode ? 0.015 : 0.04))
          if (fade <= 0.01) continue
          ctx.strokeStyle = `rgba(${lineRgb},${fade})`
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(b.x, b.y)
          ctx.stroke()
        }
      }
      ctx.restore()
    }

    const drawRippleRing = (x: number, y: number, radius: number, alpha: number, seed: number, width: number, phase: number, rgb: string, calmMode: boolean) => {
      if (radius <= 4 || alpha <= 0.002) return
      const wobble = Math.min(22, Math.max(7, radius * 0.105))
      const segments = calmMode ? 56 : 96
      ctx.beginPath()
      for (let i = 0; i <= segments; i++) {
        const angle = (i / segments) * Math.PI * 2
        const point = ringPoint(x, y, radius, angle, seed, wobble, phase)
        if (i === 0) ctx.moveTo(point.x, point.y)
        else ctx.lineTo(point.x, point.y)
      }
      ctx.closePath()
      ctx.strokeStyle = `rgba(${rgb},${alpha})`
      ctx.lineWidth = width
      ctx.stroke()
      // Highlight arcs
      const arcCount = calmMode ? 2 : 4
      for (let arc = 0; arc < arcCount; arc++) {
        const start = seededUnit(seed + arc * 9.37) * Math.PI * 2
        const length = (0.18 + seededUnit(seed + arc * 3.91) * 0.18) * Math.PI
        ctx.beginPath()
        const steps = 14
        for (let i = 0; i <= steps; i++) {
          const angle = start + (i / steps) * length
          const point = ringPoint(x, y, radius, angle, seed + arc, wobble * 1.12, phase + arc * 0.4)
          if (i === 0) ctx.moveTo(point.x, point.y)
          else ctx.lineTo(point.x, point.y)
        }
        ctx.strokeStyle = `rgba(${rgb},${alpha * (0.3 + seededUnit(seed + arc) * 0.28)})`
        ctx.lineWidth = width * 0.48
        ctx.stroke()
      }
    }

    const drawPulses = (time: number, calmMode: boolean) => {
      pulses = pulses.filter((pulse) => time - pulse.startedAt < PULSE_MS)
      ctx.save()
      for (const pulse of pulses) {
        const age = Math.max(0, Math.min(1, (time - pulse.startedAt) / PULSE_MS))
        const alpha = Math.pow(1 - age, 1.75)
        const clickRadius = pulseRadius()
        const wash = ctx.createRadialGradient(pulse.x, pulse.y, 0, pulse.x, pulse.y, clickRadius * 0.55)
        wash.addColorStop(0, `rgba(${pulseRgb},${0.12 * alpha})`)
        wash.addColorStop(0.28, `rgba(${pulseRgb},${0.08 * alpha})`)
        wash.addColorStop(1, 'transparent')
        ctx.fillStyle = wash
        ctx.beginPath()
        ctx.arc(pulse.x, pulse.y, clickRadius * 0.55, 0, Math.PI * 2)
        ctx.fill()
        const radius = easeOutCubic(age) * clickRadius
        const phase = age * Math.PI * 5.6 + Math.sin(age * Math.PI) * 1.4
        drawRippleRing(pulse.x, pulse.y, radius, alpha * (calmMode ? 0.48 : 0.60), pulse.seed ?? 0, pulseWidth, phase, pulseRgb, calmMode)
      }
      ctx.restore()
    }

    const drawParticles = (time: number) => {
      const particleRgb = readRgb('--xp-particle-rgb', '225, 230, 238')
      const speedScale = 1 + intensity * 0.36
      ctx.save()
      for (const p of particles) {
        // Autonomous gentle drift so particles move even without mouse
        const driftSpeed = p.layer === 'front' ? 0.00022 : p.layer === 'mid' ? 0.00016 : 0.00010
        p.vx += Math.sin(time * driftSpeed + p.seed) * 0.0010
        p.vy += Math.cos(time * driftSpeed + p.seed + 1.7) * 0.0010

        // Click force — particles within the click radius get pushed outward
        let clickGlow = 0
        for (const pulse of pulses) {
          const age = Math.max(0, Math.min(1, (time - pulse.startedAt) / PULSE_MS))
          const dx = p.x - pulse.x
          const dy = p.y - pulse.y
          const dist = Math.hypot(dx, dy)
          const clickRadius = pulseRadius()
          if (dist <= 1 || dist > clickRadius) continue
          const raw = 1 - dist / clickRadius
          const force = easeOutCubic(raw) * 0.48 * Math.pow(1 - age, 0.9)
          clickGlow = Math.max(clickGlow, force)
          const layerMul = p.layer === 'front' ? 1 : p.layer === 'mid' ? 0.75 : 0.5
          p.vx += (dx / dist) * force * layerMul + (-dy / dist) * force * layerMul * 0.12
          p.vy += (dy / dist) * force * layerMul + (dx / dist) * force * layerMul * 0.12
        }

        // Mouse force field — gentle spiral toward smoothed cursor position
        if (mouse.active) {
          const dx = mouse.tx - p.x
          const dy = mouse.ty - p.y
          const dist2 = dx * dx + dy * dy
          const cursorRadius = mouseRadius()
          if (dist2 < cursorRadius * cursorRadius && dist2 > 0.5) {
            const dist = Math.sqrt(dist2)
            const raw = 1 - dist / cursorRadius
            const force = raw * MOUSE_FORCE
            p.vx += (dx / dist) * force * 0.5 - (dy / dist) * force * 0.25
            p.vy += (dy / dist) * force * 0.5 + (dx / dist) * force * 0.25
            p.vx *= 0.985; p.vy *= 0.985
          }
        }

        p.x += p.vx * speedScale
        p.y += p.vy * speedScale

        // Wrap around edges so the field feels endless.
        if (p.x < -20) p.x = width + 20
        if (p.x > width + 20) p.x = -20
        if (p.y < -20) p.y = height + 20
        if (p.y > height + 20) p.y = -20

        // Mild damping so the mouse impulse doesn't accumulate forever.
        p.vx = Math.max(-0.9, Math.min(0.9, p.vx * 0.995))
        p.vy = Math.max(-0.9, Math.min(0.9, p.vy * 0.995))

        const shimmer = 0.8 + Math.sin(time * 0.0009 + p.seed) * 0.2
        const scale = 0.9 + Math.sin(time * 0.0011 + p.seed) * 0.1
        const alpha = Math.min(0.78, p.baseAlpha * shimmer * (1 + intensity * 0.14))
        ctx.save()
        if (p.layer !== 'dust') {
          ctx.shadowBlur = p.layer === 'front' ? 14 : 8
          ctx.shadowColor = `rgba(${particleRgb},${alpha * 0.68})`
        }
        ctx.fillStyle = `rgba(${particleRgb},${alpha})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r * scale, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()
      }
      ctx.restore()
    }

    const draw = (time: number) => {
      frame = requestAnimationFrame(draw)
      if (!visible) return
      const calmMode = false
      if (frame % 120 === 0) readThemeTokens()
      const frameBudget = reduced ? 80 : 16
      if (time - lastDrawAt < frameBudget) return
      lastDrawAt = time

      // Ease `intensity` toward the target so transitions feel organic, not snapped.
      const target = activeRef.current ? 1 : 0
      intensity += (target - intensity) * 0.04
      // Smooth mouse lerp
      if (mouse.active) {
        mouse.tx += (mouse.x - mouse.tx) * 0.08
        mouse.ty += (mouse.y - mouse.ty) * 0.08
      }
      ctx.clearRect(0, 0, width, height)
      drawLinks(intensity, calmMode)
      drawPulses(time, calmMode)
      drawParticles(time)
    }

    const onResize = () => {
      resize()
      create()
    }

    const onMouseMove = (event: MouseEvent) => {
      if (!mouse.active) { mouse.tx = event.clientX; mouse.ty = event.clientY }
      mouse.x = event.clientX
      mouse.y = event.clientY
      mouse.active = true
    }

    const onMouseLeave = (event: MouseEvent) => {
      if (event.relatedTarget) return
      mouse.active = false
      mouse.x = -9999; mouse.y = -9999
      mouse.tx = -9999; mouse.ty = -9999
    }

    const onDown = (event: MouseEvent) => {
      const now = performance.now()
      mouse.x = event.clientX; mouse.y = event.clientY
      mouse.tx = event.clientX; mouse.ty = event.clientY
      mouse.active = true
      pulses.push({ x: event.clientX, y: event.clientY, startedAt: now, power: 1, seed: Math.random() * 1000 })
      const maxPulses = 4
      if (pulses.length > maxPulses) pulses = pulses.slice(-maxPulses)
    }

    const onVisibility = () => {
      visible = !document.hidden
    }

    resize()
    create()
    frame = requestAnimationFrame(draw)
    window.addEventListener('resize', onResize)
    window.visualViewport?.addEventListener('resize', onResize)
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseout', onMouseLeave)
    window.addEventListener('mousedown', onDown)
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', onResize)
      window.visualViewport?.removeEventListener('resize', onResize)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseout', onMouseLeave)
      window.removeEventListener('mousedown', onDown)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return <canvas ref={canvasRef} className="pointer-events-none fixed inset-0" style={{ zIndex: 0, opacity: 'var(--xp-particle-opacity, 0.6)' } as React.CSSProperties} />
}
