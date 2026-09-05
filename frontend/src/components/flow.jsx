import { ArrowDown, ArrowRight } from 'lucide-react'
import { usd } from '../services/api'
import { Badge } from './ui'
import { useEffect, useRef, useState } from 'react'

/** Vertical node-link flow used by routing + cache visualizations. */
export function Flow({ nodes }) {
  return (
    <div className="flow">
      {nodes.map((n, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
          <div className={`flow-node${n.active ? ' active' : ''}${n.status ? ` ${n.status}` : ''}${n.branch ? ' branch' : ''}`} style={n.style}>
            {n.icon && <span className="flow-icon">{n.icon}</span>}
            <div className="n-title">{n.title}</div>
            {n.sub && <div className="n-sub">{n.sub}</div>}
            {n.body}
          </div>
          {i < nodes.length - 1 && <div className={`flow-link${n.active || nodes[i + 1]?.active ? ' active' : ''}`} aria-hidden />}
        </div>
      ))}
    </div>
  )
}

export function FlowLink() {
  return <div className="flow-link" aria-hidden />
}

export function DownArrow() {
  return <ArrowDown size={14} color="var(--muted)" aria-hidden />
}

export function RightArrow() {
  return <ArrowRight size={14} color="var(--muted)" aria-hidden />
}

/** Count-up to a dollar value (used by the savings moment). */
function useRiseTo(target, duration = 1200) {
  const [v, setV] = useState(0)
  const ref = useRef(0)
  useEffect(() => {
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced || !Number.isFinite(target)) { setV(target || 0); return undefined }
    const from = ref.current || 0
    const start = performance.now()
    let frame
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      const cur = from + (target - from) * eased
      ref.current = cur
      setV(cur)
      if (p < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target, duration])
  return v
}

/** Cost comparison bar: always-best vs optimizer, then the savings moment. */
export function CostCompare({ baseline, optimizer, savings, savingsPct }) {
  const max = Math.max(baseline || 0, optimizer || 0, 1e-9)
  const risen = useRiseTo(savings || 0)
  const pctText = (savingsPct || 0).toFixed ? savingsPct.toFixed(1) : savingsPct
  return (
    <div className="cost-compare" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      { [
        { name: 'Baseline (always-best)', val: baseline || 0, tone: 'var(--faint)', cls: '' },
        { name: 'Optimizer (this run)', val: optimizer || 0, tone: 'var(--text)', cls: 'cc-opt' },
      ].map((r, i) => (
        <div key={r.name} className="cc-row" style={{ '--d': `${i * 160}ms` }}>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
            <span className="muted" style={{ fontSize: 13 }}>{r.name}</span>
            <span className="num" style={{ fontWeight: 600 }}>{usd(r.val)}</span>
          </div>
          <div className="bar">
            <i className="cc-bar" style={{ width: `${(r.val / max) * 100}%`, background: r.tone, '--d': `${250 + i * 160}ms` }} />
          </div>
        </div>
      ))}
      { savings > 0 && (
        <div className="cc-savings-row" style={{ '--d': '560ms' }}>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
            <span className="muted" style={{ fontSize: 13 }}>Savings</span>
            <span className="num cc-savings-num" style={{ fontWeight: 700 }}>{usd(risen)}</span>
          </div>
          <div className="bar cc-savings-bar">
            <i className="cc-bar" style={{ width: `${Math.min(100, ((savings || 0) / max) * 100)}%`, '--d': '700ms' }} />
          </div>
        </div>
      )}
      { savings > 0 && (
        <div className="savings-reveal" style={{ '--d': '950ms' }} aria-label={`Savings ${usd(savings)}`}>
          <span className="savings-line" />
          <span className="savings-caption">optimization achieved · −{pctText}%</span>
        </div>
      )}
      <div className="row" style={{ gap: 8 }}>
        <Badge tone="good">saved {usd(savings)}</Badge>
        <Badge tone="good">−{pctText}%</Badge>
      </div>
    </div>
  )
}
