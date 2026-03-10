export function Card({ children, className = '', ...props }) {
  return (
    <div className={`rounded-xl p-5 ${className}`}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
      {...props}>
      {children}
    </div>
  );
}

export function StatCard({ label, value, sub, color = 'var(--accent)' }) {
  return (
    <Card>
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-dim)' }}>{label}</div>
      <div className="text-2xl font-bold tracking-tight" style={{ color }}>{value}</div>
      {sub && <div className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{sub}</div>}
    </Card>
  );
}

export function Badge({ children, color = 'var(--accent)' }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ background: `${color}20`, color }}>
      {children}
    </span>
  );
}

export function Table({ headers, rows, onRowClick }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {headers.map((h, i) => (
              <th key={i} className="text-left py-3 px-3 font-medium text-xs uppercase tracking-wider"
                style={{ color: 'var(--text-dim)' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}
              className={onRowClick ? 'cursor-pointer hover:bg-white/3' : ''}
              onClick={() => onRowClick?.(row)}
              style={{ borderBottom: '1px solid var(--border)' }}>
              {row.cells.map((cell, ci) => (
                <td key={ci} className="py-3 px-3">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="w-6 h-6 border-2 border-t-transparent rounded-full animate-spin"
        style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
    </div>
  );
}

export function ErrorBox({ message }) {
  return (
    <div className="rounded-lg px-4 py-3 text-sm"
      style={{ background: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.2)' }}>
      {message}
    </div>
  );
}

export function EmptyState({ message }) {
  return (
    <div className="flex items-center justify-center py-16 text-sm" style={{ color: 'var(--text-dim)' }}>
      {message}
    </div>
  );
}

export function PageHeader({ title, subtitle, action }) {
  return (
    <div className="flex items-center justify-between mb-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm mt-1" style={{ color: 'var(--text-dim)' }}>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="flex gap-1 mb-5 p-1 rounded-lg" style={{ background: 'var(--surface-2)' }}>
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-all cursor-pointer
            ${active === t.id ? 'text-white shadow-sm' : ''}`}
          style={active === t.id
            ? { background: 'var(--surface)', color: 'var(--text)' }
            : { color: 'var(--text-dim)' }}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
