import { useEffect, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { BrainCircuit, CheckCircle2, CircleDollarSign, Gauge, GitBranch, Search } from 'lucide-react'
import { Badge, AnimatedValue, Card, CardHead, Err, KPI, QualityGauge, SkeletonKPIs } from '../components/ui'
import { CostCompare, Flow } from '../components/flow'
import { api, pct, usd } from '../services/api'

const CATS_SHORT = {
  easy: 'Easy', coding: 'Coding', reasoning: 'Reasoning', math: 'Math',
  summarization: 'Summary', architecture: 'Architecture',
  long_context: 'Long ctx', repeated_context: 'Cache',
}

const MODEL_COLORS = ['#2563eb', '#d97706', '#059669', '#db2777', '#7c3aed', '#0891b2']

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
    category: CATS_SHORT[q.category] || q.category,
  }))
  const modelNames = [...new Set(qualityCost.map((d) => d.model))].sort()

  // Savings storytelling — every number from the live API, nothing fabricated.
  const storySteps = [
    { key: 'baseline', label: 'Baseline', desc: 'always-best model for every request', value: usd(base), tone: 'muted' },
    { key: 'optimizer', label: 'Smart routing', desc: 'cheapest capable model, verified', value: usd(opt), tone: 'text' },
    { key: 'saved', label: 'Total saved', desc: `${pct(savingsPct.toFixed ? savingsPct.toFixed(1) : savingsPct)} below always-best`, value: usd(savings), tone: 'good' },
  ]

  return (
    <div className="fade-in">
      <div className="status-strip"><span className="live-dot" />LIVE OPERATIONS <span>·</span> analytics from current request history</div>
      <div className="kpis">
        <KPI label="Cost saved" value={usd(savings)} sub={`${pct(savingsPct.toFixed ? savingsPct.toFixed(1) : savingsPct)} vs always-best`} tone="up" />
        <KPI label="Quality" value={quality.opt != null ? `${(quality.opt * 100).toFixed(1)}%` : '–'} sub={quality.kept != null ? `${(quality.kept * 100).toFixed(1)}% of baseline ${quality.base != null ? (quality.base * 100).toFixed(1) + '%' : ''}` : 'live average'} />
        <KPI label="Cache hit rate" value={`${(cacheRate * 100).toFixed(0)}%`} sub="exact + reusable-context hits" />
        <KPI label="Requests" value={a.total_requests} sub={`LIVE · store mode: ${a.mode}`} />
        <KPI label="Escalations" value={`${(escRate * 100).toFixed(0)}%`} sub="LIVE · quality/provider retries" />
      </div>

      {/* Savings storytelling — Baseline → Smart routing → Total saved (all real API values). */}
      <Card className="story-card" style={{ marginTop: 18 }}>
        <CardHead title="The savings story" sub="How measured savings are built up — every number from the live API." />
        <div className="story-steps">
          {storySteps.map((s, i) => (
            <div key={s.key} className={`story-step story-${s.tone}`} style={{ '--d': `${i * 260}ms` }}>
              <div className="story-label">{s.label}</div>
              <div className="story-value num"><AnimatedValue value={s.value} /></div>
              <div className="story-desc">{s.desc}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid side" style={{ marginTop: 18 }}>
        <div>
          <Card>
            <CardHead
              title="LIVE cost: optimizer vs baseline"
              sub="Current request history. Baseline is counterfactual; benchmark results are shown in Benchmark Lab."
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
                  <Area
                    type="monotone"
                    dataKey="baseline"
                    name="Always-best"
                    stroke="#d1d5db"
                    fill="#f3f4f6"
                    strokeWidth={1.6}
                    animationBegin={250}
                    animationDuration={1400}
                  />
                  <Area
                    type="monotone"
                    dataKey="optimizer"
                    name="Optimizer"
                    stroke="#0a0c10"
                    fill="#e5e7eb"
                    strokeWidth={1.8}
                    animationBegin={700}
                    animationDuration={1400}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </Card>
          )}

          {qualityCost.length > 0 && (
            <Card style={{ marginTop: 18 }}>
              <CardHead title="Quality vs cost" sub="Each dot is one query: cost x-axis, quality y-axis. Dashed line = benchmark quality bar." />
              <ResponsiveContainer width="100%" height={250}>
                <ScatterChart margin={{ left: -10, right: 16, top: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" />
                  <XAxis type="number" dataKey="cost" name="cost" tickFormatter={(v) => `$${v.toFixed(3)}`} axisLine={false} tickLine={false} />
                  <YAxis type="number" dataKey="quality" domain={[0, 1]} axisLine={false} tickLine={false} />
                  <ZAxis range={[90, 90]} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<QcTip />} />
                  <Legend />
                  {bench && bench.optimizer_quality != null && (
                    <ReferenceLine
                      y={bench.optimizer_quality}
                      stroke="#059669"
                      strokeDasharray="5 4"
                      label={{ value: `quality bar ${(bench.optimizer_quality * 100).toFixed(0)}%`, position: 'insideTopRight', fontSize: 10, fill: '#6b7280' }}
                    />
                  )}
                  {modelNames.map((model, index) => (
                    <Scatter
                      key={model}
                      name={model}
                      data={qualityCost.filter((d) => d.model === model)}
                      fill={MODEL_COLORS[index % MODEL_COLORS.length]}
                      stroke="#fff"
                      strokeWidth={1.5}
                      className="qc-dot"
                      animationBegin={index * 180}
                      animationDuration={900}
                    />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
              <div className="muted" style={{ marginTop: 6 }}>
                The goal is upper-left: low cost, high quality. Verify-and-escalate keeps points up while the router keeps them left.
              </div>
            </Card>
          )}

          {bench && (
            <Card className="benchmark-pulse" style={{ marginTop: 18 }}>
              <CardHead title="Latest benchmark" sub={`${bench.baseline_quality_mode || 'sampled'} baseline quality · stored result`} />
              <div className="row benchmark-summary">
                <div><span className="summary-label">Optimizer</span><strong>{(bench.optimizer_quality * 100).toFixed(1)}%</strong></div>
                <div><span className="summary-label">Saved</span><strong className="good-text">{bench.savings_pct}%</strong></div>
                <div><span className="summary-label">Queries</span><strong>{bench.queries_tested}</strong></div>
                <Badge tone="good"><CheckCircle2 size={13} /> {bench.failed_requests ? `${bench.failed_requests} failed` : 'completed'}</Badge>
              </div>
            </Card>
          )}
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

          <Card style={{ marginTop: 18 }} className="decision-card">
            <CardHead title="Decision intelligence" sub="How the router decided, from live routing stats." />
            <div className="decision-grid">
              <div className="decision-item" style={{ '--d': '0ms' }}>
                <div className="d-label">Distinct models used</div>
                <div className="d-value num">{dist.length}</div>
              </div>
              <div className="decision-item" style={{ '--d': '120ms' }}>
                <div className="d-label">Top model share</div>
                <div className="d-value num">{totalReq && dist.length ? `${Math.round((Math.max(...dist.map((d) => d.value)) / totalReq) * 100)}%` : '–'}</div>
              </div>
              <div className="decision-item" style={{ '--d': '240ms' }}>
                <div className="d-label">Escalation rate</div>
                <div className="d-value num">{(escRate * 100).toFixed(0)}%</div>
              </div>
              <div className="decision-item" style={{ '--d': '360ms' }}>
                <div className="d-label">Cache hit rate</div>
                <div className="d-value num">{(cacheRate * 100).toFixed(0)}%</div>
              </div>
            </div>
            <p className="muted" style={{ margin: '10px 0 0' }}>
              The router picks the cheapest model that clears its measured capability bar; escalations and cache hits show the guardrail and reuse in action.
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
      {d.category && <div className="t-row muted">category: {d.category}</div>}
    </div>
  )
}
