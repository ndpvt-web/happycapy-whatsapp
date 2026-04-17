import { AFFECT_COLORS } from '../lib/constants';

export function AffectPanel({ data }) {
  if (!data) return null;

  const distribution = data.distribution || {};
  const entries = Object.entries(distribution).map(([key, val]) => ({
    key,
    label: AFFECT_COLORS[key]?.label || key,
    value: val,
    color: AFFECT_COLORS[key]?.dot || '#71717a',
  }));

  const total = entries.reduce((s, d) => s + d.value, 0);

  // Build conic-gradient stops
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
    ? {
        background: `conic-gradient(${gradientStops})`,
        WebkitMask: 'radial-gradient(farthest-side, transparent 55%, #fff 56%)',
        mask: 'radial-gradient(farthest-side, transparent 55%, #fff 56%)',
      }
    : { background: '#27272a' };

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <h3 className="text-sm font-semibold text-zinc-200 mb-4">Affective State Distribution</h3>
      <div className="flex items-center gap-6">
        {/* Donut chart via CSS conic-gradient */}
        <div className="flex-shrink-0 relative" style={{ width: 140, height: 140 }}>
          <div className="w-full h-full rounded-full" style={donutStyle} />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <p className="text-xl font-bold text-zinc-200">{total}</p>
              <p className="text-xs text-zinc-500">students</p>
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex-1 space-y-2">
          {entries.map((d) => (
            <div key={d.key} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span
                  className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                  style={{ background: d.color }}
                />
                <span className="text-xs text-zinc-300">{d.label}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-zinc-200">{d.value}</span>
                <span className="text-xs text-zinc-600">
                  {total > 0 ? `${Math.round((d.value / total) * 100)}%` : ''}
                </span>
              </div>
            </div>
          ))}
          {entries.length === 0 && (
            <p className="text-xs text-zinc-600">No affect data recorded yet</p>
          )}
        </div>
      </div>

      {/* At-risk students callout */}
      {(() => {
        const atRisk = entries.filter((e) => ['frustrated', 'anxious'].includes(e.key));
        const atRiskCount = atRisk.reduce((s, e) => s + e.value, 0);
        if (atRiskCount === 0) return null;
        return (
          <div className="mt-3 pt-3 border-t border-border">
            <p className="text-xs text-amber-400">
              {atRiskCount} student{atRiskCount !== 1 ? 's' : ''} showing signs of distress
              ({atRisk.map((e) => e.label.toLowerCase()).join(', ')})
            </p>
          </div>
        );
      })()}
    </div>
  );
}
