import { TYPE_COLORS, TYPE_LABELS } from '../lib/constants';

export function EffectivenessPanel({ data }) {
  if (!data) return null;

  const overall = data.overall || {};
  const responseRate = overall.total
    ? Math.round(((overall.responded || 0) / overall.total) * 100)
    : 0;

  const byType = (data.by_type || []).map((t) => ({
    type: t.message_type,
    label: TYPE_LABELS[t.message_type] || t.message_type,
    total: t.total || 0,
    responded: t.responded || 0,
    rate: t.total ? Math.round(((t.responded || 0) / t.total) * 100) : 0,
    color: TYPE_COLORS[t.message_type] || '#71717a',
  })).sort((a, b) => b.total - a.total);

  const maxTotal = Math.max(...byType.map((t) => t.total), 1);

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200">Message Effectiveness</h3>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <p className="text-lg font-bold text-green-400">{responseRate}%</p>
            <p className="text-xs text-zinc-500">response rate</p>
          </div>
          <div className="text-right">
            <p className="text-lg font-bold text-zinc-200">
              {overall.avg_response_min != null ? `${Math.round(overall.avg_response_min)}m` : '--'}
            </p>
            <p className="text-xs text-zinc-500">avg response</p>
          </div>
        </div>
      </div>

      {byType.length > 0 ? (
        <div className="space-y-2">
          {byType.map((t) => {
            const totalWidth = Math.max((t.total / maxTotal) * 100, 5);
            const respondedWidth = t.total > 0 ? (t.responded / t.total) * totalWidth : 0;
            const noResponseWidth = totalWidth - respondedWidth;
            return (
              <div key={t.type} className="flex items-center gap-2">
                <span className="text-xs text-zinc-400 w-20 text-right flex-shrink-0 truncate">
                  {t.label}
                </span>
                <div className="flex-1 h-5 flex rounded overflow-hidden bg-zinc-800">
                  {respondedWidth > 0 && (
                    <div
                      className="h-full"
                      style={{
                        width: `${respondedWidth}%`,
                        background: `linear-gradient(90deg, ${t.color}cc, ${t.color})`,
                      }}
                    />
                  )}
                  {noResponseWidth > 0 && (
                    <div
                      className="h-full"
                      style={{ width: `${noResponseWidth}%`, background: '#3f3f46' }}
                    />
                  )}
                </div>
                <span className="text-xs font-mono text-zinc-300 w-14 text-right flex-shrink-0">
                  {t.responded}/{t.total}
                </span>
              </div>
            );
          })}

          {/* Legend */}
          <div className="flex items-center gap-4 mt-1 pt-1">
            <div className="flex items-center gap-1">
              <div className="w-2 h-2 rounded bg-green-500" />
              <span className="text-xs text-zinc-500">Responded</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="w-2 h-2 rounded bg-zinc-600" />
              <span className="text-xs text-zinc-500">No Response</span>
            </div>
          </div>
        </div>
      ) : (
        <div className="h-48 flex items-center justify-center text-zinc-600 text-sm">
          No effectiveness data yet. Send proactive messages first.
        </div>
      )}

      {overall.total > 0 && (
        <div className="grid grid-cols-3 gap-3 mt-3 pt-3 border-t border-border">
          <div className="text-center">
            <p className="text-xs text-zinc-500">Total Sent</p>
            <p className="text-sm font-mono text-zinc-200">{overall.total}</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-zinc-500">Led to Study</p>
            <p className="text-sm font-mono text-green-400">{overall.led_to_study || 0}</p>
          </div>
          <div className="text-center">
            <p className="text-xs text-zinc-500">Sentiment</p>
            <p className="text-sm font-mono">
              <span className="text-green-400">{overall.positive || 0}</span>
              <span className="text-zinc-600"> / </span>
              <span className="text-red-400">{overall.negative || 0}</span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
