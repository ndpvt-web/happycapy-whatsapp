import { pct } from '../lib/constants';

const SUBJECT_COLORS = {
  Maths: '#a855f7',
  Physics: '#3b82f6',
  Chemistry: '#22c55e',
  English: '#f59e0b',
  Urdu: '#ec4899',
  Hindi: '#ef4444',
  Biology: '#14b8a6',
  History: '#f97316',
  Geography: '#06b6d4',
};

function getColor(subject) {
  return SUBJECT_COLORS[subject] || '#8b5cf6';
}

export function MasteryPanel({ data }) {
  if (!data) return null;

  const { summary = {}, by_subject = [] } = data;

  const masteredPct = summary.total_concepts
    ? Math.round(((summary.mastered || 0) / summary.total_concepts) * 100)
    : 0;

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200">Concept Mastery by Subject</h3>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-lg font-bold text-purple-400">{summary.total_concepts || 0}</p>
            <p className="text-xs text-zinc-500">concepts</p>
          </div>
          <div className="text-right">
            <p className="text-lg font-bold text-green-400">{masteredPct}%</p>
            <p className="text-xs text-zinc-500">mastered</p>
          </div>
        </div>
      </div>

      {by_subject.length > 0 ? (
        <div className="space-y-3">
          {by_subject.map((s) => {
            const val = Math.round((s.avg_mastery || 0) * 100);
            const color = getColor(s.subject);
            return (
              <div key={s.subject}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-zinc-300 font-medium">{s.subject}</span>
                  <span className="text-xs text-zinc-500">
                    {val}% <span className="text-zinc-600">({s.count})</span>
                  </span>
                </div>
                <div className="h-5 bg-zinc-800 rounded overflow-hidden relative">
                  <div
                    className="h-full rounded transition-all duration-500"
                    style={{
                      width: `${val}%`,
                      background: `linear-gradient(90deg, ${color}cc, ${color})`,
                      minWidth: val > 0 ? '8px' : '0',
                    }}
                  />
                  {val >= 15 && (
                    <span
                      className="absolute left-2 top-1/2 -translate-y-1/2 text-xs font-mono font-bold"
                      style={{ color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}
                    >
                      {val}%
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="h-48 flex items-center justify-center text-zinc-600 text-sm">
          No mastery data yet. Concepts are tracked via SM-2 spaced repetition.
        </div>
      )}

      {summary.due_now > 0 && (
        <div className="mt-3 pt-3 border-t border-border">
          <p className="text-xs text-amber-400">
            {summary.due_now} concept{summary.due_now !== 1 ? 's' : ''} due for review now
          </p>
        </div>
      )}
    </div>
  );
}
