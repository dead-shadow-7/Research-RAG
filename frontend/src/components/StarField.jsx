import { useEffect, useRef } from 'react'

/**
 * Ambient backdrop for the chat pane: a sparse static star field with the occasional
 * star falling on a slight diagonal, trailing a short gradient tail.
 *
 * Canvas rather than CSS keyframes, because the tail has to fade *along its length* --
 * a gradient stroke behind a moving head. CSS can move a dot; it cannot do that.
 *
 * Deliberately restrained: peak star opacity is 0.42 on pure black and the tails are
 * ~1px, so serif body text stays fully readable over it. It also stops animating when
 * the tab is hidden, and never animates at all under prefers-reduced-motion.
 */

const STAR_AREA = 16000 // one static star per this many CSS px²
const MAX_STARS = 90
// Concurrency is lifetime ÷ spawn interval, so these three are tuned together:
// a ~2.4s mean lifetime against a ~0.78s mean interval keeps 2-5 in flight.
const SPAWN_MIN_MS = 450
const SPAWN_MAX_MS = 1100
const MAX_FALLING = 5

const rand = (min, max) => min + Math.random() * (max - min)

export default function StarField() {
  const ref = useRef(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')

    let width = 0
    let height = 0
    let stars = []
    let falling = []
    let raf = 0
    let last = 0
    let nextSpawn = rand(SPAWN_MIN_MS, SPAWN_MAX_MS)

    const seedStars = () => {
      const count = Math.min(MAX_STARS, Math.round((width * height) / STAR_AREA))
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        r: rand(0.35, 1.05),
        alpha: rand(0.1, 0.42),
        // Out-of-phase so the field breathes rather than pulsing in unison.
        phase: Math.random() * Math.PI * 2,
        rate: rand(0.15, 0.5),
      }))
    }

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      width = rect.width
      height = rect.height
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      seedStars()
    }

    const spawn = () => {
      const angle = rand(0.2, 0.38) // radians off vertical: a lean, not a sweep
      const speed = rand(90, 170)
      falling.push({
        x: rand(-0.1, 1) * width,
        y: rand(-0.15, 0.25) * height,
        vx: Math.sin(angle) * speed,
        vy: Math.cos(angle) * speed,
        len: rand(38, 72),
        life: 0,
        ttl: rand(1.8, 3),
      })
    }

    const drawStars = (seconds) => {
      ctx.fillStyle = '#ffffff'
      for (const s of stars) {
        ctx.globalAlpha = s.alpha * (0.75 + 0.25 * Math.sin(seconds * s.rate + s.phase))
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    const drawFalling = (f) => {
      const speed = Math.hypot(f.vx, f.vy)
      const ux = f.vx / speed
      const uy = f.vy / speed
      const tailX = f.x - ux * f.len
      const tailY = f.y - uy * f.len

      // Fade in quickly, then out across the rest of its life, so nothing pops.
      const k = Math.min(1, f.life / 0.25) * Math.max(0, 1 - f.life / f.ttl)

      const gradient = ctx.createLinearGradient(f.x, f.y, tailX, tailY)
      gradient.addColorStop(0, `rgba(255,255,255,${0.5 * k})`)
      gradient.addColorStop(0.45, `rgba(226,232,240,${0.16 * k})`)
      gradient.addColorStop(1, 'rgba(255,255,255,0)')

      ctx.strokeStyle = gradient
      ctx.lineWidth = 1.1
      ctx.lineCap = 'round'
      ctx.beginPath()
      ctx.moveTo(f.x, f.y)
      ctx.lineTo(tailX, tailY)
      ctx.stroke()

      // The head carries the only glow in the scene.
      ctx.globalAlpha = 0.65 * k
      ctx.fillStyle = '#ffffff'
      ctx.shadowColor = 'rgba(219,234,254,0.85)'
      ctx.shadowBlur = 5
      ctx.beginPath()
      ctx.arc(f.x, f.y, 0.9, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0
      ctx.globalAlpha = 1
    }

    const frame = (now) => {
      const dt = Math.min((now - last) / 1000, 0.05) // clamp after a tab stall
      last = now

      ctx.clearRect(0, 0, width, height)
      drawStars(now / 1000)

      nextSpawn -= dt * 1000
      if (nextSpawn <= 0) {
        if (falling.length < MAX_FALLING) spawn()
        nextSpawn = rand(SPAWN_MIN_MS, SPAWN_MAX_MS)
      }

      falling = falling.filter((f) => f.life < f.ttl && f.y - f.len < height * 1.2)
      for (const f of falling) {
        f.life += dt
        f.x += f.vx * dt
        f.y += f.vy * dt
        drawFalling(f)
      }

      raf = requestAnimationFrame(frame)
    }

    const start = () => {
      if (raf) return
      last = performance.now()
      raf = requestAnimationFrame(frame)
    }

    const stop = () => {
      cancelAnimationFrame(raf)
      raf = 0
    }

    /** Reduced motion still gets the star field, just held still. */
    const drawStill = () => {
      ctx.clearRect(0, 0, width, height)
      drawStars(0)
    }

    const onVisibility = () => (document.hidden ? stop() : start())
    const onMotionPreference = () => {
      stop()
      if (reduce.matches) drawStill()
      else start()
    }

    const observer = new ResizeObserver(() => {
      resize()
      if (reduce.matches) drawStill()
    })
    observer.observe(canvas)

    resize()
    if (reduce.matches) {
      drawStill()
    } else {
      start()
      document.addEventListener('visibilitychange', onVisibility)
    }
    reduce.addEventListener('change', onMotionPreference)

    return () => {
      stop()
      observer.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      reduce.removeEventListener('change', onMotionPreference)
    }
  }, [])

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  )
}
