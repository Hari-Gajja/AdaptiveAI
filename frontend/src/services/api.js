const j = async (r) => {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export const api = {
  health: () => fetch('/health').then(j),
  analytics: () => fetch('/api/analytics').then(j),
  routingStats: () => fetch('/api/routing-stats').then(j),
  models: () => fetch('/api/models').then(j),
  catalogModels: () => fetch('/api/models/catalog').then(j),
  profiles: () => fetch('/api/models/profiles').then(j),
  controlPlane: () => fetch('/api/models/control-plane').then(j),
  addModel: (body) =>
    fetch('/api/models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j),
  updateModel: (id, body) =>
    fetch(`/api/models/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j),
  startProfiling: (model_ids) =>
    fetch('/api/models/profile', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_ids: model_ids || null }) }).then(j),
  profileJob: (id) => fetch(`/api/models/profile/jobs/${id}`).then(j),
  chat: (body) =>
    fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j),
  preview: (prompt) =>
    fetch('/api/route/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }) }).then(j),
  cacheStats: () => fetch('/api/cache/stats').then(j),
  benchmarkQueries: () => fetch('/api/benchmark/queries').then(j),
  benchmarkRun: (limit, baseline_sample_n, baseline_quality_mode = 'sampled', mode = 'full_optimizer') =>
    fetch('/api/benchmark/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit: limit || 0, baseline_sample_n: baseline_sample_n || 5, baseline_quality_mode, mode }) }).then(j),
  benchmarkJob: (id) => fetch(`/api/benchmark/jobs/${id}`).then(j),
  benchmarkLatest: () => fetch('/api/benchmark/latest').then(j),
  tokenBenchmarkRun: (limit = 10) =>
    fetch('/api/benchmark/token-efficiency', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit }) }).then(j),
  tokenBenchmarkJob: (id) => fetch(`/api/benchmark/token-efficiency/${id}`).then(j),
}

export const usd = (n) => (n == null ? '–' : `$${Number(n).toFixed(n < 0.01 ? 6 : 4)}`)
export const pct = (n) => (n == null ? '–' : `${Number(n)}%`)

// ---------------------------------------------------------------- gateway /v1
// Demo convenience: the gateway key lives in localStorage. Production would
// use a proper session/token flow — this keeps the demo self-contained.
const GW_KEY = 'llmo_gateway_key'
const GW_HISTORY = 'llmo_generated_gateway_keys'
export const gateway = {
  getKey: () => localStorage.getItem(GW_KEY) || '',
  setKey: (k) => localStorage.setItem(GW_KEY, k || ''),
  clearKey: () => localStorage.removeItem(GW_KEY),
  rememberGeneratedKey: (key, name) => {
    const history = gateway.getGeneratedKeys()
    const next = [{ key, name: name || 'API key', created_at: new Date().toISOString() }, ...history.filter((item) => item.key !== key)]
    localStorage.setItem(GW_HISTORY, JSON.stringify(next.slice(0, 20)))
  },
  getGeneratedKeys: () => {
    try { return JSON.parse(localStorage.getItem(GW_HISTORY) || '[]') } catch { return [] }
  },
}

const gwHeaders = () => {
  const k = gateway.getKey()
  return { 'Content-Type': 'application/json', ...(k ? { Authorization: `Bearer ${k}` } : {}) }
}

const gw = (path, opts = {}) =>
  fetch(path, { ...opts, headers: { ...gwHeaders(), ...(opts.headers || {}) } }).then(j)

export const gwApi = {
  createCustomer: (body) =>
    fetch('/v1/customers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j),
  me: () => gw('/v1/customers/me'),
  rotateKey: () => gw('/v1/customers/rotate-key', { method: 'POST' }),
  health: () => gw('/v1/health'),
  models: (enabledOnly = false) => gw(`/v1/models?enabled_only=${enabledOnly}`),
  discoverModels: (body) => gw('/v1/models/discover', { method: 'POST', body: JSON.stringify(body) }),
  registerModel: (body) => gw('/v1/models/register', { method: 'POST', body: JSON.stringify(body) }),
  updateModel: (id, body) => gw(`/v1/models/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteModel: (id) => gw(`/v1/models/${id}`, { method: 'DELETE' }),
  testModel: (id) => gw(`/v1/models/${id}/test`, { method: 'POST' }),
  profileModel: (id) => gw(`/v1/models/${id}/profile`, { method: 'POST' }),
  analytics: () => gw('/v1/analytics'),
  optimize: (body) => gw('/v1/optimize', { method: 'POST', body: JSON.stringify(body) }),
  chat: (body) => gw('/v1/chat/completions', { method: 'POST', body: JSON.stringify(body) }),
}
