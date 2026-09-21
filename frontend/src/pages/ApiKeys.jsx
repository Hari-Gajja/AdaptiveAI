import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Eye, EyeOff, KeyRound, Plus, Search, Trash2 } from 'lucide-react'
import { Badge, Card, CardHead, Empty, Err } from '../components/ui'
import { gateway, gwApi } from '../services/api'

const HISTORY_KEY = 'llmo_generated_gateway_keys'
const mask = (k) => (k && k.length > 10 ? `${k.slice(0, 4)}••••••••${k.slice(-4)}` : '••••••••')

const persist = (keys) => localStorage.setItem(HISTORY_KEY, JSON.stringify(keys.slice(0, 20)))

export default function ApiKeys({ onGoGateway }) {
  const [keys, setKeys] = useState([])
  const [active, setActive] = useState(gateway.getKey())
  const [modelsCount, setModelsCount] = useState(null)
  const [q, setQ] = useState('')
  const [shown, setShown] = useState({})
  const [copied, setCopied] = useState('')
  const [err, setErr] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState('')

  const load = () => {
    // Self-heal: a provider key (oc_sk_/sk-) saved as gateway key can never
    // authenticate — drop it so Create/Connect work again.
    const stored = gateway.getKey()
    if (stored.startsWith('oc_sk_') || stored.startsWith('sk-')) gateway.setKey('')
    setKeys(gateway.getGeneratedKeys()); setActive(gateway.getKey())
    if (gateway.getKey()) gwApi.models().then((d) => setModelsCount(d.models?.length ?? 0)).catch(() => setModelsCount(null))
    else setModelsCount(null)
  }
  useEffect(load, [])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return keys
    return keys.filter((item) => (item.name || '').toLowerCase().includes(needle) || item.key.toLowerCase().includes(needle))
  }, [keys, q])

  const copy = async (key) => {
    try { await navigator.clipboard.writeText(key); setCopied(key); setTimeout(() => setCopied(''), 1500) }
    catch (e) { setErr(String(e)) }
  }

  const friendlyErr = (e) => {
    const msg = String(e && e.message ? e.message : e)
    if (msg.includes('gw_')) return `${msg} — paste a gw_… gateway key, not an oc_sk_/sk- provider key. Create a fresh key below.`
    return msg
  }

  const create = async () => {
    setErr(''); setCreating(true)
    try {
      // ponytail: always provision a fresh customer — never auto-rotate here.
      // Rotate invalidates the active key; Create must work even when the
      // stored key is stale or a provider key (oc_sk_/sk-) was pasted.
      const stored = gateway.getKey()
      if (stored.startsWith('oc_sk_') || stored.startsWith('sk-')) gateway.setKey('')
      const keyName = name.trim() || 'api-key'
      const d = await gwApi.createCustomer({ customer_id: `key-${Date.now().toString(36)}`, name: keyName })
      gateway.setKey(d.api_key)
      gateway.rememberGeneratedKey(d.api_key, keyName)
      setName(''); setShowCreate(false); setJustCreated(d.api_key); load()
    } catch (e) { setErr(friendlyErr(e)) } finally { setCreating(false) }
  }

  const remove = (key) => {
    persist(gateway.getGeneratedKeys().filter((item) => item.key !== key))
    load()
  }

  const useKey = (key) => { gateway.setKey(key); setActive(key) }

  return (
    <div className="fade-in">
      <Card>
        <CardHead
          title="API keys"
          sub="Gateway keys generated in this browser. AI Studio-style: masked by default, copy or revoke anytime."
          actions={<button className="btn primary sm" onClick={() => setShowCreate(true)}><Plus size={14} /> Create API key</button>}
        />
        <Err>{err}</Err>
        {active && modelsCount === 0 && (
          <div className="err-box" style={{ marginBottom: 12 }}>
            Connected, but no models loaded yet — keys have nothing to route to.{' '}
            {onGoGateway && <button className="btn subtle sm" style={{ padding: '2px 6px' }} onClick={onGoGateway}>Load models in Gateway →</button>}
          </div>
        )}
        {modelsCount > 0 && (
          <p className="muted" style={{ margin: '0 0 12px' }}>{modelsCount} model{modelsCount === 1 ? '' : 's'} connected in Gateway — new keys route across them.</p>
        )}
        <div className="row" style={{ marginBottom: 12 }}>
          <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
            <Search size={15} style={{ position: 'absolute', left: 11, top: 11, color: 'var(--faint)' }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Search API keys" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <span className="muted">{filtered.length} key{filtered.length === 1 ? '' : 's'}</span>
        </div>

        {!keys.length ? (
          <Empty title="No API keys yet">Create your first key — it works immediately as a Bearer token for {typeof window !== 'undefined' ? `${window.location.origin}/v1` : '/v1'}.</Empty>
        ) : !filtered.length ? (
          <Empty title="No matches">No keys match “{q}”.</Empty>
        ) : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Key</th><th>Created</th><th>Status</th><th style={{ textAlign: 'right' }}>Actions</th></tr></thead>
              <tbody>
                {filtered.map((item) => {
                  const visible = !!shown[item.key]
                  const isActive = item.key === active
                  return (
                    <tr key={item.key}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <KeyRound size={14} style={{ color: 'var(--faint)', flex: 'none' }} />
                          <div>
                            <div style={{ fontWeight: 600, fontSize: 13.5 }}>{item.name || 'API key'}</div>
                            <div className="mono" style={{ color: 'var(--text-2)' }}>{visible ? item.key : mask(item.key)}</div>
                          </div>
                        </div>
                      </td>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}>{item.created_at ? new Date(item.created_at).toLocaleString() : '—'}</td>
                      <td>{isActive ? <Badge tone="good">in use</Badge> : <Badge>saved</Badge>}</td>
                      <td>
                        <div className="row" style={{ gap: 4, justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
                          <button className="btn subtle sm" title={visible ? 'Hide' : 'Show'} onClick={() => setShown((s) => ({ ...s, [item.key]: !s[item.key] }))}>{visible ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                          <button className="btn subtle sm" title="Copy" onClick={() => copy(item.key)}>{copied === item.key ? <Check size={14} /> : <Copy size={14} />}</button>
                          {!isActive && <button className="btn subtle sm" onClick={() => useKey(item.key)}>Use</button>}
                          <button className="btn subtle sm" title="Delete from this browser" onClick={() => remove(item.key)}><Trash2 size={14} /></button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted" style={{ margin: '10px 0 0' }}>Keys are stored only in this browser (localStorage). Creating a key here connects it to the models you loaded in Gateway.</p>
      </Card>

      {showCreate && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(10,12,16,.35)', display: 'grid', placeItems: 'center', padding: 16 }} onClick={() => !creating && setShowCreate(false)}>
          <div className="card" style={{ width: 420, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <div className="section-title">Create API key</div>
            <p className="muted" style={{ margin: '4px 0 12px' }}>A new gateway key is issued immediately{active ? ' (rotates the current one)' : ''}.</p>
            <div className="field">
              <label>Key name (optional)</label>
              <input className="input" placeholder="e.g. demo-app" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn ghost sm" disabled={creating} onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="btn primary sm" disabled={creating} onClick={create}>{creating ? 'Creating…' : 'Create'}</button>
            </div>
          </div>
        </div>
      )}

      {justCreated && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(10,12,16,.35)', display: 'grid', placeItems: 'center', padding: 16 }} onClick={() => setJustCreated('')}>
          <div className="card" style={{ width: 520, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <div className="section-title">Copy your new API key</div>
            <p className="muted" style={{ margin: '4px 0 12px' }}>Shown once — store it in your secret manager.</p>
            <div className="row" style={{ background: 'var(--bg-soft)', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px' }}>
              <span className="mono" style={{ flex: 1, overflowWrap: 'anywhere' }}>{justCreated}</span>
              <button className="btn ghost sm" onClick={() => copy(justCreated)}>{copied === justCreated ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}</button>
            </div>
            <div className="mono" style={{ marginTop: 12, background: 'var(--bg-soft)', border: '1px solid var(--line-soft)', borderRadius: 10, padding: '10px 12px', overflowX: 'auto', whiteSpace: 'pre' }}>curl {typeof window !== 'undefined' ? window.location.origin : ''}/v1/chat/completions -H "Authorization: Bearer {justCreated.slice(0, 8)}…"</div>
            <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn primary sm" onClick={() => setJustCreated('')}>Done</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
