export function Badge({ tone, children, title }) {
  return (
    <span className={`badge${tone ? ` ${tone}` : ''}`} title={title}>
      <span className="b-dot" />
      {children}
    </span>
  )
}

export function Card({ className = '', children, ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}>
      {children}
    </div>
  )
}

export function CardHead({ title, sub, actions }) {
  return (
    <div className="card-head">
      <div>
        <div className="section-title">{title}</div>
        {sub && <div className="section-sub" style={{ margin: '4px 0 0' }}>{sub}</div>}
      </div>
      <div className="spacer" />
      {actions}
    </div>
  )
}

export function KPI({ label, value, sub, tone }) {
  return (
    <div className="card kpi fade-in">
      <div className="k-label">{label}</div>
      <div className="k-value num">{value}</div>
      {sub && (
        <div className="k-sub">
          {tone === 'up' && <span className="up">▲</span>}
          {tone === 'down' && <span className="down">▼</span>}
          {sub}
        </div>
      )}
    </div>
  )
}

export function Bar({ value, good, label, right }) {
  return (
    <div className="bar-row">
      {label && <span className="muted" style={{ minWidth: 120 }}>{label}</span>}
      <div className={`bar${good ? ' good' : ''}`} role="progressbar" aria-valuenow={Math.round((value || 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${Math.min(100, Math.max(0, (value || 0) * 100))}%` }} />
      </div>
      <span className="val">{right != null ? right : `${Math.round((value || 0) * 100)}%`}</span>
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <div className="e-title">{title}</div>
      <div>{children}</div>
    </div>
  )
}

export function Skeleton({ h = 14, w = '100%', style }) {
  return <div className="skel" style={{ height: h, width: w, ...style }} />
}

export function SkeletonKPIs({ n = 4 }) {
  return (
    <div className="kpis">
      {Array.from({ length: n }).map((_, i) => (
        <div className="card kpi" key={i}>
          <Skeleton h={11} w="55%" />
          <Skeleton h={28} w="70%" style={{ marginTop: 10 }} />
          <Skeleton h={11} w="85%" style={{ marginTop: 9 }} />
        </div>
      ))}
    </div>
  )
}

export function Err({ children }) {
  if (!children) return null
  return <div className="err-box" role="alert">{children}</div>
}
