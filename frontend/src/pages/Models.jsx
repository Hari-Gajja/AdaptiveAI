import React, { useEffect, useState } from 'react'
import { Badge, Card, CardHead, Empty, Err, Skeleton } from '../components/ui'
import { api, gateway, gwApi } from '../services/api'

export default function Models() {
  const [models, setModels] = useState([])
  const [err, setErr] = useState('')
  const [key, setKey] = useState(gateway.getKey())
  const [connected, setConnected] = useState(Boolean(gateway.getKey()))
  const [busy, setBusy] = useState('')
  const [customerScoped, setCustomerScoped] = useState(Boolean(gateway.getKey()))
  const [selected, setSelected] = useState([])
  const [query, setQuery] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = () => {
    setErr('')
    setLoaded(false)
    const hasGatewayKey = Boolean(gateway.getKey())
    const registryRequest = hasGatewayKey ? gwApi.models() : api.models()
    setCustomerScoped(hasGatewayKey)
    Promise.all([
      registryRequest.catch(() => ({ models: [] })),
      api.catalogModels().catch(() => ({ models: [] })),
    ]).then(([registryData, catalogData]) => {
      const registry = registryData.models || []
      const registryById = new Map(registry.map((m) => [m.model_id, m]))
      const catalog = (catalogData.models || []).map((item) => {
        const id = item.id
        return {
          model_id: id,
          display_name: item.name || id,
          provider: 'opencode',
          enabled: false,
          pricing_status: 'unknown',
          priced: false,
          context_window: item.context_length || 200000,
          profile_status: 'unprofiled',
          ...item,
          ...(registryById.get(id) || {}),
          catalog_only: !registryById.has(id),
        }
      })
      const catalogIds = new Set(catalog.map((m) => m.model_id))
      const localOnly = registry.filter((m) => !catalogIds.has(m.model_id))
      setModels([...catalog, ...localOnly])
      setSelected([])
      setConnected(true)
    })
      .catch((e) => {
        setModels([]); setConnected(false); setErr(String(e))
      })
      .finally(() => setLoaded(true))
  }
  useEffect(load, [])

  const connect = () => {
    const k = key.trim()
    if (k.startsWith('oc_sk_') || k.startsWith('sk-')) {
      setErr('401 OpenCode provider key supplied where a gateway key is required — paste a gw_… key from API Keys → Create API key.')
      return
    }
    gateway.setKey(k)
    setConnected(Boolean(k))
    if (k) load()
  }

  const act = async (id, fn) => {
    setBusy(id); setErr('')
    try { await fn(id); load() } catch (e) { setErr(String(e)) } finally { setBusy('') }
  }

  const toggleSelected = (id) => {
    setSelected((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id])
  }
  const enableSelected = async () => {
    setErr('')
    try {
      const selectedModels = models.filter((m) => selected.includes(m.model_id))
      if (customerScoped && selectedModels.some((m) => m.catalog_only)) {
        throw new Error('Catalog models need a customer endpoint and API key before they can be enabled.')
      }
      await Promise.all(selectedModels.map((model) => {
        if (customerScoped) return gwApi.updateModel(model.model_id, { enabled: true })
        if (model.catalog_only) return api.addModel({ model_id: model.model_id, enabled: true })
        return api.updateModel(model.model_id, { enabled: true })
      }))
      setSelected([])
      load()
    } catch (e) { setErr(String(e)) }
  }

  const rows = models.filter((m) => {
    const q = query.trim().toLowerCase()
    return !q || m.model_id.toLowerCase().includes(q) || (m.provider || '').toLowerCase().includes(q)
  })
  const allVisibleSelected = rows.length > 0 && rows.every((m) => selected.includes(m.model_id))
  const toggleAllVisible = () => {
    setSelected((current) => allVisibleSelected
      ? current.filter((id) => !rows.some((m) => m.model_id === id))
      : [...new Set([...current, ...rows.map((m) => m.model_id)])])
  }

  return (
    <div className="fade-in">
      {!connected && (
        <Card>
          <CardHead title="Connect your model registry" sub="Enter the gateway API key to load your customer-scoped models." />
          <Err>{err}</Err>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <input className="input" style={{ width: 340 }} placeholder="gw_… gateway API key" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn primary" onClick={connect}>Load models</button>
          </div>
        </Card>
      )}

      {connected && (
      <Card>
        <CardHead
          title="My model registry"
          sub="Live models loaded from the gateway API key."
          actions={
            <div className="row" style={{ gap: 8 }}>
              <input className="input" style={{ width: 220 }} placeholder="Search models…" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button className="btn primary sm" disabled={!selected.length || busy} onClick={enableSelected}>Enable selected ({selected.length})</button>
              <button className="btn ghost sm" onClick={load}>Refresh</button>
            </div>
          }
        />
        <Err>{err}</Err>
        {!loaded ? (
          <div style={{ padding: '8px 0' }}>{[0, 1, 2].map((i) => <Skeleton key={i} h={34} style={{ marginBottom: 8 }} />)}</div>
        ) : rows.length === 0 ? (
          <Empty title="No matching models">Register models from the Gateway tab.</Empty>
        ) : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th><input
                  type="checkbox"
                  aria-label="Select all visible models"
                  checked={allVisibleSelected}
                  onChange={toggleAllVisible}
                /></th><th>Model</th><th>Status</th><th>Pricing</th><th>$ in / 1M</th><th>$ out / 1M</th><th>Context</th><th>Profile</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                    <tr key={m.model_id} className={m.enabled ? '' : 'dimmed'}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${m.model_id}`}
                          checked={selected.includes(m.model_id)}
                          onChange={() => toggleSelected(m.model_id)}
                        />
                      </td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{m.model_id}</div>
                        <div className="muted" style={{ fontSize: 12 }}>{m.provider}{m.base_url ? ` · ${m.base_url.replace(/^https?:\/\//, '').slice(0, 28)}` : ''}</div>
                      </td>
                      <td>{m.catalog_only ? <Badge>available</Badge> : m.enabled ? <Badge tone="good">enabled</Badge> : <Badge>disabled</Badge>}</td>
                      <td>{m.pricing_status === 'configured' ? <Badge tone="good">configured</Badge> : <Badge tone="warn">{m.pricing_status}</Badge>}</td>
                      <td className="num">{m.priced ? `$${m.input_per_1M}` : '—'}</td>
                      <td className="num">{m.priced ? `$${m.output_per_1M}` : '—'}</td>
                      <td className="num">{(m.context_window / 1000).toFixed(0)}k</td>
                      <td>{m.profile_status === 'profiled' ? <Badge tone="good">profiled</Badge> : <Badge tone="warn">{m.profile_status}</Badge>}</td>
                      <td onClick={(e) => e.stopPropagation()}><div className="row" style={{ gap: 6 }}>
                        {customerScoped && !m.catalog_only && <>
                          <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.testModel(id))}>Test</button>
                          <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.profileModel(id))}>Profile</button>
                          <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.updateModel(id, { enabled: !m.enabled }))}>{m.enabled ? 'Disable' : 'Enable'}</button>
                          <button className="btn subtle sm" disabled={busy === m.model_id} onClick={() => act(m.model_id, (id) => gwApi.deleteModel(id))}>Delete</button>
                        </>}
                      </div>
                      </td>
                    </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </Card>
      )}
    </div>
  )
}
