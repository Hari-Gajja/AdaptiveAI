import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUp,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  Copy,
  Cpu,
  Gauge,
  Loader2,
  RefreshCw,
  Route,
  Sparkles,
  X,
  Zap,
} from 'lucide-react'
import { Err } from '../components/ui'
import { api, usd } from '../services/api'

const SUGGESTIONS = [
  { icon: 'code', title: 'Debug this code', desc: 'Analyze a difficult programming problem' },
  { icon: 'book', title: 'Explain this concept', desc: 'Get a concise knowledge answer' },
  { icon: 'puzzle', title: 'Solve this problem', desc: 'Test reasoning and mathematics' },
  { icon: 'layers', title: 'Analyze this architecture', desc: 'Evaluate a complex technical decision' },
]

const LIFECYCLE = [
  'Analyzing request',
  'Understanding task',
  'Checking model capabilities',
  'Selecting optimal model',
  'Generating response',
  'Verifying quality',
]

const CATS = {
  easy: 'Easy', coding: 'Coding', reasoning: 'Reasoning', math: 'Math',
  summarization: 'Summary', architecture: 'Architecture',
  long_context: 'Long context', repeated_context: 'Repeated context',
}

function SuggestionIcon({ name }) {
  const map = {
    code: <Cpu size={17} />,
    book: <Sparkles size={17} />,
    puzzle: <Gauge size={17} />,
    layers: <Route size={17} />,
  }
  return map[name] || <Sparkles size={17} />
}

