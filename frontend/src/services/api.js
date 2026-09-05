const j = async (r) => {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export const api = {
  health: () => fetch('/health').then(j),
  analytics: () => fetch('/api/analytics').then(j),
  routingStats: () => fetch('/api/routing-stats').then(j),
  models: () => fetch('/api/models').then(j),
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
