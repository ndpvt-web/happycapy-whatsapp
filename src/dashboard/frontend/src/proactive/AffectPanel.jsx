import { AFFECT_COLORS } from './constants';

export function AffectPanel({ data }) {
  if (!data) return null;

  const distribution = data.distribution || {};
  const entries = Object.entries(distribution).map(([key, val]) => ({
    key, label: AFFECT_COLORS[key]?.label || key, value: val,
    color: AFFECT_COLORS[key]?.dot || '#71717a',
  }));

  const total = entries.reduce((s, d) => s + d.value, 0);

  let gradientStops = '';
  let cumulative = 0;
  if (total > 0) {
    entries.forEach((entry, i) => {
      const startPct = (cumulative / total) * 100;
      cumulative += entry.value;
      const endPct = (cumulative / total) * 100;
      if (i > 0) gradientStops += ', ';
      gradientStops += `${entry.color} ${startPct}% ${endPct}%`;
    });
  }

  const donutStyle = total > 0
    ? { background: `conic-gradient(${gradientStops})`,
        WebkitMask: 'radial-gradient(farthest-side, transparent 55%, #fff 56%)',
        mask: 'radial-gradient(farthest-side, transparent 55%, #fff 56%)' }
    : { background: 'var(--surface-2)' };

  return (
    <div className="rounded-xl p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-semibold mb-4">Affective State Distribution</h3>
      <div className="flex items-center gap-6">
        <div className="flex-shrink-0 relative" style={{ width: 140, height: 140 }}>
          <div className="w-full h-full rounded-full" style={donutStyle} />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <p className="text-xl font-bold">{total}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>students</p>
            </div>
          </div>
        </div>
        <div className="flex-1 space-y-2">
          {entries.map((d) => (
            <div key={d.key} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: d.color }} />
                <span className="text-xs">{d.label}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono">{d.value}</span>
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                  {total > 0 ? `${Math.round((d.value / total) * 100)}%` : ''}
                </span>
              </div>
            </div>
          ))}
          {entries.length === 0 && (
            <p className="text-xs" style={{ color: 'var(--text-dim)' }}>No affect data recorded yet</p>
          )}
        </div>
      </div>

      {(() => {
        const atRisk = entries.filter((e) => ['frustrated', 'anxious'].includes(e.key));
        const atRiskCount = atRisk.reduce((s, e) => s + e.value, 0);
        if (atRiskCount === 0) return null;
        return (
          <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
            <p className="text-xs" style={{ color: 'var(--warning)' }}>
              {atRiskCount} student{atRiskCount !== 1 ? 's' : ''} showing signs of distress
            </p>
          </div>
        );
      })()}
    </div>
  );
}
