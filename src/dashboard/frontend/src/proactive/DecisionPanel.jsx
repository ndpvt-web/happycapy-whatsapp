import { TYPE_COLORS, TYPE_LABELS } from './constants';

export function DecisionPanel({ data }) {
  if (!data) return null;

  const chartData = (data.decision_distribution || [])
    .map((d) => ({
      type: d.message_type,
      label: TYPE_LABELS[d.message_type] || d.message_type,
      count: d.count || 0,
      color: TYPE_COLORS[d.message_type] || '#71717a',
    }))
    .sort((a, b) => b.count - a.count);

  const total = chartData.reduce((s, d) => s + d.count, 0);
  const maxCount = Math.max(...chartData.map((d) => d.count), 1);

  return (
    <div className="rounded-xl p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold">Decision Type Distribution</h3>
        <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Last 30 days</span>
      </div>

      {chartData.length > 0 ? (
        <>
          <div className="space-y-2">
            {chartData.map((d) => {
              const pctWidth = Math.max((d.count / maxCount) * 100, 3);
              return (
                <div key={d.type} className="flex items-center gap-2">
                  <span className="text-xs w-24 text-right flex-shrink-0 truncate"
                    style={{ color: 'var(--text-dim)' }}>{d.label}</span>
                  <div className="flex-1 h-4 rounded overflow-hidden relative"
                    style={{ background: 'var(--surface-2)' }}>
                    <div className="h-full rounded transition-all duration-500"
                      style={{ width: `${pctWidth}%`, background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`, minWidth: '8px' }} />
                  </div>
                  <span className="text-xs font-mono w-6 text-right flex-shrink-0">{d.count}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-2 text-xs text-center"
            style={{ borderTop: '1px solid var(--border)', color: 'var(--text-dim)' }}>
            {total} total decisions across {chartData.length} types
          </div>
        </>
      ) : (
        <div className="h-48 flex items-center justify-center text-sm" style={{ color: 'var(--text-dim)' }}>
          No proactive messages sent in the last 30 days.
        </div>
      )}
    </div>
  );
}
