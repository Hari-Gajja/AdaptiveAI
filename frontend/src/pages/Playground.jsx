import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Cpu, Zap } from 'lucide-react'
import { Badge, Card, CardHead, Err, QualityGauge } from '../components/ui'
import { Flow, FlowLink } from '../components/flow'
import { api, usd } from '../services/api'

const DEMOS = [
  { label: 'Simple → cheap', prompt: 'What is an API?' },
  { label: 'Coding → capable', prompt: 'Debug this Python concurrency issue: threads increment a shared counter without a lock and the final count is too low. Explain the cause and fix.' },
  { label: 'Hard → strong', prompt: 'Design a fault-tolerant distributed banking ledger in four sentences: name the consistency model, replication strategy, failure handling, and one tradeoff.' },
]

const STAGES = [
  'Analyzing request',
  'Filtering candidate models',
  'Selecting cheapest capable model',
  'Generating answer',
  'Verifying quality',
]

export default function Playground() {
  const [prompt, setPrompt] = useState(DEMOS[0].prompt)
  const [context, setContext] = useState('')
  const [force, setForce] = useState('')
  const [res, setRes] = useState(null)
  const [loading, setLoading] = useState(false)
  const [stage, setStage] = useState(-1)
  const [err, setErr] = useState('')

  const send = async () => {
    setLoading(true); setErr(''); setRes(null)
    setStage(0)
    const timer = setInterval(() => {
      setStage((s) => (s < STAGES.length - 1 ? s + 1 : s))
    }, 900)
    try {
      const body = { prompt, max_tokens: 512 }
      if (context.trim()) body.context = context
      if (force) body.force_model = force
      const r = await api.chat(body)
      clearInterval(timer)
      setStage(STAGES.length)
      setRes(r)
    } catch (e) {
      clearInterval(timer)
      setErr(String(e))
      setStage(-1)
    }
    setLoading(false)
  }

  return (
    <div className="grid side fade-in">
      <div>
        <Card>
          <CardHead title="Test request" sub="Send a prompt through the full optimizer pipeline." />
          <div className="row" style={{ marginBottom: 12 }}>
            {DEMOS.map((d) => (
              <button key={d.label} className="btn ghost sm" onClick={() => setPrompt(d.prompt)}>{d.label}</button>
            ))}
          </div>
          <div className="field">
            <label htmlFor="prompt">Prompt</label>
            <textarea id="prompt" className="textarea" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ctx">Reusable context <span className="muted" style={{ fontWeight: 400 }}>(optional — same context + new question = cache hit)</span></label>
            <textarea id="ctx" className="textarea" style={{ minHeight: 60 }} value={context} onChange={(e) => setContext(e.target.value)} />
          </div>
          <div className="row">
            <input
              className="input"
              style={{ maxWidth: 260 }}
              placeholder="force first model (demo)"
              value={force}
              onChange={(e) => setForce(e.target.value)}
            />
            <button className="btn primary" onClick={send} disabled={loading}>
              {loading ? 'Optimizing…' : 'Optimize request'}
            </button>
          </div>
          <Err>{err}</Err>
        </Card>

        {loading && stage >= 0 && (
          <Card style={{ marginTop: 18 }}>
            <div className="stages">
              {STAGES.map((s, i) => (
                <div key={s} className={`stage${stage > i ? ' done' : stage === i ? ' active' : ''}`}>
                  <span className="s-dot">{stage > i ? '✓' : ''}</span>
                  {s}{stage === i ? '…' : ''}
                </div>
              ))}
            </div>
          </Card>
        )}

        {res && (
          <Card style={{ marginTop: 18 }} className="fade-in">
            <CardHead
              title={`Answer · ${res.final_model}`}
              sub={`${res.verification_status} · quality ${res.quality_score ?? 'N/A'} (${res.quality_method}/${res.quality_detail || 'unspecified'}) · cost ${res.cost_status === 'measured' ? usd(res.actual_cost_usd) : 'N/A'} · baseline ${usd(res.baseline_cost_usd)} · saved ${res.savings_status === 'measured' ? `${res.savings_pct}%` : 'N/A'} · ${res.latency_ms} ms`}
              actions={
                <div className="row" style={{ gap: 6 }}>
                  {res.verification_status !== 'verified' && res.verification_status !== 'escalated_and_verified' && <Badge tone="warn">{res.verification_status}</Badge>}
                  {res.escalated && <Badge tone="warn">escalated</Badge>}
                  {res.cache_hit && <Badge tone="good">{res.cache_kind} hit</Badge>}
                </div>
              }
            />
            <pre className="answer">{res.answer}</pre>
          </Card>
        )}
      </div>

      <div>
        {res ? (
          <Card className="trace fade-in">
            <CardHead title="Decision trace" sub={`capability source: ${res.capability_source}`} />
            {res.escalated && (
              <div className="escalation-banner">
                <AlertTriangle size={14} />
                <span>Escalation story: the first model fell below the quality bar, so the optimizer re-routed to a stronger capable model and re-verified.</span>
              </div>
            )}
            {res.cache_hit && (
              <div className="cache-banner">
                <Zap size={14} />
                <span>{res.cache_kind === 'exact' ? 'Cache hit: answer returned from the store — no LLM call, cost fully avoided.' : 'Context cache hit: reusable context restored, only the new question generated.'}</span>
              </div>
            )}
            <div className="trace-seq">
              {([
                {
                  key: 'analyze',
                  node: {
                    title: 'Analyze',
                    sub: `${res.analysis.task_type} · difficulty ${res.analysis.difficulty_score} · confidence ${res.analysis.confidence}`,
                    icon: <Cpu size={14} />,
                  },
                },
                {
                  key: 'route',
                  node: {
                    title: 'Route',
                    sub: `${res.routing.selected_model} selected`,
                    icon: <Zap size={14} />,
                    body: (
                      <div style={{ marginTop: 10, textAlign: 'left' }}>
                        {(res.routing.candidates || []).map((c, ci) => (
                          <div
                            key={c.model_id}
                            className={`cand-row${c.model_id === res.routing.selected_model ? ' cand-sel' : ''}`}
                            style={{ '--d': `${300 + ci * 110}ms` }}
                          >
                            <span>{c.qualifies ? '✓' : '✗'} {c.model_id}{c.model_id === res.routing.selected_model ? ' ← selected' : ''}</span>
                            <span className="num">{usd(c.expected_cost_usd)}</span>
                          </div>
                        ))}
                      </div>
                    ),
                  },
                },
                {
                  key: 'verify',
                  node: {
                    title: 'Verify',
                    sub: res.attempts?.length
                      ? res.attempts.map((a) => `${a.model_id} ${a.quality} ${a.passed ? '✓' : '✗'}`).join(' → ')
                      : 'served from cache',
                    status: res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified' ? 'success' : res.verification_status === 'cached' ? 'cached' : 'warning',
                    icon: res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified' ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />,
                    body: res.attempts?.length
                      ? (
                        <div style={{ marginTop: 8 }}>
                          <div className="trace-quality"><QualityGauge value={res.quality_score} threshold={res.quality_threshold} method={res.quality_method} /><span>{res.verification_status}</span></div>
                          <div className="bar">
                            <i style={{ width: `${Math.min(100, (res.quality_score || 0) * 100)}%`, background: (res.quality_score || 0) >= res.quality_threshold ? 'var(--good)' : 'var(--warn)' }} />
                          </div>
                          <div className="muted" style={{ marginTop: 4 }}>threshold {res.quality_threshold} · {res.quality_detail || res.quality_method}</div>
                        </div>
                      )
                      : null,
                  },
                },
                {
                  key: 'respond',
                  node: {
                    title: 'Respond',
                    sub: `${usd(res.actual_cost_usd)} actual · ${res.savings_pct}% below always-best${res.escalated ? ' · escalated' : ''}`,
                    status: res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified' ? 'success' : 'warning',
                  },
                },
              ].map(({ key, node }, i) => (
                <div key={key} className="trace-step" style={{ '--d': `${i * 190}ms` }}>
                  <Flow nodes={[node]} />
                  {i < 3 && <FlowLink />}
                </div>
              )))}
            </div>
            <div style={{ marginTop: 16, borderTop: '1px solid var(--line-soft)', paddingTop: 12 }}>
              <div className="section-title" style={{ marginBottom: 8 }}>Why this route</div>
              {(res.decision_reason || []).map((r, i) => (
                <div key={i} className="reason-line" style={{ '--d': `${400 + i * 220}ms` }}>· {r}</div>
              ))}
            </div>
          </Card>
        ) : (
          !loading && (
            <Card>
              <div className="empty">
                <div className="e-title">No request yet</div>
                <div>Send a request to see analyze → route → generate → verify, with the model-by-model comparison.</div>
              </div>
            </Card>
          )
        )}
      </div>
    </div>
  )
}
