import { useEffect, useRef, type RefObject } from 'react'
import gsap from 'gsap'

interface StaggerOptions {
  /** Selector scoped to the container to find the items to stagger in. */
  selector?: string
  from?: gsap.TweenVars
  to?: gsap.TweenVars
  /** Re-run when this value changes (e.g. a list length). */
  deps?: ReadonlyArray<unknown>
}

/**
 * Fade+rise a batch of elements in sequence on mount (and whenever `deps` change).
 * Replaces the ad-hoc `gsap.fromTo` blocks scattered across pages.
 */
export function useStaggerEnter<T extends HTMLElement = HTMLDivElement>(
  options: StaggerOptions = {}
): RefObject<T | null> {
  const ref = useRef<T>(null)
  const { selector = '[data-enter]', from, to, deps = [] } = options

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const targets = selector === 'self' ? node : node.querySelectorAll<HTMLElement>(selector)
    if (!targets || (Array.isArray(targets) ? targets.length === 0 : !(targets as NodeListOf<Element>).length)) return
    const ctx = gsap.context(() => {
      gsap.fromTo(
        targets as gsap.TweenTarget,
        { autoAlpha: 0, y: 16, ...from },
        { autoAlpha: 1, y: 0, duration: 0.5, ease: 'power3.out', stagger: 0.04, ...to }
      )
    }, node)
    return () => ctx.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return ref
}

interface TiltOptions {
  /** Max rotation in degrees. */
  max?: number
  /** Perspective in px. */
  perspective?: number
}

/**
 * Attach a pointer-driven 3D tilt to a card. Returns a ref to spread on the target.
 * Uses GSAP quickTo for buttery per-axis smoothing and cleans up on unmount.
 */
export function useTilt<T extends HTMLElement = HTMLDivElement>(options: TiltOptions = {}): RefObject<T | null> {
  const ref = useRef<T>(null)
  const { max = 8, perspective = 600 } = options

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    node.style.transformStyle = 'preserve-3d'
    const setX = gsap.quickTo(node, 'rotationY', { duration: 0.4, ease: 'power2.out' })
    const setY = gsap.quickTo(node, 'rotationX', { duration: 0.4, ease: 'power2.out' })

    const onMove = (event: PointerEvent) => {
      const rect = node.getBoundingClientRect()
      const px = (event.clientX - rect.left) / rect.width - 0.5
      const py = (event.clientY - rect.top) / rect.height - 0.5
      setX(px * max * 2)
      setY(-py * max * 2)
    }
    const onLeave = () => {
      setX(0)
      setY(0)
    }

    node.addEventListener('pointermove', onMove)
    node.addEventListener('pointerleave', onLeave)
    return () => {
      node.removeEventListener('pointermove', onMove)
      node.removeEventListener('pointerleave', onLeave)
    }
  }, [max, perspective])

  void perspective
  return ref
}

interface MorphOptions {
  /** Trigger re-morph when this changes (e.g. an idle↔running flag). */
  trigger: unknown
}

/**
 * Cross-fade+slide between two rendered states. Attach the returned ref to the
 * wrapper that swaps its children when `trigger` changes. The new content rises
 * in while the layout settles.
 */
export function useMorphSwap<T extends HTMLElement = HTMLDivElement>(options: MorphOptions): RefObject<T | null> {
  const ref = useRef<T>(null)
  const { trigger } = options
  const first = useRef(true)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (first.current) {
      first.current = false
      return
    }
    const ctx = gsap.context(() => {
      gsap.fromTo(
        node,
        { autoAlpha: 0, y: 18, scale: 0.985 },
        { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'power3.out' }
      )
    }, node)
    return () => ctx.revert()
  }, [trigger])

  return ref
}
