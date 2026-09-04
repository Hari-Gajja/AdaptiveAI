import { useEffect, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { Badge, Card, CardHead, Err, KPI, SkeletonKPIs } from '../components/ui'
import { CostCompare, Flow } from '../components/flow'
import { api, pct, usd } from '../services/api'

const CATS_SHORT = {
  easy: 'Easy', coding: 'Coding', reasoning: 'Reasoning', math: 'Math',
  summarization: 'Summary', architecture: 'Architecture',
  long_context: 'Long ctx', repeated_context: 'Cache',
}

function Tip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="chart-tip">
      <div className="t-title">{label}</div>
      {payload.map((p) => (
        <div className="t-row" key={p.dataKey}>{p.name}: {p.dataKey === 'quality' ? p.value : usd(p.value)}</div>
      ))}
    </div>
  )
}

export default function CommandCenter() {
  const [a, setA] = useState(null)
  const [stats, setStats] = useState(null)
  const [bench, setBench] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    Promise.all([api.analytics(), api.routingStats()])
      .then(([x, y]) => { setA(x); setStats(y) })
      .catch((e) => setErr(String(e)))
    api.benchmarkLatest().then(setBench).catch(() => {})
  }, [])

  if (err) return <Err>{err}</Err>
  if (!a) return <SkeletonKPIs n={4} />

  const src = bench || a
  const base = src.baseline_cost_usd ?? src.baseline_cost
  const opt = src.optimizer_cost ?? src.total_cost_usd
  const savings = src.savings ?? src.savings_usd
  const savingsPct = src.savings_pct ?? (base ? (100 * (base - opt)) / base : 0)
  const cacheRate = ((bench ? bench.cache_hit_rate : a.cache_hit_rate) || 0)
  const escRate = ((bench ? bench.escalation_rate : a.escalation_rate) || 0)
  const quality = bench
    ? { opt: bench.optimizer_quality, base: bench.baseline_quality, kept: bench.quality_retention }
    : { opt: a.avg_quality, base: null, kept: null }

  const dist = Object.entries(
    bench ? (bench.model_distribution || {}) : ((stats && stats.by_model) || {}),
  ).map(([name, value]) => ({ name, value }))
  const totalReq = dist.reduce((s, d) => s + d.value, 0)

  // Per-query series from the benchmark (real measured rows).
  const perQ = (bench && bench.per_query) || []
  const costSeries = perQ.map((q, i) => ({
    i: i + 1,
    name: `#${q.id} ${CATS_SHORT[q.category] || q.category}`,
    optimizer: q.actual_cost,
    baseline: q.baseline_cost,
  }))
  const qualityCost = perQ.map((q) => ({
    name: q.final_model,
    cost: q.actual_cost,
    quality: q.quality,
    model: q.final_model,
  }))

  return (
    <div className="fade-in">
      <div className="kpis">
        <KPI label="Cost saved" value={usd(savings)} sub={`${pct(savingsPct.toFixed ? savingsPct.toFixed(1) : savingsPct)} vs always-best`} tone="up" />
        <KPI label="Quality" value={quality.opt != null ? `${(quality.opt * 100).toFixed(1)}%` : '–'} sub={quality.kept != null ? `${(quality.kept * 100).toFixed(1)}% of baseline ${quality.base != null ? (quality.base * 100).toFixed(1) + '%' : ''}` : 'live average'} />
        <KPI label="Cache hit rate" value={`${(cacheRate * 100).toFixed(0)}%`} sub="exact + reusable-context hits" />
        <KPI label="Requests" value={bench ? bench.queries_tested : a.total_requests} sub={bench ? 'benchmark queries' : `store mode: ${a.mode}`} />
        <KPI label="Escalations" value={`${(escRate * 100).toFixed(0)}%`} sub={bench ? `avg latency ${bench.avg_latency_ms} ms` : 'quality-triggered retries'} />
      </div>

      <div className="grid side" style={{ marginTop: 18 }}>
        <div>
          <Card>
            <CardHead
              title="Cost: always-best vs optimizer"
              sub="Baseline is counterfactual (measured tokens × best-model pricing) — no duplicate expensive calls."
            />
            <CostCompare baseline={base} optimizer={opt} savings={savings} savingsPct={savingsPct} />
          </Card>

          {costSeries.length > 0 && (
            <Card style={{ marginTop: 18 }}>
              <CardHead title="Cost per query" sub="Cumulative style view across the 50-query benchmark." />
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={costSeries} margin={{ left: -14, right: 8, top: 6, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="name" hide />
                  <YAxis tickFormatter={(v) => `$${v.toFixed(3)}`} width={64} axisLine={false} tickLine={false} />
                  <Tooltip content={<Tip />} />
                  <Area type="monotone" dataKey="baseline" name="Always-best" stroke="#d1d5db" fill="#f3f4f6" strokeWidth={1.6} />
                  <Area type="monotone" dataKey="optimizer" name="Optimizer" stroke="#0a0c10" fill="#e5e7eb" strokeWidth={1.8} />
                </AreaChart>
              </ResponsiveContainer>
            </Card>
          )}

          {qualityCost.length > 0 && (
            <Card style={{ marginTop: 18 }}>
              <CardHead title="Quality vs cost" sub="Each dot is one query: cost x-axis, quality y-axis." />
              <ResponsiveContainer width="100%" height={250}>
                <ScatterChart margin={{ left: -10, right: 16, top: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" />
                  <XAxis type="number" dataKey="cost" name="cost" tickFormatter={(v) => `$${v.toFixed(3)}`} axisLine={false} tickLine={false} />
                  <YAxis type="number" dataKey="quality" domain={[0, 1]} axisLine={false} tickLine={false} />
                  <ZAxis range={[46, 46]} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<QcTip />} />
                  <Scatter data={qualityCost} fill="#0a0c10" />
                </ScatterChart>
              </ResponsiveContainer>
              <div className="muted" style={{ marginTop: 6 }}>
                The goal is upper-left: low cost, high quality. Verify-and-escalate keeps points up while the router keeps them left.
              </div>
            </Card>
          )}
        </div>

        <div>
          <Card>
            <CardHead title="How a request flows" sub="The pipeline every request takes." />
            <Flow
              nodes={[
                { title: 'Request', sub: 'prompt + optional reusable context' },
                { title: 'Analyze', sub: 'task type · difficulty · confidence' },
                { title: 'Route', sub: 'cheapest model that clears measured thresholds' },
                { title: 'Generate', sub: 'selected model answers' },
                { title: 'Verify', sub: 'quality scored; escalate if below bar' },
                { title: 'Measure', sub: 'cost, baseline and quality recorded' },
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
                  <div key={d.name}>
                    <div className="row" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{d.name}</span>
                      <span className="num muted">{totalReq ? Math.round((d.value / totalReq) * 100) : 0}% · {d.value}</span>
                    </div>
                    <div className="bar"><i style={{ width: `${totalReq ? (d.value / totalReq) * 100 : 0}%` }} /></div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card style={{ marginTop: 18 }}>
            <CardHead title="Quality guardrail" sub="What the verify step guarantees." />
            <div className="row" style={{ gap: 8, marginBottom: 10 }}>
              <Badge tone="good">measured, not assumed</Badge>
              {quality.kept != null && <Badge tone={quality.kept >= 0.97 ? 'good' : 'warn'}>{(quality.kept * 100).toFixed(1)}% retained</Badge>}
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Answers below the quality threshold are automatically re-routed to a stronger model.
              Capability scores come from our own 24-item benchmark and are labeled measured vs estimated everywhere.
            </p>
          </Card>
        </div>
      </div>
    </div>
  )
}

function QcTip({ active, payload }) {
  if (!active || !payload || !payload.length) return null
  const d = payload[0].payload
  return (
    <div className="chart-tip">
      <div className="t-title">{d.model}</div>
      <div className="t-row">cost: {usd(d.cost)}</div>
      <div className="t-row">quality: {d.quality}</div>
    </div>
  )
}
