import { ArrowDown, ArrowDownRight, ArrowRight, ArrowUp, CircleDollarSign, TrendingDown } from 'lucide-react'
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
export function CostCompare({ baseline, optimizer, savings, savingsPct, netSavings, netSavingsPct, direction }) {
  const max = Math.max(baseline || 0, optimizer || 0, 1e-9)
  const risen = useRiseTo(Math.abs(savings || 0))
  const pctText = (savingsPct || 0).toFixed ? savingsPct.toFixed(1) : savingsPct
  const basePct = ((baseline || 0) / max) * 100
  const optPct = ((optimizer || 0) / max) * 100
  const gapPct = Math.max(0, basePct - optPct)
  const isLoss = (direction === 'loss') || (savings != null && savings < 0)
  const isNetLoss = (netSavings != null && netSavings < 0)
  const netPctText = (netSavingsPct || 0).toFixed ? netSavingsPct.toFixed(1) : netSavingsPct
  const lossPctText = Math.abs(Number(pctText) || 0).toFixed(1)
  return (
    <div className="cost-compare" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="cc-row" style={{ '--d': '0ms' }}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <span className="cc-label"><CircleDollarSign size={13} />Baseline · always-best</span>
          <span className="num" style={{ fontWeight: 600 }}>{usd(baseline || 0)}</span>
        </div>
        <div className="bar cc-track">
          <i className="cc-bar cc-base" style={{ width: `${basePct}%`, '--d': '250ms' }} />
        </div>
      </div>

      <div className="cc-row" style={{ '--d': '160ms' }}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <span className={`cc-label cc-label-opt${isLoss ? ' cc-label-loss' : ''}`}><TrendingDown size={13} />Optimizer · this run</span>
          <span className={`num cc-opt-num${isLoss ? ' cc-opt-num-loss' : ''}`} style={{ fontWeight: 700 }}>{usd(optimizer || 0)}</span>
        </div>
        <div className="bar cc-track">
          <i className={`cc-bar cc-opt${isLoss ? ' cc-opt-loss' : ''}`} style={{ width: `${optPct}%`, '--d': '410ms' }} />
        </div>
      </div>

      {savings != null && savings !== 0 && (
        <div className="cc-savings-row" style={{ '--d': '560ms' }}>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
            <span className={`cc-label cc-label-save${isLoss ? ' cc-label-loss' : ''}`}>
              {isLoss ? <ArrowUp size={13} /> : <ArrowDownRight size={13} />}
              {isLoss ? 'Loss vs baseline' : 'Savings'}
            </span>
            <span className={`num cc-savings-num${isLoss ? ' cc-savings-num-loss' : ''}`} style={{ fontWeight: 700 }}>
              {isLoss ? `−${usd(risen)}` : usd(risen)}
            </span>
          </div>
          <div className="bar cc-track cc-gap-track">
            <i className={`cc-bar cc-gap${isLoss ? ' cc-gap-loss' : ''}`} style={{ width: `${gapPct}%`, '--d': '700ms' }} />
          </div>
        </div>
      )}

      {savings != null && savings !== 0 && (
        <div className={`savings-reveal${isLoss ? ' savings-reveal-loss' : ''}`} style={{ '--d': '950ms' }} aria-label={`${isLoss ? 'Loss' : 'Savings'} ${usd(Math.abs(savings))}`}>
          <span className="savings-line" />
          <span className="savings-caption">{isLoss ? `optimization cost more · +${lossPctText}%` : `optimization achieved · −${pctText}%`}</span>
        </div>
      )}
      <div className="row" style={{ gap: 8 }}>
        {isLoss ? (
          <Badge tone="bad">loss {usd(Math.abs(savings))}</Badge>
        ) : (
          <Badge tone="good">saved {usd(savings)}</Badge>
        )}
        {isLoss ? <Badge tone="bad">+{lossPctText}%</Badge> : <Badge tone="good">−{pctText}%</Badge>}
        {netSavings != null && (
          isNetLoss
            ? <Badge tone="bad" title="savings minus control-plane overhead">net −{usd(Math.abs(netSavings))} (−{Math.abs(Number(netPctText) || 0).toFixed(1)}%)</Badge>
            : <Badge tone="good" title="savings minus control-plane overhead">net {usd(netSavings)} ({netPctText}%)</Badge>
        )}
      </div>
    </div>
  )
}
