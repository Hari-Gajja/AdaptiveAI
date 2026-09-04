import { ArrowDown, ArrowRight } from 'lucide-react'
import { usd } from '../services/api'
import { Badge } from './ui'

/** Vertical node-link flow used by routing + cache visualizations. */
export function Flow({ nodes }) {
  return (
    <div className="flow">
      {nodes.map((n, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
          <div className={`flow-node${n.active ? ' active' : ''}${n.branch ? ' branch' : ''}`} style={n.style}>
            <div className="n-title">{n.title}</div>
            {n.sub && <div className="n-sub">{n.sub}</div>}
            {n.body}
          </div>
          {i < nodes.length - 1 && <div className="flow-link" aria-hidden />}
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

/** Cost comparison bar: always-best vs optimizer. */
export function CostCompare({ baseline, optimizer, savings, savingsPct }) {
  const max = Math.max(baseline || 0, optimizer || 0, 1e-9)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {[
        { name: 'Always-best', val: baseline || 0, tone: 'var(--faint)' },
        { name: 'Optimizer', val: optimizer || 0, tone: 'var(--text)' },
      ].map((r) => (
        <div key={r.name}>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
            <span className="muted" style={{ fontSize: 13 }}>{r.name}</span>
            <span className="num" style={{ fontWeight: 600 }}>{usd(r.val)}</span>
          </div>
          <div className="bar">
            <i style={{ width: `${(r.val / max) * 100}%`, background: r.tone }} />
          </div>
        </div>
      ))}
      <div className="row" style={{ gap: 8 }}>
        <Badge tone="good">saved {usd(savings)}</Badge>
        <Badge tone="good">−{(savingsPct || 0).toFixed ? savingsPct.toFixed(1) : savingsPct}%</Badge>
      </div>
    </div>
  )
}