/** Auto-growing textarea that expands up to a max height, then scrolls. */
function AutoTextarea({ value, onChange, placeholder, onKeyDown, onFocus, onBlur, maxRows = 8 }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, maxRows * 24)}px`
  }, [value, maxRows])
  return (
    <textarea
      ref={ref}
      className="pg-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      onBlur={onBlur}
      rows={1}
    />
  )
}

/** Compact horizontal cost comparison: optimized vs always-best + saved. */
function CostMini({ res }) {
  const opt = res.actual_cost_usd
  const base = res.baseline_cost_usd
  const saved = res.savings_usd
  const hasCost = res.cost_status === 'measured' && opt != null
  const hasBase = base != null
  const max = Math.max(opt || 0, base || 0, 1e-9)
  return (
    <div className="pg-cost">
      <div className="pg-cost-head">
        <CircleDollarSign size={13} />
        <span>Request cost</span>
      </div>
      <div className="pg-cost-row">
        <span className="pg-cost-label">Optimized</span>
        <div className="pg-cost-track"><i className="pg-cost-fill pg-cost-opt" style={{ width: `${((opt || 0) / max) * 100}%` }} /></div>
        <span className="pg-cost-val num">{hasCost ? usd(opt) : '–'}</span>
      </div>
      <div className="pg-cost-row">
        <span className="pg-cost-label">Always-best</span>
        <div className="pg-cost-track"><i className="pg-cost-fill pg-cost-base" style={{ width: `${((base || 0) / max) * 100}%` }} /></div>
        <span className="pg-cost-val num">{hasBase ? usd(base) : '–'}</span>
      </div>
      {saved != null && saved > 0 && (
        <div className="pg-cost-saved">
          <ArrowDownRight size={13} />
          <span>Saved <b className="num">{usd(saved)}</b></span>
        </div>
      )}
    </div>
  )
}

/** Quality mini indicator: animated bar + verified/escalating state. */
function QualityMini({ res }) {
  const q = res.quality_score
  const status = res.verification_status
  const numeric = Number(q)
  const pct = Number.isFinite(numeric) ? Math.round(numeric * 100) : null
  const verified = status === 'verified' || status === 'escalated_and_verified'
  const escalating = status === 'escalating' || (res.escalated && !verified)
  return (
    <div className="pg-quality">
      <div className="pg-quality-head">
        <Gauge size={13} />
        <span>Quality</span>
        {pct != null && <b className="num">{pct}%</b>}
      </div>
      <div className="pg-quality-track">
        <i
          className={`pg-quality-fill${verified ? ' ok' : escalating ? ' warn' : ''}`}
          style={{ width: `${pct != null ? Math.min(100, Math.max(0, pct)) : 0}%` }}
        />
      </div>
      <div className={`pg-quality-status${verified ? ' ok' : escalating ? ' warn' : ''}`}>
        {verified ? <CheckCircle2 size={12} /> : escalating ? <AlertTriangle size={12} /> : <span className="pg-dot" />}
        {verified ? 'Verified' : escalating ? 'Escalating' : status || '—'}
      </div>
    </div>
  )
}

/** Escalation story: first attempt → escalate → final attempt. */
function EscalationStory({ res }) {
  const attempts = res.attempts || []
  if (attempts.length < 2) return null
  return (
    <div className="pg-esc">
      {attempts.map((a, i) => (
        <div key={i} className="pg-esc-step" style={{ '--d': `${i * 220}ms` }}>
          <div className="pg-esc-model">
            <span className={`pg-esc-dot${a.passed ? ' ok' : ''}`}>{a.passed ? <Check size={11} /> : <X size={11} />}</span>
            <span>{a.model_id}</span>
            <span className="num pg-esc-q">{a.quality != null ? `${Math.round(a.quality * 100)}%` : '–'}</span>
          </div>
          {i < attempts.length - 1 && (
            <div className="pg-esc-arrow">
              <span>Escalating…</span>
              <ChevronDown size={13} />
            </div>
          )}
        </div>
      ))}
      <div className="pg-esc-final">
        <CheckCircle2 size={13} />
        <span>Verified</span>
      </div>
    </div>
  )
}

/** Decision trace drawer — the strongest UI element. */
function DecisionTrace({ res, prompt, onClose }) {
  const steps = []
  steps.push({
    key: 'request',
    title: 'Request',
    body: <div className="pg-trace-req">{prompt || res.prompt || '—'}</div>,
  })
  steps.push({
    key: 'task',
    title: 'Task detected',
    body: (
      <div className="pg-trace-line">
        <b>{CATS[res.task_type] || res.task_type || '—'}</b>
        {res.difficulty_score != null && <span className="num">difficulty {res.difficulty_score}</span>}
      </div>
    ),
  })
  steps.push({
    key: 'cap',
    title: 'Required capability',
    body: (
      <div className="pg-trace-line">
        <span className="num">{res.required_capabilities?.length ? res.required_capabilities.join(', ') : '—'}</span>
      </div>
    ),
  })
  steps.push({
    key: 'models',
    title: 'Available models',
    body: (
      <div className="pg-trace-models">
        {(res.routing?.candidates || []).map((c) => (
          <div key={c.model_id} className={`pg-trace-model${c.model_id === res.routing.selected_model ? ' sel' : ''}`}>
            <span>{c.qualifies ? <Check size={12} /> : <X size={12} />} {c.model_id}</span>
            <span className="num">{c.qualifies ? '✓' : '✗'}</span>
          </div>
        ))}
      </div>
    ),
  })
  steps.push({
    key: 'selected',
    title: 'Selected',
    body: <div className="pg-trace-line"><b>{res.final_model || '—'}</b></div>,
  })
  steps.push({
    key: 'verify',
    title: 'Quality verification',
    body: (
      <div className="pg-trace-line">
        <span className="num">{res.quality_score != null ? `${Math.round(res.quality_score * 100)}%` : '—'}</span>
        {res.quality_passed ? <Check size={12} className="pg-ok" /> : <X size={12} className="pg-bad" />}
      </div>
    ),
  })
  steps.push({
    key: 'final',
    title: 'Final state',
    body: (
      <div className="pg-trace-line">
        <b className="pg-ok">{res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified' ? 'Verified' : res.verification_status || '—'}</b>
      </div>
    ),
  })
  return (
    <div className="pg-drawer" role="dialog" aria-label="Decision trace">
      <div className="pg-drawer-head">
        <div>
          <div className="pg-drawer-title">WHY THIS MODEL?</div>
          <div className="pg-drawer-sub">capability source: {res.capability_source || '—'}</div>
        </div>
        <button className="pg-icon-btn" onClick={onClose} aria-label="Close decision trace"><X size={16} /></button>
      </div>
      <div className="pg-trace">
        {steps.map((s, i) => (
          <div key={s.key} className="pg-trace-step" style={{ '--d': `${i * 180}ms` }}>
            <div className="pg-trace-title">{s.title}</div>
            {s.body}
            {i < steps.length - 1 && <div className="pg-trace-arrow"><ChevronDown size={14} /></div>}
          </div>
        ))}
      </div>
      <div className="pg-drawer-foot">
        <div className="pg-drawer-reasons">
          <div className="pg-drawer-reasons-title">Why this route</div>
          {(res.decision_reason || []).map((r, i) => (
            <div key={i} className="pg-reason-line" style={{ '--d': `${400 + i * 220}ms` }}>· {r}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

/** Optimization metadata card (collapsed by default). */
function OptMeta({ res, onTrace }) {
  const [open, setOpen] = useState(false)
  const q = res.quality_score
  const pct = Number.isFinite(Number(q)) ? Math.round(Number(q) * 100) : null
  const verified = res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified'
  const hasCost = res.cost_status === 'measured' && res.actual_cost_usd != null
  return (
    <div className="pg-meta">
      <button className="pg-meta-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="pg-meta-toggle-label">Optimization details</span>
        <ChevronDown size={15} className={`pg-meta-chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="pg-meta-body">
          <div className="pg-meta-grid">
            <div className="pg-meta-item">
              <span className="pg-meta-k">Model selected</span>
              <span className="pg-meta-v"><b>{res.final_model || '—'}</b></span>
            </div>
            <div className="pg-meta-item">
              <span className="pg-meta-k">Why</span>
              <span className="pg-meta-v">
                {(res.decision_reason || []).slice(0, 3).map((r, i) => (
                  <span key={i} className="pg-meta-why"><Check size={11} className="pg-ok" />{r}</span>
                ))}
              </span>
            </div>
            <div className="pg-meta-item">
              <span className="pg-meta-k">Quality</span>
              <span className="pg-meta-v num">{pct != null ? `${pct}%` : '—'}</span>
            </div>
            <div className="pg-meta-item">
              <span className="pg-meta-k">Cost</span>
              <span className="pg-meta-v num">{hasCost ? usd(res.actual_cost_usd) : 'Cost unavailable'}</span>
            </div>
            <div className="pg-meta-item">
              <span className="pg-meta-k">Status</span>
              <span className={`pg-meta-v${verified ? ' pg-ok' : ''}`}>{verified ? '✓ Verified' : res.verification_status || '—'}</span>
            </div>
          </div>
          <button className="pg-trace-btn" onClick={onTrace}>
            <Route size={14} />
            View decision trace
          </button>
        </div>
      )}
    </div>
  )
}

