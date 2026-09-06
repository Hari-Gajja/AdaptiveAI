import { useEffect, useState } from 'react'
import { BrainCircuit, CheckCircle2, CircleDollarSign, Gauge, GitBranch, Search } from 'lucide-react'
import { Badge, Card, CardHead, Err, KPI, QualityGauge, SkeletonKPIs } from '../components/ui'
import { CostCompare, Flow } from '../components/flow'
import { api, pct, usd } from '../services/api'

export default function CommandCenter() {
  const [a, setA] = useState(null)
  const [stats, setStats] = useState(null)
  const [cp, setCp] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    const load = () => {
      Promise.all([api.analytics(), api.routingStats(), api.controlPlane().catch(() => null)])
        .then(([x, y, z]) => { if (alive) { setA(x); setStats(y); setCp(z) } })
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
  const netSavings = src.net_savings_usd ?? (savings != null ? savings - (src.control_plane_cost_usd ?? 0) : null)
  const netSavingsPct = src.net_savings_pct ?? (base ? (100 * (netSavings ?? 0)) / base : 0)
  const savingsDir = src.savings_direction ?? (savings != null ? (savings > 0 ? 'savings' : savings < 0 ? 'loss' : 'breakeven') : 'unavailable')
  const netDir = src.net_savings_direction ?? (netSavings != null ? (netSavings > 0 ? 'savings' : netSavings < 0 ? 'loss' : 'breakeven') : 'unavailable')
  const cacheRate = a.cache_hit_rate || 0
  const escRate = a.escalation_rate || 0
  const quality = { opt: a.avg_quality, base: null, kept: null }

  const dist = Object.entries(
    (stats && stats.by_model) || {},
  ).map(([name, value]) => ({ name, value }))
  const totalReq = dist.reduce((s, d) => s + d.value, 0)
  const modelColor = (index) => ['var(--blue)', 'var(--purple)', 'var(--cyan)', 'var(--orange)'][index % 4]

  const cpHealth = cp && cp.health
  const cpStats = cp && cp.stats
  const cpCls = cpStats && cpStats.by_kind && cpStats.by_kind.classifier
  const cpVer = cpStats && cpStats.by_kind && cpStats.by_kind.verifier
  const cpEval = cpStats && cpStats.by_kind && cpStats.by_kind.evaluator

  return (
    <div className="fade-in">
      <div className="status-strip"><span className="live-dot" />LIVE OPERATIONS <span>·</span> analytics from current request history <span className="live-refresh">auto-refreshing</span></div>
      <div className="kpis kpis-6">
        <KPI label="Cost saved" value={savingsDir === 'loss' ? `−${usd(Math.abs(savings))}` : usd(savings)} sub={`${pct(savingsPct.toFixed ? savingsPct.toFixed(1) : savingsPct)} vs always-best`} tone={savingsDir === 'loss' ? 'down' : 'up'} />
        <KPI label="Net saved (incl. CP)" value={netDir === 'loss' ? `−${usd(Math.abs(netSavings))}` : usd(netSavings)} sub={`CP overhead ${usd(src.control_plane_cost_usd ?? 0)} · ${pct(netSavingsPct.toFixed ? netSavingsPct.toFixed(1) : netSavingsPct)}`} tone={netDir === 'loss' ? 'down' : 'up'} />
        <KPI label="Quality" value={quality.opt != null ? `${(quality.opt * 100).toFixed(1)}%` : '–'} sub={quality.kept != null ? `${(quality.kept * 100).toFixed(1)}% of baseline ${quality.base != null ? (quality.base * 100).toFixed(1) + '%' : ''}` : 'live average'} />
        <KPI label="Cache hit rate" value={`${(cacheRate * 100).toFixed(0)}%`} sub="exact + reusable-context hits" />
        <KPI label="Requests" value={a.total_requests} sub={`LIVE · store mode: ${a.mode}`} />
        <KPI label="Escalations" value={`${(escRate * 100).toFixed(0)}%`} sub="LIVE · quality/provider retries" />
      </div>

      <div className="grid side" style={{ marginTop: 18 }}>
        <div className="stack">
          <Card>
            <CardHead
              title="LIVE cost: optimizer vs baseline"
              sub="Current request history. Baseline is counterfactual; benchmark results are shown in Benchmark Lab."
            />
            <CostCompare baseline={base} optimizer={opt} savings={savings} savingsPct={savingsPct} netSavings={netSavings} netSavingsPct={netSavingsPct} direction={savingsDir} />
          </Card>

          <Card>
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
        </div>

        <div className="stack">
          <Card>
            <CardHead title="How a request flows" sub="The pipeline every request takes." />
            <Flow
              nodes={[
                { title: 'Request', sub: 'prompt + optional reusable context', icon: <Search size={14} /> },
                { title: 'Analyze', sub: 'OpenCode classifier · legacy fallback', icon: <BrainCircuit size={14} /> },
                { title: 'Route', sub: 'cheapest model that clears measured thresholds', icon: <GitBranch size={14} /> },
                { title: 'Generate', sub: 'selected model answers', icon: <Gauge size={14} /> },
                { title: 'Verify', sub: 'quality scored; escalate if below bar', icon: <CheckCircle2 size={14} />, status: 'success' },
                { title: 'Measure', sub: 'cost, baseline and quality recorded', icon: <CircleDollarSign size={14} /> },
              ]}
            />
          </Card>

          <Card>
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

      <div className="grid side" style={{ marginTop: 18 }}>
        <div className="stack">
          <Card>
            <CardHead title="Control plane usage" sub="OpenCode classifier · cache verifier · LLM evaluator (lifetime counters)." />
            {!cp ? (
              <div className="muted">Control plane status unavailable.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                  <Badge tone={cpHealth && cpHealth.available ? 'good' : 'warn'}>
                    {cpHealth && cpHealth.available ? 'opencode reachable' : 'fallback active'}
                  </Badge>
                  <Badge>{cp.model_id}</Badge>
                  <Badge>{cp.classifier_backend}</Badge>
                  <Badge>quality: {cp.quality_check_mode}</Badge>
                  <Badge>verify: {cp.cache_verify_enabled ? 'on' : 'off'}</Badge>
                </div>
                <div className="kpis kpis-3">
                  <KPI label="Classifier calls" value={cpCls ? cpCls.calls : 0} sub={`${cpCls ? cpCls.failures : 0} failures`} />
                  <KPI label="Verifier calls" value={cpVer ? cpVer.calls : 0} sub={`${cpVer ? cpVer.failures : 0} failures`} />
                  <KPI label="Evaluator calls" value={cpEval ? cpEval.calls : 0} sub={`${cpEval ? cpEval.failures : 0} failures`} />
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  Total control-plane tokens: {cpStats ? cpStats.input_tokens + cpStats.output_tokens : 0}
                  {cpStats && cpStats.estimated_usage_calls > 0 && ' (some usage estimated chars/4)'}
                  {cpHealth && cpHealth.last_failure ? ` · last failure: ${cpHealth.last_failure}` : ''}
                </div>
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card>
            <CardHead title="Cost attribution" sub="Total = Control Plane + Task Model (per-request ledger)." />
            <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span>Task model spend (history)</span><span className="num">{usd(opt)}</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span>Control-plane spend</span><span className="num">tracked per request</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', borderTop: '1px solid var(--line)', paddingTop: 6 }}>
                <span><strong>Total</strong></span><span className="num"><strong>{usd(opt)} + CP</strong></span>
              </div>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Per-request control-plane cost is in the ledger: /api/chat returns control_plane_cost_usd and total_cost_incl_cp_usd; benchmark runs report control_plane_cost_usd and net_savings_pct (savings minus control-plane overhead).
            </p>
          </Card>

          <Card>
            <CardHead title="Explainability" sub="Why each decision was made." />
            <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
              <div>• Classification backend: <strong>{cp ? cp.classifier_backend : '–'}</strong> with automatic legacy fallback on any control-plane failure.</div>
              <div>• Cache verifier can only VETO a semantic reuse after all deterministic gates pass — it can never approve a blocked reuse.</div>
              <div>• LLM evaluator only grades subjective tasks (no reference, non-math); math/logic keeps deterministic scoring.</div>
              <div>• Every /api/chat response carries decision_reason, the control-plane ledger, cache_verifier and quality_evaluator views.</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
