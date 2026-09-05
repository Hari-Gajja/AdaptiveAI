import { useEffect, useState } from 'react'
import { Badge, Card, CardHead, Empty, Err, KPI, SkeletonKPIs } from '../components/ui'
import { CostCompare } from '../components/flow'
import { api, pct, usd } from '../services/api'

/** Phased reveal: each phase mounts after the previous one, gated on real data. */
function usePhased(count, step = 260, enabled = true) {
  const [phase, setPhase] = useState(enabled ? 0 : count)
  useEffect(() => {
    if (!enabled) { setPhase(count); return undefined }
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) { setPhase(count); return undefined }
    setPhase(0)
    const timers = []
    for (let i = 1; i <= count; i++) timers.push(setTimeout(() => setPhase(i), i * step))
    return () => timers.forEach(clearTimeout)
  }, [count, step, enabled])
  return phase
}

export default function Benchmark() {
  const [info, setInfo] = useState(null)
  const [job, setJob] = useState(null)
  const [latest, setLatest] = useState(null)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('all')
  const [revealKey, setRevealKey] = useState(0)
  const [mode, setMode] = useState('full_optimizer')
  const [tokJob, setTokJob] = useState(null)
  const [tokRes, setTokRes] = useState(null)

  const loadLatest = (bump = true) => api.benchmarkLatest().then((d) => {
    setLatest((prev) => {
      // Only re-reveal when a genuinely new run arrived (different finished_at).
      if (bump && prev && prev.finished_at !== d.finished_at) setRevealKey((k) => k + 1)
      return d
    })
  }).catch(() => {})
  useEffect(() => {
    api.benchmarkQueries().then(setInfo).catch((e) => setErr(String(e)))
    loadLatest()
    // Live refresh: pick up benchmark runs finished elsewhere (e.g. another tab).
    const t = setInterval(() => loadLatest(false), 8000)
    return () => clearInterval(t)
  }, [])

  const run = async (limit, baselineQualityMode = 'sampled') => {
    setErr('')
    try {
      const { job_id } = await api.benchmarkRun(limit, 5, baselineQualityMode, mode)
      setJob({ job_id, status: 'running', done: 0, total: limit || (info?.count ?? 50) })
      const poll = setInterval(async () => {
        const j = await api.benchmarkJob(job_id)
        setJob(j)
        if (j.status !== 'running') { clearInterval(poll); loadLatest() }
      }, 4000)
    } catch (e) { setErr(String(e)) }
  }

  const runToken = async (limit = 10) => {
    setErr('')
    try {
      const { job_id } = await api.tokenBenchmarkRun(limit)
      setTokJob({ job_id, status: 'running', done: 0, total: limit || (info?.count ?? 50) })
      const poll = setInterval(async () => {
        const j = await api.tokenBenchmarkJob(job_id)
        setTokJob(j)
        if (j.status !== 'running') { clearInterval(poll); if (j.status === 'done') setTokRes(j.result) }
      }, 4000)
    } catch (e) { setErr(String(e)) }
  }

  const categories = info ? Object.keys(info.categories) : []
  const rows = (latest && latest.per_query) || []
  const shown = filter === 'all' ? rows : rows.filter((q) => q.category === filter)
  const progress = job && job.status === 'running' ? Math.min(1, (job.done || 0) / Math.max(1, job.total || 1)) : null
  const hasLatest = !!latest
  const phase = usePhased(4, 300, hasLatest)

  return (
    <div className="fade-in">
      <Card>
        <CardHead
          title="Benchmark lab"
          sub="Optimizer vs always-best — measured, not claimed."
          actions={
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                            <select className="input sm" value={mode} onChange={(e) => setMode(e.target.value)} style={{ maxWidth: 220 }} disabled={job && job.status === 'running'}>
                <option value="always_frontier">A/B: Always frontier</option>
                <option value="legacy_classifier">A/B: Legacy classifier</option>
                <option value="opencode_classifier">A/B: OpenCode classifier</option>
                <option value="exact_cache">A/B: OpenCode + exact cache</option>
                <option value="full_optimizer">A/B: Full optimizer</option>
                <option value="full_plus_llm_eval">A/B: Full + LLM eval</option>
                <option value="full_plus_token_opt">A/B: Full + token opt (1 attempt)</option>
                <option value="full_plus_token_opt_escalation">A/B: Full + token opt + escalation</option>
              </select>
              <button className="btn ghost sm" onClick={() => run(10)} disabled={job && job.status === 'running'}>Run 10 (smoke)</button>
              <button className="btn primary" onClick={() => run(0, 'sampled')} disabled={job && job.status === 'running'}>
                {job && job.status === 'running' ? `Running ${job.done}/${job.total}` : 'Run full benchmark'}
              </button>
              <button className="btn ghost sm" onClick={() => run(0, 'full')} disabled={job && job.status === 'running'}>
                Full baseline quality
              </button>
              <button className="btn ghost sm" onClick={() => runToken(10)} disabled={tokJob && tokJob.status === 'running'}>
                {tokJob && tokJob.status === 'running' ? `Tokens ${tokJob.done}/${tokJob.total}` : 'Token efficiency (10)'}
              </button>
            </div>
          }
        />
        <div className={`benchmark-status ${job?.status === 'running' ? 'running' : job?.status === 'error' ? 'failed' : latest ? 'completed' : 'idle'}`}>
          <span className="status-led" />
          {job?.status === 'running' ? 'RUNNING' : job?.status === 'error' ? 'FAILED' : latest ? 'COMPLETED' : 'READY'}
          {latest?.finished_at && <span className="muted">· {new Date(latest.finished_at).toLocaleString()}</span>}
        </div>
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
          n=5 sample by default; Full baseline quality explicitly runs the baseline model for every query. Cache is cold-started
          so repeated-context items measure warm-up honestly.
        </p>
      </Card>

      {latest ? (
        <div style={{ marginTop: 18 }} key={revealKey}>
          <div className={`phase phase-1${phase >= 1 ? ' in' : ''}`}>
          <div className="kpis">
            <KPI label="Queries" value={latest.queries_tested} sub={`mode: ${latest.mode || 'full_optimizer'}`} />
            <KPI label="Always-best" value={usd(latest.baseline_cost)} sub={`${latest.baseline_model} · ${latest.baseline_quality_mode || 'sampled'} quality`} />
            <KPI label="Optimizer" value={usd(latest.optimizer_cost)} sub={`saved ${usd(latest.savings)} (${pct(latest.savings_pct)})`} tone="up" />
            <KPI label="Net saved (incl. CP)" value={usd(latest.net_savings ?? latest.savings)} sub={`CP overhead ${usd(latest.control_plane_cost_usd ?? 0)} · ${pct(latest.net_savings_pct ?? latest.savings_pct)}`} tone="up" />
            <KPI label="Quality" value={`${(latest.optimizer_quality * 100).toFixed(1)}%`} sub={`baseline ${latest.baseline_quality != null ? (latest.baseline_quality * 100).toFixed(1) + '%' : 'N/A'} · retained ${latest.quality_retention != null ? (latest.quality_retention * 100).toFixed(1) + '%' : 'N/A'}`} />
            <KPI label="Cache hits" value={`${(latest.cache_hit_rate * 100).toFixed(0)}%`} sub="exact + context" />
            <KPI label="Escalations" value={`${(latest.escalation_rate * 100).toFixed(0)}%`} sub={`${latest.escalation_count ?? Math.round(latest.escalation_rate * latest.queries_tested)} · avg ${latest.avg_latency_ms} ms`} />
            <KPI label="Cost / correct answer" value={latest.cost_per_correct_answer != null ? usd(latest.cost_per_correct_answer) : 'N/A'} sub={`${latest.correct_answers ?? 0}/${latest.queries_tested} passed quality`} />
          </div>
          </div>

          <div className={`phase phase-2${phase >= 2 ? ' in' : ''}`}>
          <Card style={{ marginTop: 18 }}>
            <CardHead title="Validation metrics" sub="All values come from the stored benchmark run; unavailable values remain explicit." />
            <div className="kpis">
              <KPI label="Successful" value={latest.successful_requests ?? latest.queries_tested} sub={`${latest.failed_requests ?? 0} failed`} />
              <KPI label="Exact cache" value={latest.exact_cache_hits ?? 0} sub={`${latest.measured_tokens_avoided ?? 0} measured tokens avoided`} />
              <KPI label="Semantic cache" value={latest.semantic_cache_hits ?? 0} sub="gate-safe similar prompts" />
              <KPI label="Context cache" value={latest.context_cache_hits ?? 0} sub={`${latest.estimated_tokens_avoided ?? 0} estimated tokens avoided`} />
              <KPI label="Median latency" value={latest.median_latency_ms != null ? `${latest.median_latency_ms} ms` : 'N/A'} sub="successful requests" />
            </div>
          </Card>
          <Card style={{ marginTop: 18 }}>
            <CardHead title="Control plane accounting" sub="Total = Control Plane + Task Model. Overhead is subtracted from savings (net_savings)." />
            <div className="kpis">
              <KPI label="CP calls" value={latest.control_plane_calls ?? 0} sub={`${latest.control_plane_fallbacks ?? 0} fallbacks to legacy`} />
              <KPI label="CP tokens" value={latest.control_plane_tokens ?? 0} sub="classifier + verifier + evaluator" />
              <KPI label="CP cost" value={usd(latest.control_plane_cost_usd ?? 0)} sub="priced with CP model rates" />
              <KPI label="CP latency" value={latest.control_plane_latency_ms != null ? `${latest.control_plane_latency_ms} ms` : 'N/A'} sub="summed across calls" />
            </div>
          </Card>
          <Card style={{ marginTop: 18 }}>
            <CardHead title="Token optimization" sub="Normalization, output budgets, and classifier calls avoided by cache-first routing." />
            <div className="kpis">
              <KPI label="Input tokens saved (norm)" value={latest.token_aggregate?.totals?.input_tokens_saved_estimate ?? 0} sub="prompt normalization estimate" />
              <KPI label="Classifier calls avoided" value={`${(latest.classifier_calls_avoided_exact ?? 0) + (latest.classifier_calls_avoided_semantic ?? 0)}`} sub={`exact ${latest.classifier_calls_avoided_exact ?? 0} · semantic ${latest.classifier_calls_avoided_semantic ?? 0}`} />
              <KPI label="Routing accuracy" value={latest.routing_accuracy != null ? pct(latest.routing_accuracy * 100) : 'N/A'} sub="routed model passed quality" />
              <KPI label="Frontier usage" value={latest.unnecessary_frontier_usage != null ? pct(latest.unnecessary_frontier_usage * 100) : 'N/A'} sub="final model = always-best baseline" />
              <KPI label="Cheap failures" value={latest.cheap_failure_rate != null ? pct(latest.cheap_failure_rate * 100) : 'N/A'} sub="first attempt failed → escalated" />
              <KPI label="Classification accuracy" value={latest.classification_accuracy != null ? pct(latest.classification_accuracy * 100) : 'N/A'} sub="analyzer task_type vs query category" />
            </div>
            {latest.classification_confusion && Object.keys(latest.classification_confusion).length > 0 && (
              <div className="row" style={{ gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
                {Object.entries(latest.classification_confusion).map(([k, v]) => (
                  <Badge key={k} tone="warn">{k}: {v}</Badge>
                ))}
              </div>
            )}
          </Card>
          </div>

          <div className={`phase phase-3${phase >= 3 ? ' in' : ''}`}>
          <div className="grid two" style={{ marginTop: 18 }}>
            <Card>
              <CardHead title="Cost comparison" sub="Counterfactual baseline vs actual spend." />
              <CostCompare baseline={latest.baseline_cost} optimizer={latest.optimizer_cost} savings={latest.savings} savingsPct={latest.savings_pct} />
            </Card>
            <Card>
              <CardHead title="Quality guardrail" sub="Cost savings that survive verification." />
              <div className="row" style={{ gap: 8, marginBottom: 12 }}>
                <Badge tone="good">{(latest.optimizer_quality * 100).toFixed(1)}% optimizer</Badge>
                <Badge>{latest.baseline_quality != null ? `${(latest.baseline_quality * 100).toFixed(1)}% baseline` : 'N/A baseline quality'}</Badge>
                {latest.quality_retention != null && (
                  <Badge tone={latest.quality_retention >= 0.97 ? 'good' : 'warn'}>{(latest.quality_retention * 100).toFixed(1)}% retained</Badge>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {Object.entries(latest.model_distribution || {}).map(([m, n], di) => {
                  const total = Object.values(latest.model_distribution).reduce((s, x) => s + x, 0)
                  return (
                    <div key={m} className="distribution-row" style={{ '--d': `${di * 90}ms` }}>
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
          </div>
          {tokRes && (
            <Card style={{ marginTop: 18 }}>
              <CardHead title="Token efficiency: naive vs optimized" sub={`Same queries, raw prompt + fixed ${tokRes.baseline_output_budget} budget vs templates + predicted budget. Measured, no estimates.`} />
              <div className="kpis">
                <KPI label="Output tokens saved" value={tokRes.output_tokens_saved} sub={`${tokRes.output_tokens_saved_pct}% vs naive`} tone={tokRes.output_tokens_saved > 0 ? 'up' : 'down'} />
                <KPI label="Cost saved" value={usd(tokRes.cost_saved_usd)} sub={`${tokRes.cost_saved_pct}% vs naive`} tone={tokRes.cost_saved_usd > 0 ? 'up' : 'down'} />
                <KPI label="Quality delta" value={tokRes.quality_delta != null ? (tokRes.quality_delta >= 0 ? `+${tokRes.quality_delta}` : tokRes.quality_delta) : 'N/A'} sub="optimized − naive" />
                <KPI label="Naive avg out" value={tokRes.naive.avg_output_tokens} sub={`optimized avg ${tokRes.optimized.avg_output_tokens}`} />
              </div>
              <div className="tbl-wrap" style={{ maxHeight: 260, overflow: 'auto', marginTop: 10 }}>
                <table className="tbl">
                  <thead>
                    <tr><th>#</th><th>Category</th><th>Naive in/out</th><th>Opt in/out</th><th>Opt budget</th><th>Norm saved</th></tr>
                  </thead>
                  <tbody>
                    {tokRes.optimized.rows.map((r, i) => {
                      const nrow = tokRes.naive.rows[i]
                      return (
                        <tr key={r.id || i}>
                          <td className="num">{r.id}</td>
                          <td>{r.category}</td>
                          <td className="num">{nrow?.input_tokens ?? '–'} / {nrow?.output_tokens ?? '–'}</td>
                          <td className="num">{r.input_tokens ?? '–'} / {r.output_tokens ?? '–'}</td>
                          <td className="num">{r.estimated_output_tokens ?? '–'}</td>
                          <td className="num">{r.normalization_tokens_saved ?? 0}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <div className={`phase phase-4${phase >= 4 ? ' in' : ''}`}>
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
                  <tr><th>#</th><th>Category</th><th>Selected → final</th><th>Quality</th><th>Cost</th><th>Tokens</th><th>Flags</th></tr>
                </thead>
                <tbody>
                  {shown.map((q, qi) => (
                    <tr key={q.id} className="trace-row" style={{ '--d': `${Math.min(qi * 35, 700)}ms` }}>
                      <td className="num">{q.id}</td>
                      <td>{q.category}</td>
                      <td>{q.status === 'failed' ? <Badge tone="bad">failed</Badge> : <><span className="muted">{q.selected_model}</span> → <b>{q.final_model}</b></>}</td>
                      <td className="num">{q.quality ?? 'N/A'} {q.quality_method && <span className="muted" style={{ fontSize: 11 }}>({q.quality_method})</span>}</td>
                      <td className="num">{q.actual_cost != null ? usd(q.actual_cost) : 'N/A'}</td>
                      <td className="num">{q.input_tokens != null ? `${q.input_tokens} in / ${q.output_tokens} out` : 'N/A'}</td>
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
        </div>
      ) : (
        !job && <div style={{ marginTop: 18 }}><SkeletonKPIs n={6} /><Card style={{ marginTop: 18 }}><Empty title="No benchmark yet">Run the full benchmark to produce measured savings and quality numbers.</Empty></Card></div>
      )}
    </div>
  )
}
