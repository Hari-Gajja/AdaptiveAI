import { useEffect, useState } from 'react'
import { BrainCircuit, CheckCircle2, CircleDollarSign, Gauge, GitBranch, Search } from 'lucide-react'
import { Badge, Card, CardHead, Err, KPI, QualityGauge, SkeletonKPIs } from '../components/ui'
import { CostCompare, Flow } from '../components/flow'
import { api, pct, usd } from '../services/api'

export default function CommandCenter() {
  const [a, setA] = useState(null)
  const [stats, setStats] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    const load = () => {
      Promise.all([api.analytics(), api.routingStats()])
        .then(([x, y]) => { if (alive) { setA(x); setStats(y) } })
        .catch((e) => { if (alive) setErr(String(e)) })
    }
    load()
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (err) return <Err>{err}</Err>
  if (!a) return <SkeletonKPIs n={4} />

  const src = a
  const base = src.baseline_cost_usd ?? src.baseline_cost
  const opt = src.optimizer_cost ?? src.total_cost_usd
  const savings = src.savings ?? src.savings_usd
  const savingsPct = src.savings_pct ?? (base ? (100 * (base - opt)) / base : 0)
  const cacheRate = a.cache_hit_rate || 0
  const escRate = a.escalation_rate || 0
  const quality = { opt: a.avg_quality, base: null, kept: null }

  const dist = Object.entries(
    (stats && stats.by_model) || {},
  ).map(([name, value]) => ({ name, value }))
  const totalReq = dist.reduce((s, d) => s + d.value, 0)
  const modelColor = (index) => ['var(--blue)', 'var(--purple)', 'var(--cyan)', 'var(--orange)'][index % 4]

  return (
    <div className="fade-in">
      <div className="status-strip"><span className="live-dot" />LIVE OPERATIONS <span>·</span> analytics from current request history <span className="live-refresh">auto-refreshing</span></div>
      <div className="kpis">
        <KPI label="Cost saved" value={usd(savings)} sub={`${pct(savingsPct.toFixed ? savingsPct.toFixed(1) : savingsPct)} vs always-best`} tone="up" />
        <KPI label="Quality" value={quality.opt != null ? `${(quality.opt * 100).toFixed(1)}%` : '–'} sub={quality.kept != null ? `${(quality.kept * 100).toFixed(1)}% of baseline ${quality.base != null ? (quality.base * 100).toFixed(1) + '%' : ''}` : 'live average'} />
        <KPI label="Cache hit rate" value={`${(cacheRate * 100).toFixed(0)}%`} sub="exact + reusable-context hits" />
        <KPI label="Requests" value={a.total_requests} sub={`LIVE · store mode: ${a.mode}`} />
        <KPI label="Escalations" value={`${(escRate * 100).toFixed(0)}%`} sub="LIVE · quality/provider retries" />
      </div>

      <div className="grid side" style={{ marginTop: 18 }}>
        <div>
          <Card>
            <CardHead
              title="LIVE cost: optimizer vs baseline"
              sub="Current request history. Baseline is counterfactual; benchmark results are shown in Benchmark Lab."
            />
            <CostCompare baseline={base} optimizer={opt} savings={savings} savingsPct={savingsPct} />
          </Card>
        </div>

        <div>
          <Card>
            <CardHead title="How a request flows" sub="The pipeline every request takes." />
            <Flow
              nodes={[
                { title: 'Request', sub: 'prompt + optional reusable context', icon: <Search size={14} /> },
                { title: 'Analyze', sub: 'task type · difficulty · confidence', icon: <BrainCircuit size={14} /> },
                { title: 'Route', sub: 'cheapest model that clears measured thresholds', icon: <GitBranch size={14} /> },
                { title: 'Generate', sub: 'selected model answers', icon: <Gauge size={14} /> },
                { title: 'Verify', sub: 'quality scored; escalate if below bar', icon: <CheckCircle2 size={14} />, status: 'success' },
                { title: 'Measure', sub: 'cost, baseline and quality recorded', icon: <CircleDollarSign size={14} /> },
              ]}
            />
          </Card>

          <Card style={{ marginTop: 18 }}>
            <CardHead title="Model distribution" sub="Where requests actually landed." />
            {dist.length === 0 ? (
              <div className="muted">No requests yet — run the Playground or Benchmark.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {dist.map((d) => (
                  <div key={d.name} className="distribution-row">
                    <div className="row" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
                      <span style={{ fontSize: 13, fontWeight: 500 }}><span className="model-dot" style={{ background: modelColor(dist.indexOf(d)) }} />{d.name}</span>
                      <span className="num muted">{totalReq ? Math.round((d.value / totalReq) * 100) : 0}% · {d.value}</span>
                    </div>
                    <div className="bar"><i style={{ width: `${totalReq ? (d.value / totalReq) * 100 : 0}%`, background: modelColor(dist.indexOf(d)) }} /></div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card style={{ marginTop: 18 }}>
            <CardHead title="Quality guardrail" sub="What the verify step guarantees." />
            <div className="quality-panel">
              <QualityGauge value={quality.opt} threshold={null} />
              <div><Badge tone="good">measured, not assumed</Badge>{quality.kept != null && <Badge tone={quality.kept >= 0.97 ? 'good' : 'warn'}>{(quality.kept * 100).toFixed(1)}% retained</Badge>}<p className="muted">Answers below the threshold are re-routed to a stronger capable model.</p></div>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Capability scores come from our own 24-item benchmark and are labeled measured vs estimated everywhere.
            </p>
          </Card>
        </div>
      </div>
    </div>
  )
}
