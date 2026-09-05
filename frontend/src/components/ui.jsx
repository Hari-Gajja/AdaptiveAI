import { useEffect, useRef, useState } from 'react'

export function Badge({ tone, children, title }) {
  return (
    <span className={`badge${tone ? ` ${tone}` : ''}`} title={title}>
      <span className="b-dot" />
      {children}
    </span>
  )
}

export function Card({ className = '', children, ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}>
      {children}
    </div>
  )
}

export function CardHead({ title, sub, actions }) {
  return (
    <div className="card-head">
      <div>
        <div className="section-title">{title}</div>
        {sub && <div className="section-sub" style={{ margin: '4px 0 0' }}>{sub}</div>}
      </div>
      <div className="spacer" />
      {actions}
    </div>
  )
}

export function KPI({ label, value, sub, tone }) {
  return (
    <div className={`card kpi fade-in ${tone ? `kpi-${tone}` : ''}`}>
      <div className="k-label"><span className="k-signal" />{label}</div>
      <div className="k-value num"><AnimatedValue value={value} /></div>
      {sub && (
        <div className="k-sub">
          {tone === 'up' && <span className="up">▲</span>}
          {tone === 'down' && <span className="down">▼</span>}
          {sub}
        </div>
      )}
    </div>
  )
}

/**
 * AnimatedValue — Graphy-style number morphing.
 * - First render: counts 0 → target (entrance).
 * - Value CHANGES: morphs from the previously shown number to the new one,
 *   with a subtle highlight pulse on the container (k-flash handled via [data-changed]).
 * Never fabricates: renders the exact string when no number is present.
 */
export function AnimatedValue({ value, duration = 850, morphDuration = 650 }) {
  const text = String(value ?? '–')
  const match = text.match(/^(.*?)(-?\d+(?:\.\d+)?)(.*)$/)
  const [shown, setShown] = useState(text)
  const [changed, setChanged] = useState(false)
  const prevNum = useRef(null)
  const frame = useRef(0)
  const first = useRef(true)

  useEffect(() => {
    if (!match) { prevNum.current = null; setShown(text); first.current = false; return undefined }
    const prefix = match[1]
    const target = Number(match[2])
    const suffix = match[3]
    const decimals = match[2].includes('.') ? match[2].split('.')[1].length : 0
    const reduced = typeof window !== 'undefined'
      && window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const from = first.current || prevNum.current == null ? 0 : prevNum.current
    const dur = reduced ? 0 : first.current ? duration : morphDuration

    const render = (n) => setShown(`${prefix}${n.toFixed(decimals)}${suffix}`)

    if (dur === 0) {
      render(target)
    } else {
      const start = performance.now()
      const tick = (now) => {
        const progress = Math.min(1, (now - start) / dur)
        const eased = 1 - Math.pow(1 - progress, 3)
        render(from + (target - from) * eased)
        if (progress < 1) frame.current = requestAnimationFrame(tick)
        else { prevNum.current = target; first.current = false }
      }
      frame.current = requestAnimationFrame(tick)
    }
    if (!first.current && prevNum.current != null && prevNum.current !== target && !reduced) {
      setChanged(true)
      const t = setTimeout(() => setChanged(false), 900)
      return () => { clearTimeout(t); cancelAnimationFrame(frame.current) }
    }
    first.current = false
    prevNum.current = target
    return () => cancelAnimationFrame(frame.current)
  }, [text, duration, morphDuration])

  return (
    <span className={`av${changed ? ' av-changed' : ''}`}>{shown}</span>
  )
}

/** Count-up for already-formatted percentages/counts in rows (distribution, tables). */
export function CountUp({ value, suffix = '', duration = 900 }) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return <>{value}{suffix}</>
  return <AnimatedValue value={`${numeric}${suffix}`} duration={duration} />
}

export function QualityGauge({ value, threshold, method }) {
  const numeric = Number(value)
  const safe = Number.isFinite(numeric) ? Math.max(0, Math.min(1, numeric)) : 0
  const passed = threshold == null || numeric >= Number(threshold)
  const marker = threshold != null && Number.isFinite(Number(threshold))
    ? Math.max(0, Math.min(100, Number(threshold) * 100))
    : null
  return (
    <div className={`quality-gauge${passed ? '' : ' warn'}`} style={{ '--gauge': `${safe * 100}%` }}>
      {marker != null && <span className="g-marker" style={{ '--marker': `${marker}%` }} aria-hidden />}
      <div>
        <span className="g-value">{Number.isFinite(numeric) ? numeric.toFixed(2) : 'N/A'}</span>
        <span className="g-label">{threshold == null ? 'average' : passed ? 'verified' : 'below bar'}</span>
        {method && <span className="g-method">{method}</span>}
      </div>
    </div>
  )
}

export function Bar({ value, good, label, right }) {
  return (
    <div className="bar-row">
      {label && <span className="muted" style={{ minWidth: 120 }}>{label}</span>}
      <div className={`bar${good ? ' good' : ''}`} role="progressbar" aria-valuenow={Math.round((value || 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${Math.min(100, Math.max(0, (value || 0) * 100))}%` }} />
      </div>
      <span className="val">{right != null ? right : `${Math.round((value || 0) * 100)}%`}</span>
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <div className="e-title">{title}</div>
      <div>{children}</div>
    </div>
  )
}

export function Skeleton({ h = 14, w = '100%', style }) {
  return <div className="skel" style={{ height: h, width: w, ...style }} />
}

export function SkeletonKPIs({ n = 4 }) {
  return (
    <div className="kpis">
      {Array.from({ length: n }).map((_, i) => (
        <div className="card kpi" key={i}>
          <Skeleton h={11} w="55%" />
          <Skeleton h={28} w="70%" style={{ marginTop: 10 }} />
          <Skeleton h={11} w="85%" style={{ marginTop: 9 }} />
        </div>
      ))}
    </div>
  )
}

export function Err({ children }) {
  if (!children) return null
  return <div className="err-box" role="alert">{children}</div>
}