/** Assistant message: wide readable content + metadata + actions. */
function AssistantMsg({ res, onTrace, onRegen }) {
  const [copied, setCopied] = useState(false)
  const verified = res.verification_status === 'verified' || res.verification_status === 'escalated_and_verified'
  const q = res.quality_score
  const pct = Number.isFinite(Number(q)) ? Math.round(Number(q) * 100) : null
  const hasCost = res.cost_status === 'measured' && res.actual_cost_usd != null
  const cacheHit = res.cache_hit
  const cacheKind = res.cache_kind
  const doCopy = () => {
    if (navigator.clipboard) navigator.clipboard.writeText(res.answer || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }
  return (
    <div className="pg-msg pg-msg-assistant">
      <div className="pg-msg-head">
        <span className="pg-avatar pg-avatar-ai"><Sparkles size={13} /></span>
        <span className="pg-msg-author">AI Optimizer</span>
        <span className="pg-msg-model">{res.final_model || ''}</span>
        <span className="spacer" />
        {cacheHit && (
          <span className="pg-cache-badge">
            <Zap size={11} />
            {cacheKind === 'exact' ? 'Cached response' : cacheKind === 'context' ? 'Context cache hit' : 'Cached'}
          </span>
        )}
        {verified && <span className="pg-verified-badge"><CheckCircle2 size={11} />Verified</span>}
      </div>
      <div className="pg-answer">{res.answer || ''}</div>
      <div className="pg-msg-meta">
        <span className="pg-meta-chip"><span className="pg-chip-dot" />{res.final_model || '—'}</span>
        {pct != null && <span className="pg-meta-chip">Quality {pct}%</span>}
        {hasCost && <span className="pg-meta-chip">{usd(res.actual_cost_usd)}</span>}
        {res.latency_ms != null && <span className="pg-meta-chip">{res.latency_ms} ms</span>}
      </div>
      <div className="pg-msg-actions">
        <button className="pg-action" onClick={doCopy} aria-label="Copy response">
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button className="pg-action" onClick={onRegen} aria-label="Regenerate response">
          <RefreshCw size={13} />
          Regenerate
        </button>
        <button className="pg-action" onClick={onTrace} aria-label="View decision trace">
          <Route size={13} />
          View decision
        </button>
        <button className="pg-action" onClick={() => document.querySelector('.pg-cost')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })} aria-label="View cost">
          <CircleDollarSign size={13} />
          View cost
        </button>
        <button className="pg-action" onClick={() => document.querySelector('.pg-quality')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })} aria-label="View quality">
          <Gauge size={13} />
          View quality
        </button>
      </div>
      <div className="pg-msg-opt">
        <CostMini res={res} />
        <QualityMini res={res} />
        {res.escalated && <EscalationStory res={res} />}
        <OptMeta res={res} onTrace={onTrace} />
      </div>
    </div>
  )
}

