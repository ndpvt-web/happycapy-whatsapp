import { TYPE_COLORS, TYPE_LABELS } from '../lib/constants';

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
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200">Decision Type Distribution</h3>
        <span className="text-xs text-zinc-500">Last 30 days</span>
      </div>

      {chartData.length > 0 ? (
        <>
          <div className="space-y-2">
            {chartData.map((d) => {
              const pctWidth = Math.max((d.count / maxCount) * 100, 3);
              return (
                <div key={d.type} className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400 w-24 text-right flex-shrink-0 truncate">
                    {d.label}
                  </span>
                  <div className="flex-1 h-4 bg-zinc-800 rounded overflow-hidden relative">
                    <div
                      className="h-full rounded transition-all duration-500"
                      style={{
                        width: `${pctWidth}%`,
                        background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`,
                        minWidth: '8px',
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-zinc-300 w-6 text-right flex-shrink-0">
                    {d.count}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-2 border-t border-border text-xs text-zinc-500 text-center">
            {total} total decisions across {chartData.length} types
          </div>
        </>
      ) : (
        <div className="h-48 flex items-center justify-center text-zinc-600 text-sm">
          No proactive messages sent in the last 30 days.
        </div>
      )}
    </div>
  );
}
