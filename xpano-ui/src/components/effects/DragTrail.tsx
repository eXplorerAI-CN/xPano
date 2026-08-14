import { useEffect, useRef } from 'react'

interface TrailPoint {
  x: number
  y: number
  t: number
  pressure: number
}

const TRAIL_LIFETIME = 760
const MIN_DISTANCE = 7

function viewportSize() {
  const viewport = window.visualViewport
  return {
    width: Math.round(viewport?.width || window.innerWidth),
    height: Math.round(viewport?.height || window.innerHeight),
  }
}

function isBlockedTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return true
  return Boolean(target.closest([
    'button',
    'a',
    'input',
    'textarea',
    'select',
    'img',
    'video',
    'canvas',
    '[contenteditable="true"]',
    '[role="button"]',
    '[role="slider"]',
    '[role="switch"]',
    '.no-drag',
    '.drag-region',
    '.cursor-pointer',
    '.cursor-ew-resize',
    '.motion-press',
    '.glass-control',
    '.icon-tile',
    '.icon-tile-lg',
    '.terminal',
    '.number-input-shell',
    '.theme-input',
    '[data-trail-block]',
  ].join(',')))
}

function readRgb(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const raw = value || fallback
  return raw.replace(/,/g, ' ').trim().split(/\s+/).slice(0, 3).join(', ')
}

export function DragTrail() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const maybeCtx = canvas.getContext('2d')
    if (!maybeCtx) return
    const ctx = maybeCtx

    let width = 0
    let height = 0
    let dpr = 1
    let frame = 0
    let drawing = false
    let lastPoint: TrailPoint | null = null
    let points: TrailPoint[] = []
    let trailRgb = '110, 167, 219'
    let sparkRgb = '53, 208, 216'
    let coreRgb = '237, 244, 252'

    const readThemeTokens = () => {
      trailRgb = readRgb('--xp-trail-rgb', '110, 167, 219')
      sparkRgb = readRgb('--xp-trail-spark-rgb', '53, 208, 216')
      coreRgb = readRgb('--xp-trail-core-rgb', '237, 244, 252')
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

    const requestDraw = () => {
      if (!frame) frame = requestAnimationFrame(draw)
    }

    const addPoint = (x: number, y: number, pressure = 0.5) => {
      const now = performance.now()
      const next = { x, y, t: now, pressure: Math.max(0.35, pressure || 0.5) }
      if (lastPoint) {
        const dx = x - lastPoint.x
        const dy = y - lastPoint.y
        if (Math.hypot(dx, dy) < MIN_DISTANCE) return
      }
      points.push(next)
      lastPoint = next
      requestDraw()
    }

    function draw(now: number) {
      frame = 0
      ctx.clearRect(0, 0, width, height)
      points = points.filter((point) => now - point.t < TRAIL_LIFETIME)
      if (points.length < 2) return

      ctx.save()
      readThemeTokens()
      ctx.globalCompositeOperation = 'source-over'
      ctx.lineJoin = 'miter'
      if (points.length >= 2) {
        const head = points[points.length - 1]
        const headLife = Math.max(0, 1 - (now - head.t) / TRAIL_LIFETIME)
        const path = new Path2D()
        path.moveTo(points[0].x, points[0].y)
        for (let i = 1; i < points.length - 1; i++) {
          const current = points[i]
          const next = points[i + 1]
          path.quadraticCurveTo(current.x, current.y, (current.x + next.x) / 2, (current.y + next.y) / 2)
        }
        path.lineTo(head.x, head.y)

        const gradient = ctx.createLinearGradient(points[0].x, points[0].y, head.x, head.y)
        gradient.addColorStop(0, `rgba(${trailRgb},0)`)
        gradient.addColorStop(0.38, `rgba(${coreRgb},${0.44 * headLife})`)
        gradient.addColorStop(0.78, `rgba(${coreRgb},${0.78 * headLife})`)
        gradient.addColorStop(1, `rgba(${sparkRgb},${0.42 * headLife})`)

        ctx.lineCap = 'round'
        ctx.strokeStyle = `rgba(${sparkRgb},${0.16 * headLife})`
        ctx.lineWidth = 8.5 * headLife
        ctx.shadowBlur = 10 * headLife
        ctx.shadowColor = `rgba(${sparkRgb},${0.24 * headLife})`
        ctx.stroke(path)

        ctx.lineCap = 'round'
        ctx.strokeStyle = gradient
        ctx.lineWidth = Math.max(1.1, 2.4 * headLife)
        ctx.shadowBlur = 3 * headLife
        ctx.shadowColor = `rgba(${coreRgb},${0.24 * headLife})`
        ctx.stroke(path)
      }
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1]
        const b = points[i]
        const age = now - b.t
        const life = Math.max(0, 1 - age / TRAIL_LIFETIME)
        if (life <= 0) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const len = Math.max(1, Math.hypot(dx, dy))
        const nx = -dy / len
        const ny = dx / len
        const speed = Math.min(1, len / 34)
        const alpha = life * life * (0.42 + speed * 0.36)
        const taper = Math.sin((i / Math.max(points.length - 1, 1)) * Math.PI)
        const widthScale = 0.75 + b.pressure * 0.7 + taper * 0.55
        ctx.lineWidth = Math.max(0.45, 0.85 * life)
        ctx.shadowBlur = 0
        ctx.strokeStyle = `rgba(${trailRgb},${alpha * 0.28})`
        for (const side of [-1, 1]) {
          const offset = side * (2.8 + speed * 2.2) * widthScale
          ctx.beginPath()
          ctx.moveTo(a.x + nx * offset, a.y + ny * offset)
          ctx.lineTo(b.x + nx * offset * 0.72, b.y + ny * offset * 0.72)
          ctx.stroke()
        }

        if (i % 3 === 0 && speed > 0.35) {
          const chip = 5 + speed * 8
          const cx = a.x + dx * 0.58
          const cy = a.y + dy * 0.58
          ctx.strokeStyle = `rgba(${coreRgb},${alpha * 0.25})`
          ctx.lineWidth = 0.7 * life
          ctx.beginPath()
          ctx.moveTo(cx, cy)
          ctx.lineTo(cx + nx * chip, cy + ny * chip)
          ctx.stroke()
        }
      }
      ctx.restore()

      if (points.length > 0) requestDraw()
    }

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || isBlockedTarget(event.target)) return
      drawing = true
      lastPoint = null
      points = []
      addPoint(event.clientX, event.clientY, event.pressure)
    }

    const onPointerMove = (event: PointerEvent) => {
      if (!drawing) return
      addPoint(event.clientX, event.clientY, event.pressure)
    }

    const stop = () => {
      drawing = false
      lastPoint = null
      requestDraw()
    }

    resize()
    window.addEventListener('resize', resize)
    window.visualViewport?.addEventListener('resize', resize)
    window.addEventListener('pointerdown', onPointerDown, { passive: true })
    window.addEventListener('pointermove', onPointerMove, { passive: true })
    window.addEventListener('pointerup', stop, { passive: true })
    window.addEventListener('pointercancel', stop, { passive: true })

    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
      window.visualViewport?.removeEventListener('resize', resize)
      window.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [])

  return <canvas ref={canvasRef} className="pointer-events-none fixed inset-0" style={{ zIndex: 18 }} />
}