/** User message: clean right-aligned block. */
function UserMsg({ text }) {
  return (
    <div className="pg-msg pg-msg-user">
      <div className="pg-msg-head">
        <span className="pg-avatar pg-avatar-user"><span className="pg-user-initial">Y</span></span>
        <span className="pg-msg-author">You</span>
      </div>
      <div className="pg-user-text">{text}</div>
    </div>
  )
}

/** Request lifecycle progress (visual representation, not fake telemetry). */
function Lifecycle({ stage }) {
  return (
    <div className="pg-lifecycle" role="status" aria-live="polite">
      <div className="pg-lifecycle-head">
        <Loader2 size={14} className="pg-spin" />
        <span>{LIFECYCLE[Math.min(stage, LIFECYCLE.length - 1)]}…</span>
      </div>
      <div className="pg-lifecycle-track">
        {LIFECYCLE.map((s, i) => (
          <div key={s} className={`pg-life-step${stage > i ? ' done' : stage === i ? ' active' : ''}`}>
            <span className="pg-life-dot">{stage > i ? <Check size={10} /> : ''}</span>
            <span className="pg-life-label">{s}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** Auto Route popover — shows ENABLED models only. */
function AutoRoutePop({ models, onClose }) {
  return (
    <div className="pg-route-pop" role="dialog" aria-label="Auto routing">
      <div className="pg-route-pop-title">AUTO ROUTING</div>
      <div className="pg-route-pop-desc">
        The optimizer will select the minimum-cost capable model.
      </div>
      <div className="pg-route-pop-models">
        {(models || []).map((m) => (
          <div key={m} className="pg-route-pop-model"><Check size={12} className="pg-ok" />{m}</div>
        ))}
      </div>
      <button className="pg-icon-btn pg-route-pop-close" onClick={onClose} aria-label="Close"><X size={14} /></button>
    </div>
  )
}

/** Model picker — dropdown listing ENABLED models only, refreshed on open. */
function ModelPicker({ enabledModels, value, onChange }) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState([])
  const wrapRef = useRef(null)

  // Re-fetch enabled models every time the dropdown opens, so a model
  // disabled on the Models page disappears from here immediately.
  useEffect(() => {
    if (!open) return undefined
    let live = true
    api.models().then((d) => {
      if (live) setNames(((d && d.models) || []).filter((m) => m.enabled).map((m) => m.model_id))
    }).catch(() => {})
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => { live = false; document.removeEventListener('mousedown', onDocClick) }
  }, [open])

  const current = value || 'Auto Route'
  const stale = value && !names.includes(value)
  return (
    <div className="pg-model-picker" ref={wrapRef}>
      <button
        className={`pg-ctx-btn pg-model-btn${value ? ' active' : ''}`}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={value ? `Force model: ${value}` : 'Model selection: Auto Route'}
      >
        <Cpu size={13} />
        <span className="pg-model-btn-label">{stale ? 'Auto Route' : current}</span>
        <ChevronDown size={13} className={`pg-model-chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="pg-model-menu" role="listbox" aria-label="Select model">
          <div
            role="option"
            aria-selected={!value}
            className={`pg-model-item${!value ? ' sel' : ''}`}
            onClick={() => { onChange(''); setOpen(false) }}
          >
            <Route size={13} className="pg-model-item-ico" />
            <span>Auto Route</span>
            <span className="pg-model-item-sub">cheapest capable</span>
          </div>
          {(names || []).map((m) => (
            <div
              key={m}
              role="option"
              aria-selected={value === m}
              className={`pg-model-item${value === m ? ' sel' : ''}`}
              onClick={() => { onChange(m); setOpen(false) }}
            >
              <Cpu size={13} className="pg-model-item-ico" />
              <span>{m}</span>
              {value === m && <Check size={13} className="pg-ok pg-model-check" />}
            </div>
          ))}
          {names && names.length === 0 && (
            <div className="pg-model-empty">No enabled models — enable one on the Models page.</div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Playground() {
  const [prompt, setPrompt] = useState('')
  const [context, setContext] = useState('')
  const [force, setForce] = useState('')
  const [msgs, setMsgs] = useState([])
  const [loading, setLoading] = useState(false)
  const [stage, setStage] = useState(-1)
  const [err, setErr] = useState('')
  const [traceFor, setTraceFor] = useState(null)
  const [routeOpen, setRouteOpen] = useState(false)
  const [models, setModels] = useState([])
  const [focused, setFocused] = useState(false)
  const [showCtx, setShowCtx] = useState(false)
  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.models().then((m) => setModels((m && m.models) || [])).catch(() => {})
  }, [])

  // Enabled models only — a model disabled on the Models page must never be
  // selectable here. The backend also rejects unknown/disabled force_model.
  const enabledModels = useMemo(
    () => (models || []).filter((m) => m.enabled !== false),
    [models],
  )

  // Smooth-scroll to newest message when it changes (respect user scroll-up).
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [msgs.length])

  const send = async (text) => {
    const body = { prompt: text, max_tokens: 512 }
    if (context.trim()) body.context = context
    if (force) body.force_model = force
    setLoading(true); setErr(''); setStage(0)
    setMsgs((m) => [...m, { role: 'user', text }])
    setPrompt('')
    const timer = setInterval(() => {
      setStage((s) => (s < LIFECYCLE.length - 1 ? s + 1 : s))
    }, 850)
    try {
      const r = await api.chat(body)
      clearInterval(timer)
      setStage(LIFECYCLE.length)
      setMsgs((m) => [...m, { role: 'assistant', res: r }])
    } catch (e) {
      clearInterval(timer)
      setErr(String(e))
      setStage(-1)
    }
    setLoading(false)
  }

  const onSubmit = () => {
    const text = prompt.trim()
    if (!text || loading) return
    send(text)
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  const onSuggestion = (title) => {
    setPrompt(title)
    inputRef.current?.focus()
  }

  const onRegen = async (idx) => {
    const userMsg = msgs[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return
    setLoading(true); setErr(''); setStage(0)
    setMsgs((m) => m.slice(0, idx))
    const timer = setInterval(() => {
      setStage((s) => (s < LIFECYCLE.length - 1 ? s + 1 : s))
    }, 850)
    try {
      const r = await api.chat({ prompt: userMsg.text, max_tokens: 512 })
      clearInterval(timer)
      setStage(LIFECYCLE.length)
      setMsgs((m) => [...m, { role: 'assistant', res: r }])
    } catch (e) {
      clearInterval(timer)
      setErr(String(e))
      setStage(-1)
    }
    setLoading(false)
  }

  const hasConv = msgs.length > 0
  const enabledNames = enabledModels.map((m) => m.model_id || m.id || m.name).filter(Boolean)
  // If the forced model was disabled on the Models page, drop the selection.
  useEffect(() => {
    if (force && enabledNames.length && !enabledNames.includes(force)) setForce('')
  }, [force, enabledNames])

  return (
    <div className="pg">
      <div className="pg-scroll" ref={scrollRef}>
        <div className="pg-inner">
          {!hasConv && !loading ? (
            <div className="pg-hero">
              <div className="pg-hero-badge"><Sparkles size={13} />Adaptive Multi-LLM Cost Optimizer</div>
              <h2 className="pg-hero-title">Ask your AI system anything</h2>
              <p className="pg-hero-sub">
                We'll analyze the request, select the most cost-efficient capable model, verify the response, and measure the result.
              </p>
              <div className="pg-suggestions">
                {SUGGESTIONS.map((s, i) => (
                  <button key={s.title} className="pg-suggestion" style={{ '--d': `${80 + i * 80}ms` }} onClick={() => onSuggestion(s.title)}>
                    <span className="pg-sugg-icon"><SuggestionIcon name={s.icon} /></span>
                    <span className="pg-sugg-text">
                      <span className="pg-sugg-title">{s.title}</span>
                      <span className="pg-sugg-desc">{s.desc}</span>
                    </span>
                    <ChevronRight size={14} className="pg-sugg-arrow" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="pg-conv">
              {msgs.map((m, i) => (
                m.role === 'user'
                  ? <UserMsg key={i} text={m.text} />
                  : <AssistantMsg key={i} res={m.res} onTrace={() => setTraceFor({ res: m.res, prompt: msgs[i - 1]?.text || '' })} onRegen={() => onRegen(i)} />
              ))}
              {loading && <Lifecycle stage={stage} />}
              {err && <Err>{err}</Err>}
            </div>
          )}
        </div>
      </div>

      <div className="pg-composer-wrap">
        <div className={`pg-composer${focused ? ' focused' : ''}`}>
          <AutoTextarea
            value={prompt}
            onChange={setPrompt}
            placeholder="Ask anything…"
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          <div className="pg-composer-bar">
            <div className="pg-composer-left">
              <ModelPicker enabledModels={enabledNames} value={force} onChange={setForce} />
              <button className={`pg-ctx-btn${showCtx ? ' active' : ''}`} onClick={() => setShowCtx(!showCtx)} aria-label="Toggle context" title="Add reusable context">
                <span className="pg-plus">+</span>
                <span>Context</span>
              </button>
              <button className="pg-route-btn" onClick={() => setRouteOpen(!routeOpen)} aria-label="Auto route">
                <Route size={13} />
                <span>Auto Route</span>
                <span className="pg-route-count">· {enabledNames.length} models</span>
              </button>
              {routeOpen && <AutoRoutePop models={enabledNames} onClose={() => setRouteOpen(false)} />}
            </div>
            <div className="pg-composer-right">
              <span className="pg-kbd-hint"><kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>
              <button className={`pg-send${loading ? ' loading' : ''}${prompt.trim() ? ' active' : ''}`} onClick={onSubmit} disabled={loading} aria-label="Send">
                {loading ? <Loader2 size={16} className="pg-spin" /> : <ArrowUp size={16} />}
              </button>
            </div>
          </div>
          {showCtx && (
            <div className="pg-ctx-panel">
              <textarea
                className="pg-ctx-input"
                placeholder="Reusable context (docs/policy) — same context + new question = cache hit"
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={2}
              />
            </div>
          )}
        </div>
      </div>

      {traceFor?.res && <DecisionTrace res={traceFor.res} prompt={traceFor.prompt} onClose={() => setTraceFor(null)} />}
    </div>
  )
}
