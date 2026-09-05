import { useEffect, useState } from 'react'
import { Badge, Card, CardHead, Err, KPI, SkeletonKPIs } from '../components/ui'
import { Flow } from '../components/flow'
import { api, usd } from '../services/api'

export default function CachePage() {
  const [stats, setStats] = useState(null)
  const [err, setErr] = useState('')

  const load = () => api.cacheStats().then(setStats).catch((e) => setErr(String(e)))
  useEffect(() => {
    load()
    // Live refresh: cache stats change as Playground traffic flows in.
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const clear = async () => { await api.cacheStats(); try { await fetch('/api/cache/clear', { method: 'POST' }); load() } catch (e) { setErr(String(e)) } }

  if (err) return <Err>{err}</Err>
  if (!stats) return <SkeletonKPIs n={4} />

  return (
    <div className="fade-in">
      <div className="kpis">
        <KPI label="Cache hit rate" value={`${(stats.hit_rate * 100).toFixed(0)}%`} sub={`${stats.exact_hits + stats.semantic_hits + stats.context_hits} hits of ${stats.requests}`} />
        <KPI label="Tokens avoided" value={stats.tokens_avoided.toLocaleString()} sub="input + output" />
        <KPI label="Exact hits" value={stats.exact_hits} sub={`measured savings ${usd(stats.exact_saved_measured_usd)}`} />
        <KPI label="Semantic hits" value={stats.semantic_hits} sub={`gate-safe reuse · ${usd(stats.semantic_saved_measured_usd)}`} />
        <KPI label="Context hits" value={stats.context_hits} sub={`estimated savings ${usd(stats.context_saved_estimated_usd)}`} />
        <KPI label="Entries" value={stats.entries} sub={`${stats.backend || 'memory'} backend · ${stats.semantic_vetoes || 0} gate vetoes`} />
      </div>

      <Card className="cache-hero" style={{ marginTop: 18 }}>
        <div className="cache-meter" style={{ '--cache-rate': `${Math.max(0, Math.min(1, stats.hit_rate || 0)) * 100}%` }}>
          <div><strong>{(stats.hit_rate * 100).toFixed(0)}%</strong><span>hit rate</span></div>
        </div>
        <div>
          <div className="section-title">Cache performance</div>
          <h2 className="cache-title">Reuse context. Skip repeat work.</h2>
          <p className="muted">Exact hits skip generation entirely. Context hits keep the question fresh while measuring avoided reusable tokens as estimated savings.</p>
          <div className="row"><Badge tone="good">{stats.exact_hits} exact hits</Badge><Badge tone="purple">{stats.semantic_hits} semantic hits</Badge><Badge tone="blue">{stats.context_hits} context hits</Badge><Badge>{stats.misses} misses</Badge></div>
        </div>
      </Card>

      <div className="grid side" style={{ marginTop: 18 }}>
        <Card>
          <CardHead
            title="How the cache decides"
            sub="Exact hits skip the LLM entirely; context hits still generate but count avoided reusable tokens."
            actions={<button className="btn ghost sm" onClick={clear}>Clear cache</button>}
          />
          <Flow
            nodes={[
              { title: 'Request', sub: 'prompt + optional reusable context' },
              { title: 'Cache lookup', sub: 'normalized SHA-256 keys' },
              {
                title: 'Hit',
                sub: `exact: return stored answer · measured ${usd(stats.exact_saved_measured_usd)}`,
                body: <Badge tone="good">no LLM call</Badge>,
                status: 'cached',
              },
              {
                title: 'Context hit',
                sub: `same reusable context, new question · estimated ${usd(stats.context_saved_estimated_usd)}`,
                body: <Badge tone="warn">generate, count avoided tokens</Badge>,
                status: 'warning',
              },
              { title: 'Miss', sub: 'route → generate → verify → store', branch: true },
            ]}
          />
        </Card>

        <div>
          <Card>
            <CardHead title="Savings, honestly labeled" sub="Measured and estimated savings are never mixed." />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="muted">Measured (exact hits)</span>
                <span className="num" style={{ fontWeight: 600 }}>{usd(stats.exact_saved_measured_usd)}</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="muted">Estimated (context hits)</span>
                <span className="num" style={{ fontWeight: 600 }}>{usd(stats.context_saved_estimated_usd)}</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', borderTop: '1px solid var(--line-soft)', paddingTop: 10 }}>
                <span style={{ fontWeight: 600 }}>Total saved</span>
                <span className="num" style={{ fontWeight: 700 }}>{usd(stats.total_saved_usd)}</span>
              </div>
            </div>
          </Card>
          <Card style={{ marginTop: 18 }}>
            <CardHead title="Why two kinds" sub="Exact hits skip the model entirely, so the saved call is real. Context hits avoid re-sending reusable text, which is a cost estimate — the UI labels each kind so nothing is overstated." />
          </Card>
        </div>
      </div>
    </div>
  )
}
