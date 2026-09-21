import React, { useEffect, useState } from 'react'
import { Badge, Card, CardHead, Empty, Err, KPI, Skeleton } from '../components/ui'
import { api, gateway, gwApi, usd } from '../services/api'

/**
 * Gateway — Phase G11. The multi-tenant surface:
 *  - connect YOUR models (base_url + key + model_id)
 *  - the system auto-profiles capability (never manual tiers)
 *  - pricing is honest: "unknown" is shown as unknown, never fabricated
 *  - hybrid routing provenance (Nemotron recommended vs deterministic)
 */
export default function Gateway({ onGoKeys }) {
  const [key, setKey] = useState(gateway.getKey())
  const [connected, setConnected] = useState(false)
  const [me, setMe] = useState(null)
  const [models, setModels] = useState([])
  const [analytics, setAnalytics] = useState(null)
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [form, setForm] = useState({ provider: 'openai_compatible', model_id: '', base_url: '', api_key: '', input_per_1M: '', output_per_1M: '' })
  const [busy, setBusy] = useState('')
  const [catalog, setCatalog] = useState([])
  const [providerModels, setProviderModels] = useState([])
  const [discovering, setDiscovering] = useState(false)
  const [serviceReady, setServiceReady] = useState(false)

  const load = () => {
    setErr('')
    gwApi.me().then((d) => { setMe(d.customer); setConnected(true); setLoaded(true) })
      .catch((e) => { setErr(String(e)); setConnected(false); setLoaded(true) })
    gwApi.models().then((d) => setModels(d.models)).catch(() => {})
    gwApi.analytics().then((d) => setAnalytics(d.analytics)).catch(() => {})
    api.catalogModels().then((d) => setCatalog(d.models || [])).catch(() => {})
  }
  useEffect(() => { if (gateway.getKey()) load() }, [])

  const connect = () => {
    const k = key.trim()
    if (k.startsWith('oc_sk_') || k.startsWith('sk-')) {
      setErr('401 OpenCode provider key supplied where a gateway key is required — paste a gw_… key from API Keys → Create API key. Provider keys go in the model form below, never here.')
      return
    }
    gateway.setKey(k)
    load()
  }

  const register = async () => {
    setErr('')
    try {
      if (!form.model_id.trim()) throw new Error('Enter a model ID.')
      if (!form.api_key.trim()) throw new Error('Paste the selected provider API key.')
      const body = { provider: form.provider, model_id: form.model_id.trim(), base_url: form.base_url.trim(), api_key: form.api_key.trim() }
      if (form.input_per_1M !== '') body.input_per_1M = Number(form.input_per_1M)
      if (form.output_per_1M !== '') body.output_per_1M = Number(form.output_per_1M)
      await gwApi.registerModel(body)
      setForm({ provider: 'openai_compatible', model_id: '', base_url: '', api_key: '', input_per_1M: '', output_per_1M: '' })
      load()
    } catch (e) { setErr(String(e)) }
  }

  const discoverProviderModels = async () => {
    setErr(''); setDiscovering(true)
    try {
      if (!form.api_key.trim()) throw new Error('Paste the provider API key first.')
      if (!form.base_url.trim()) throw new Error('Enter the provider base URL first.')
      const result = await gwApi.discoverModels({
        provider: form.provider,
        base_url: form.base_url,
        api_key: form.api_key,
      })
      setProviderModels(result.models || [])
      setForm((current) => ({ ...current, model_id: '' }))
    } catch (e) { setErr(String(e)) } finally { setDiscovering(false) }
  }

  const registerAllProviderModels = async () => {
    setErr(''); setBusy('all')
    try {
      if (!providerModels.length) throw new Error('Discover provider models first.')
      if (!form.api_key.trim()) throw new Error('Paste the provider API key first.')
      const results = []
      for (const model of providerModels) {
        try {
          await gwApi.registerModel({
            provider: form.provider,
            model_id: model.id,
            base_url: form.base_url.trim(),
            api_key: form.api_key.trim(),
            input_per_1M: model.input_per_1M,
            output_per_1M: model.output_per_1M,
          })
          results.push({ status: 'fulfilled' })
        } catch (reason) {
          results.push({ status: 'rejected', reason })
        }
      }
      const failed = results.filter((result) => result.status === 'rejected')
      if (failed.length === results.length) throw failed[0].reason
      if (failed.length) setErr(`${results.length - failed.length} models connected; ${failed.length} were already registered or rejected.`)
      setServiceReady(true)
      load()
    } catch (e) { setErr(String(e)) } finally { setBusy('') }
  }

  const selectCatalogModel = (modelId) => {
    const model = catalog.find((item) => item.id === modelId)
    if (!model) return
    setForm((current) => ({
      ...current,
      model_id: model.id,
      provider: 'opencode',
      base_url: 'https://opencode.ai/zen/go/v1',
      input_per_1M: model.input_per_1M ?? '',
      output_per_1M: model.output_per_1M ?? '',
    }))
  }

  const act = async (id, fn) => {
    setBusy(id); setErr('')
    try { await fn(id); load() } catch (e) { setErr(String(e)) } finally { setBusy('') }
  }

  const a = analytics || {}
  const priced = models.filter((m) => m.pricing_status === 'configured').length

  return (
    <div className="fade-in">
      {!connected && (
        <Card>
          <CardHead title="Connect to the gateway" sub="Paste your gateway API key — it authenticates every /v1 call." />
          <Err>{err}</Err>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <input className="input" style={{ width: 340 }} placeholder="gw_… gateway API key"
              value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn primary" onClick={connect}>Connect</button>
          </div>
          <p className="muted" style={{ margin: '12px 0 0' }}>
            No key yet? <button className="btn subtle sm" style={{ padding: '2px 6px' }} onClick={onGoKeys}>Create one in API Keys →</button>
          </p>
        </Card>
      )}
      {connected && (
        <>
          <Card>
            <CardHead title="Adaptive service API" sub="Keys live in the API Keys page — generate and manage them there." actions={onGoKeys && <button className="btn primary sm" onClick={onGoKeys}>Open API Keys →</button>} />
          </Card>
          <div className="kpis">
            <KPI label="Requests" value={a.total_requests ?? 0} sub="through your pool" />
            <KPI label="Gross savings" value={usd(a.savings_usd ?? 0)} sub={`vs ${a.savings_direction ?? '—'}`} tone={a.savings_direction === 'savings' ? 'up' : a.savings_direction === 'loss' ? 'down' : undefined} />
            <KPI label="Control-plane cost" value={usd(a.control_plane_cost_usd ?? 0)} sub="Nemotron routing overhead" />
            <KPI label="Net savings" value={usd(a.net_savings_usd ?? 0)} sub={a.net_savings_direction ?? '—'} tone={a.net_savings_direction === 'savings' ? 'up' : a.net_savings_direction === 'loss' ? 'down' : undefined} />
          </div>

          <Card>
            <CardHead title="My models" sub={`${models.length} registered · ${priced} priced · ${models.length - priced} pricing unknown (never fabricated)`}
              actions={<button className="btn subtle sm" onClick={load}>Refresh</button>} />
            <Err>{err}</Err>
            {loaded && models.length === 0 ? (
              <Empty title="No models connected yet">Register your first model below — base_url + API key + model id.</Empty>
            ) : !loaded ? (
              <div style={{ padding: '8px 0' }}>{[0, 1, 2].map((i) => <Skeleton key={i} h={34} style={{ marginBottom: 8 }} />)}</div>
            ) : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>Model</th><th>Status</th><th>Pricing</th><th>$ in / 1M</th><th>$ out / 1M</th><th>Context</th><th>Profile</th><th></th></tr>
                  </thead>
                  <tbody>
                    {models.map((m) => (
                      <tr key={m.model_id} className={m.enabled ? '' : 'dimmed'}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{m.model_id}</div>
                          <div className="muted" style={{ fontSize: 12 }}>{m.provider}{m.base_url ? ` · ${m.base_url.replace(/^https?:\/\//, '').slice(0, 28)}` : ''}</div>
                        </td>
                        <td>{m.enabled ? <Badge tone="good">enabled</Badge> : <Badge>disabled</Badge>}</td>
                        <td>
                          {m.pricing_status === 'configured' ? <Badge tone="good">configured</Badge>
                            : m.pricing_status === 'unknown' ? <Badge tone="warn" title="No price declared and none found in the catalog — excluded from cost-optimal routing until priced">unknown</Badge>
                              : <Badge tone="warn">unavailable</Badge>}
                          {m.pricing_source && m.pricing_source !== 'none' && <div className="muted" style={{ fontSize: 11 }}>{m.pricing_source}</div>}
                        </td>
                        <td className="num">{m.priced ? `$${m.input_per_1M}` : '—'}</td>
                        <td className="num">{m.priced ? `$${m.output_per_1M}` : '—'}</td>
                        <td className="num">{(m.context_window / 1000).toFixed(0)}k</td>
                        <td>{m.profile_status === 'measured' ? <Badge tone="good">measured</Badge> : <Badge tone="warn">unprofiled</Badge>}</td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <div className="row" style={{ gap: 6 }}>
                            <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.testModel(id))}>Test</button>
                            <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.profileModel(id))}>Profile</button>
                            <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.updateModel(id, { enabled: !m.enabled }))}>{m.enabled ? 'Disable' : 'Enable'}</button>
                            <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.deleteModel(id))}>Delete</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card style={{ marginTop: 18 }}>
            <CardHead title="Connect all provider models" sub="Paste one provider key, discover its catalog, and add the entire model pool to Adaptive routing. The key is encrypted and never returned." />
            <div className="grid three">
              <select className="input" value={form.provider} onChange={(e) => {
                const provider = e.target.value
                const base_url = provider === 'openai' ? 'https://api.openai.com/v1' : provider === 'opencode' ? 'https://opencode.ai/zen/go/v1' : form.base_url
                setForm({ ...form, provider, base_url })
              }}>
                <option value="openai_compatible">OpenAI-compatible</option>
                <option value="openai">OpenAI</option>
                <option value="opencode">OpenCode Go</option>
                <option value="custom">Custom provider</option>
              </select>
              <input className="input" placeholder="base_url (https://api.example.com/v1)" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
              <input className="input" type="password" placeholder="Provider API key (required)" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
              <div className="row">
                <button className="btn ghost" type="button" disabled={discovering} onClick={discoverProviderModels}>{discovering ? 'Discovering…' : 'Discover all models'}</button>
                <button className="btn primary" type="button" disabled={!providerModels.length || busy === 'all'} onClick={registerAllProviderModels}>Connect all {providerModels.length || ''} models</button>
              </div>
            </div>
            {providerModels.length > 0 && <p className="muted" style={{ margin: '10px 0 0' }}>{providerModels.length} models discovered. Adaptive will route requests across all connected models.</p>}
          </Card>

          <Card style={{ marginTop: 18 }}>
            <CardHead title="Routing analytics" sub="Customer-scoped — you never see another tenant's numbers." />
            <div className="trace">
              <div className="t-step"><span className="t-key">Cache hit rate</span><span className="t-val num">{a.cache_hit_rate != null ? `${(a.cache_hit_rate * 100).toFixed(1)}%` : '—'}</span></div>
              <div className="t-step"><span className="t-key">Escalation rate</span><span className="t-val num">{a.escalation_rate != null ? `${(a.escalation_rate * 100).toFixed(1)}%` : '—'}</span></div>
              <div className="t-step"><span className="t-key">Avg quality</span><span className="t-val num">{a.avg_quality != null ? a.avg_quality.toFixed(2) : '—'}</span></div>
              <div className="t-step"><span className="t-key">Baseline cost</span><span className="t-val num">{usd(a.baseline_cost_usd ?? 0)}</span></div>
              <div className="t-step"><span className="t-key">Actual cost</span><span className="t-val num">{usd(a.total_cost_usd ?? 0)}</span></div>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
