import { useEffect, useState } from 'react'
import { Badge, Card, CardHead, Empty, Err, KPI, SkeletonKPIs } from '../components/ui'
import { CostCompare } from '../components/flow'
import { api, pct, usd } from '../services/api'

export default function Benchmark() {
  const [info, setInfo] = useState(null)
  const [job, setJob] = useState(null)
  const [latest, setLatest] = useState(null)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('all')

  const loadLatest = () => api.benchmarkLatest().then(setLatest).catch(() => {})
  useEffect(() => {
    api.benchmarkQueries().then(setInfo).catch((e) => setErr(String(e)))
    loadLatest()
  }, [])

  const run = async (limit) => {
    setErr('')
    try {
      const { job_id } = await api.benchmarkRun(limit, 5)
      setJob({ job_id, status: 'running', done: 0, total: limit || (info?.count ?? 50) })
      const poll = setInterval(async () => {
        const j = await api.benchmarkJob(job_id)
        setJob(j)
        if (j.status !== 'running') { clearInterval(poll); loadLatest() }
      }, 4000)
    } catch (e) { setErr(String(e)) }
  }

  const categories = info ? Object.keys(info.categories) : []
  const rows = (latest && latest.per_query) || []
  const shown = filter === 'all' ? rows : rows.filter((q) => q.category === filter)
  const progress = job && job.status === 'running' ? Math.min(1, (job.done || 0) / Math.max(1, job.total || 1)) : null

  return (
    <div className="fade-in">
      <Card>
        <CardHead
          title="Benchmark lab"
          sub="Optimizer vs always-best — measured, not claimed."
          actions={
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost sm" onClick={() => run(10)} disabled={job && job.status === 'running'}>Run 10 (smoke)</button>
              <button className="btn primary" onClick={() => run(0)} disabled={job && job.status === 'running'}>
                {job && job.status === 'running' ? `Running ${job.done}/${job.total}` : 'Run full benchmark'}
              </button>
            </div>
          }
        />
        {info && (
          <div className="row" style={{ gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <Badge>{info.count} queries</Badge>
            {Object.entries(info.categories).map(([k, v]) => <Badge key={k}>{k} {v}</Badge>)}
          </div>
        )}
        {progress != null && (
          <div style={{ marginBottom: 12 }}>
            <div className="bar"><i style={{ width: `${progress * 100}%` }} /></div>
            <div className="muted" style={{ marginTop: 5 }}>job {job.job_id} · {job.done}/{job.total} queries</div>
          </div>
        )}
        <Err>{err}</Err>
        <p className="muted" style={{ margin: 0 }}>
          Methodology: optimizer runs every query live (reference-scored, costs measured). Baseline cost is counterfactual —
          measured tokens × always-best pricing, no duplicate expensive calls. Baseline quality is measured on a deterministic
          n=5 sample. Cache is cold-started so repeated-context items measure warm-up honestly.
        </p>
      </Card>

      {latest ? (
        <div style={{ marginTop: 18 }}>
          <div className="kpis">
            <KPI label="Queries" value={latest.queries_tested} sub="reference-scored" />
            <KPI label="Always-best" value={usd(latest.baseline_cost)} sub={latest.baseline_model} />
            <KPI label="Optimizer" value={usd(latest.optimizer_cost)} sub={`saved ${usd(latest.savings)} (${pct(latest.savings_pct)})`} tone="up" />
            <KPI label="Quality" value={`${(latest.optimizer_quality * 100).toFixed(1)}%`} sub={`baseline ${(latest.baseline_quality * 100).toFixed(1)}% · retained ${latest.quality_retention ? (latest.quality_retention * 100).toFixed(1) + '%' : '–'}`} />
            <KPI label="Cache hits" value={`${(latest.cache_hit_rate * 100).toFixed(0)}%`} sub="exact + context" />
            <KPI label="Escalations" value={`${(latest.escalation_rate * 100).toFixed(0)}%`} sub={`avg ${latest.avg_latency_ms} ms`} />
          </div>

          <div className="grid two" style={{ marginTop: 18 }}>
            <Card>
              <CardHead title="Cost comparison" sub="Counterfactual baseline vs actual spend." />
              <CostCompare baseline={latest.baseline_cost} optimizer={latest.optimizer_cost} savings={latest.savings} savingsPct={latest.savings_pct} />
            </Card>
            <Card>
              <CardHead title="Quality guardrail" sub="Cost savings that survive verification." />
              <div className="row" style={{ gap: 8, marginBottom: 12 }}>
                <Badge tone="good">{(latest.optimizer_quality * 100).toFixed(1)}% optimizer</Badge>
                <Badge>{(latest.baseline_quality * 100).toFixed(1)}% baseline</Badge>
                {latest.quality_retention != null && (
                  <Badge tone={latest.quality_retention >= 0.97 ? 'good' : 'warn'}>{(latest.quality_retention * 100).toFixed(1)}% retained</Badge>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {Object.entries(latest.model_distribution || {}).map(([m, n]) => {
                  const total = Object.values(latest.model_distribution).reduce((s, x) => s + x, 0)
                  return (
                    <div key={m}>
                      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
                        <span style={{ fontSize: 13 }}>{m}</span>
                        <span className="num muted">{total ? Math.round((n / total) * 100) : 0}%</span>
                      </div>
                      <div className="bar"><i style={{ width: `${total ? (n / total) * 100 : 0}%` }} /></div>
                    </div>
                  )
                })}
              </div>
            </Card>
          </div>

          <Card style={{ marginTop: 18 }}>
            <CardHead
              title="Per-query trace"
              sub="Every measured row — model chosen, quality, cost, flags."
              actions={
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  <button className={`btn sm ${filter === 'all' ? 'primary' : 'ghost'}`} onClick={() => setFilter('all')}>All</button>
                  {categories.map((c) => (
                    <button key={c} className={`btn sm ${filter === c ? 'primary' : 'ghost'}`} onClick={() => setFilter(c)}>{c}</button>
                  ))}
                </div>
              }
            />
            <div className="tbl-wrap" style={{ maxHeight: 420, overflow: 'auto' }}>
              <table className="tbl">
                <thead>
                  <tr><th>#</th><th>Category</th><th>Selected → final</th><th>Quality</th><th>Cost</th><th>Flags</th></tr>
                </thead>
                <tbody>
                  {shown.map((q) => (
                    <tr key={q.id}>
                      <td className="num">{q.id}</td>
                      <td>{q.category}</td>
                      <td><span className="muted">{q.selected_model}</span> → <b>{q.final_model}</b></td>
                      <td className="num">{q.quality} <span className="muted" style={{ fontSize: 11 }}>({q.quality_method})</span></td>
                      <td className="num">{usd(q.actual_cost)}</td>
                      <td>
                        {q.escalated ? <Badge tone="warn" style={{ marginRight: 6 }}>escalated</Badge> : null}
                        {q.cache_kind !== 'miss' ? <Badge tone="good">{q.cache_kind}</Badge> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      ) : (
        !job && <div style={{ marginTop: 18 }}><SkeletonKPIs n={6} /><Card style={{ marginTop: 18 }}><Empty title="No benchmark yet">Run the full benchmark to produce measured savings and quality numbers.</Empty></Card></div>
      )}
    </div>
  )
}
