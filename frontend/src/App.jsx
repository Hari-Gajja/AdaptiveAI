import { useEffect, useState } from 'react'
import {
  Database,
  FlaskConical,
  LayoutDashboard,
  Menu,
  Play,
  Server,
  X,
  Zap,
} from 'lucide-react'
import './App.css'
import { api } from './services/api'
import Benchmark from './pages/Benchmark'
import CachePage from './pages/CachePage'
import CommandCenter from './pages/CommandCenter'
import Models from './pages/Models'
import Playground from './pages/Playground'

const NAV = [
  ['center', 'Command Center', LayoutDashboard, 'Overview of savings and quality'],
  ['play', 'Playground', Play, 'Live routing decisions, step by step'],
  ['models', 'Models', Server, 'Registry and measured capability profiles'],
  ['cache', 'Cache', Database, 'Exact vs reusable-context hits, honestly labeled'],
  ['bench', 'Benchmark Lab', FlaskConical, 'Optimizer vs always-best, measured'],
]

export default function App() {
  const [tab, setTab] = useState('center')
  const [navOpen, setNavOpen] = useState(false)
  const [health, setHealth] = useState(null)
  const [analytics, setAnalytics] = useState(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      api.health().then((h) => { if (alive) setHealth(h) }).catch(() => { if (alive) setHealth({ status: 'down' }) })
      api.analytics().then((x) => { if (alive) setAnalytics(x) }).catch(() => {})
    }
    load()
    // Live refresh: keep the sidebar store badge and backend status current.
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const active = NAV.find(([k]) => k === tab)
  const Icon = active?.[2]

  return (
    <div className="shell">
      {navOpen && <button className="scrim" aria-label="Close menu" onClick={() => setNavOpen(false)} />}
      <aside className={`sidebar${navOpen ? ' open' : ''}`}>
        <div className="brand">
          <div className="mark"><Zap size={16} strokeWidth={2.4} /></div>
          <div>
            <div className="name">Adaptive</div>
            <div className="sub">AI Cost Optimizer</div>
          </div>
          <button className="btn subtle sm" style={{ marginLeft: 'auto' }} onClick={() => setNavOpen(false)} aria-label="Close navigation">
            <X size={16} />
          </button>
        </div>
        <div className="nav-label">Workspace</div>
        <nav aria-label="Primary">
          {NAV.map(([k, label, NavIcon]) => (
            <div
              key={k}
              role="button"
              tabIndex={0}
              className={`nav-item${tab === k ? ' active' : ''}`}
              onClick={() => { setTab(k); setNavOpen(false) }}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab(k); setNavOpen(false) } }}
            >
              <span className="nav-ico"><NavIcon size={16} strokeWidth={1.9} /></span>
              {label}
            </div>
          ))}
        </nav>
        <div className="foot">
          <div>
            <span className={`dot ${health && health.status === 'ok' ? 'ok' : 'warn'}`} />
            {health == null ? 'Connecting…' : health.status === 'ok' ? 'Backend online' : 'Backend offline'}
          </div>
          <div style={{ marginTop: 5 }}>
            Store: {analytics ? analytics.mode : '—'}
            {health && health.enabled ? ` · ${health.enabled.length} models enabled` : ''}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--faint)' }}>
            Every number on this screen is measured by the live backend.
          </div>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <button className="hamburger" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <Menu size={17} />
          </button>
          <div className="crumb">
            Adaptive / <b>{active?.[1]}</b>
          </div>
          <div className="spacer" />
          {health && health.status === 'ok' && (
            <span className="store-badge">
              <span className="dot ok" />{health.enabled?.length || 0} models enabled · phase {health.phase}
            </span>
          )}
        </div>
        <main className="content">
          <div className="page-head">
            <h1>{active?.[1]}</h1>
            <p>{active?.[3]}</p>
          </div>
          {tab === 'center' && <CommandCenter />}
          {tab === 'play' && <Playground />}
          {tab === 'models' && <Models />}
          {tab === 'cache' && <CachePage />}
          {tab === 'bench' && <Benchmark />}
        </main>
      </div>
    </div>
  )
}
