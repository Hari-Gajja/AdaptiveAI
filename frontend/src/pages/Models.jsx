import React, { useEffect, useMemo, useState } from 'react'
import { Badge, Card, CardHead, Err } from '../components/ui'
import { api } from '../services/api'

const CATS = ['reasoning', 'coding', 'math', 'summarization', 'long_context', 'general']

export default function Models() {
  const [models, setModels] = useState([])
  const [profiles, setProfiles] = useState([])
  const [err, setErr] = useState('')
  const [form, setForm] = useState({ model_id: '', input_per_1M: '', output_per_1M: '' })
  const [job, setJob] = useState(null)
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState('model_id')
  const [expanded, setExpanded] = useState(null)

  const load = () => {
    api.models().then((d) => setModels(d.models)).catch((e) => setErr(String(e)))
    api.profiles().then((d) => setProfiles(d.profiles)).catch(() => {})
  }
  useEffect(load, [])

  const profOf = (id) => profiles.find((p) => p.model_id === id)

  const rows = useMemo(() => {
    let r = models.map((m) => ({ ...m, prof: profOf(m.model_id) }))
    if (query.trim()) {
      const q = query.toLowerCase()
      r = r.filter((m) => m.model_id.toLowerCase().includes(q) || (m.endpoint_family || '').toLowerCase().includes(q))
    }
    const key = sortKey
    r = [...r].sort((a, b) => {
      const av = key === 'input' ? a.input_per_1M : key === 'output' ? a.output_per_1M : a.model_id
      const bv = key === 'input' ? b.input_per_1M : key === 'output' ? b.output_per_1M : b.model_id
      return typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv))
    })
    return r
  }, [models, profiles, query, sortKey])

  const add = async () => {
    setErr('')
    try {
      const body = { model_id: form.model_id.trim() }
      if (form.input_per_1M !== '') body.input_per_1M = Number(form.input_per_1M)
      if (form.output_per_1M !== '') body.output_per_1M = Number(form.output_per_1M)
      await api.addModel(body)
      setForm({ model_id: '', input_per_1M: '', output_per_1M: '' })
      load()
    } catch (e) { setErr(String(e)) }
  }

  const toggle = async (m) => {
    setErr('')
    try { await api.updateModel(m.model_id, { enabled: !m.enabled }); load() }
    catch (e) { setErr(String(e)) }
  }

  const profile = async () => {
    setErr('')
    try {
      const { job_id } = await api.startProfiling(null)
      const poll = setInterval(async () => {
        const j = await api.profileJob(job_id)
        setJob(j)
        if (j.status !== 'running') { clearInterval(poll); load() }
      }, 3000)
    } catch (e) { setErr(String(e)) }
  }

  return (
    <div className="fade-in">
      <Card>
        <CardHead
          title="Model registry"
          sub="Measured capability scores override estimated priors."
          actions={
            <div className="row" style={{ gap: 8 }}>
              <input className="input" style={{ width: 220 }} placeholder="Search models…" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button className="btn ghost sm" onClick={profile} disabled={job && job.status === 'running'}>
                {job && job.status === 'running' ? `Profiling ${job.done}/${job.total}` : 'Profile all models'}
              </button>
            </div>
          }
        />
        <Err>{err}</Err>
        {job && job.status !== 'running' && job.results && (
          <p className="muted" style={{ margin: '0 0 10px' }}>
            Last run measured {Object.keys(job.results).length} model(s) on our benchmark.
          </p>
        )}
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th onClick={() => setSortKey('model_id')} style={{ cursor: 'pointer' }}>Model</th>
                <th>Status</th>
                <th onClick={() => setSortKey('input')} style={{ cursor: 'pointer' }}>$ in / 1M</th>
                <th onClick={() => setSortKey('output')} style={{ cursor: 'pointer' }}>$ out / 1M</th>
                {CATS.map((c) => <th key={c} title={c}>{c.slice(0, 4)}</th>)}
                <th></th>
              </tr>
            </thead>
            <tbody>
{rows.map((m) => {
                const p = m.prof
                const isOpen = expanded === m.model_id
                return (
                  <React.Fragment key={m.model_id}>
                    <tr key={m.model_id} style={{ opacity: m.enabled ? 1 : 0.5, cursor: 'pointer' }} onClick={() => setExpanded(isOpen ? null : m.model_id)}>
                          <td>
                            <div style={{ fontWeight: 600 }}>{m.model_id}</div>
                            <div className="muted" style={{ fontSize: 12 }}>{m.endpoint_family} · {(m.context_window / 1000).toFixed(0)}k ctx</div>
                          </td>
                          <td>
                            <Badge tone={p && p.measured ? 'good' : 'warn'}>
                              {p && p.measured ? 'measured' : 'estimated'}
                            </Badge>
                          </td>
                          <td className="num">{m.pricing_status === 'unavailable' ? 'N/A' : `$${m.input_per_1M}`}</td>
                          <td className="num">{m.pricing_status === 'unavailable' ? 'N/A' : `$${m.output_per_1M}`}</td>
                          {CATS.map((c) => (
                            <td key={`${m.model_id}-${c}`} className="num">{p && p.capabilities[c] != null ? p.capabilities[c].toFixed(2) : '–'}</td>
                          ))}
                          <td onClick={(e) => e.stopPropagation()}>
                            <button className="btn subtle sm" onClick={() => toggle(m)}>{m.enabled ? 'Disable' : 'Enable'}</button>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr key={`${m.model_id}-detail`}>
                            <td colSpan={9} style={{ background: 'var(--bg-soft)' }}>
                              <div className="grid two" style={{ gap: 14 }}>
                                <div>
                                  <div className="section-title" style={{ marginBottom: 8 }}>Capability profile</div>
                                  {CATS.map((c) => (
                                    <div key={`${m.model_id}-${c}`} style={{ marginBottom: 6 }}>
                                      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 2 }}>
                                        <span className="muted" style={{ textTransform: 'capitalize' }}>{c.replace('_', ' ')}</span>
                                        <span className="num">{p && p.capabilities[c] != null ? p.capabilities[c].toFixed(2) : '–'}</span>
                                      </div>
                                      <div className="bar"><i style={{ width: `${(p && p.capabilities[c] != null ? p.capabilities[c] : 0) * 100}%` }} /></div>
                                    </div>
                                  ))}
                                </div>
                                <div>
                                  <div className="section-title" style={{ marginBottom: 8 }}>Details</div>
                                  <div className="trace">
                                    <div className="t-step"><span className="t-key">Provider</span><span className="t-val">{m.provider}</span></div>
                                    <div className="t-step"><span className="t-key">Endpoint</span><span className="t-val mono">{m.endpoint_family}</span></div>
                                    <div className="t-step"><span className="t-key">Context</span><span className="t-val num">{m.context_window.toLocaleString()} tokens</span></div>
                                    <div className="t-step"><span className="t-key">Profile</span><span className="t-val">{m.profile_status}{p && p.measured_at ? ` · ${new Date(p.measured_at).toLocaleString()}` : ''}</span></div>
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card style={{ marginTop: 18 }}>
        <CardHead title="Add model" sub="Pricing auto-fills from the known table when left blank." />
        <div className="grid three">
          <input className="input" placeholder="model_id (e.g. kimi-k2.7-code)" value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })} />
          <input className="input" placeholder="$ in / 1M (blank = auto)" value={form.input_per_1M} onChange={(e) => setForm({ ...form, input_per_1M: e.target.value })} />
          <input className="input" placeholder="$ out / 1M (blank = auto)" value={form.output_per_1M} onChange={(e) => setForm({ ...form, output_per_1M: e.target.value })} />
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={add}>Add model</button>
          <span className="muted">API keys never touch the frontend — all LLM calls go through FastAPI.</span>
        </div>
      </Card>
    </div>
  )
}
