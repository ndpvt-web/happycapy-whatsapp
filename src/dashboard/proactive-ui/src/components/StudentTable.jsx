import { useState, useMemo } from 'react';
import { Search, ChevronUp, ChevronDown, ExternalLink } from 'lucide-react';
import { AFFECT_COLORS, formatJid } from '../lib/constants';

function AffectBadge({ affect }) {
  const cfg = AFFECT_COLORS[affect] || AFFECT_COLORS.neutral;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: cfg.dot }} />
      {cfg.label}
    </span>
  );
}

function EngagementBar({ score }) {
  const s = score || 0;
  const color = s >= 70 ? 'bg-green-500' : s >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${s}%` }} />
      </div>
      <span className="text-xs text-zinc-400">{s}%</span>
    </div>
  );
}

export function StudentTable({ students, onSelect }) {
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState('current_streak');
  const [sortDir, setSortDir] = useState('desc');

  const sorted = useMemo(() => {
    let list = [...students];
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (s) =>
          (s.name || '').toLowerCase().includes(q) ||
          (s.display_name || '').toLowerCase().includes(q) ||
          formatJid(s.jid).includes(q) ||
          (s.board || '').toLowerCase().includes(q)
      );
    }
    list.sort((a, b) => {
      let av = a[sortKey] ?? 0;
      let bv = b[sortKey] ?? 0;
      if (typeof av === 'string') av = av.toLowerCase();
      if (typeof bv === 'string') bv = bv.toLowerCase();
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
    return list;
  }, [students, search, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return null;
    return sortDir === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />;
  };

  const columns = [
    { key: 'name', label: 'Student' },
    { key: 'board', label: 'Board' },
    { key: 'class', label: 'Class' },
    { key: 'current_streak', label: 'Streak' },
    { key: 'engagement_score', label: 'Engagement' },
    { key: 'recent_affect', label: 'Affect' },
    { key: 'exam_date', label: 'Exam' },
  ];

  return (
    <div className="bg-surface rounded-xl border border-border overflow-hidden">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">
          Students ({students.length})
        </h2>
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            placeholder="Search students..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 pr-3 py-1.5 text-sm bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-green-800 w-52"
          />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-zinc-900/50">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500 uppercase tracking-wider cursor-pointer hover:text-zinc-300 select-none"
                  onClick={() => toggleSort(col.key)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label} <SortIcon col={col.key} />
                  </span>
                </th>
              ))}
              <th className="px-4 py-2.5 w-10" />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {sorted.map((s) => (
              <tr
                key={s.jid}
                className="hover:bg-zinc-900/40 cursor-pointer transition-colors"
                onClick={() => onSelect(s.jid)}
              >
                <td className="px-4 py-3">
                  <div>
                    <p className="text-zinc-200 font-medium">
                      {s.name || s.display_name || formatJid(s.jid)}
                    </p>
                    <p className="text-xs text-zinc-600">{formatJid(s.jid)}</p>
                  </div>
                </td>
                <td className="px-4 py-3 text-zinc-400">{s.board || '--'}</td>
                <td className="px-4 py-3 text-zinc-400">{s.class || '--'}</td>
                <td className="px-4 py-3">
                  <span className="text-zinc-200 font-mono">
                    {s.current_streak || 0}
                    <span className="text-zinc-600 text-xs ml-0.5">d</span>
                  </span>
                </td>
                <td className="px-4 py-3">
                  <EngagementBar score={s.engagement_score} />
                </td>
                <td className="px-4 py-3">
                  <AffectBadge affect={s.recent_affect || 'neutral'} />
                </td>
                <td className="px-4 py-3 text-zinc-400 text-xs">
                  {s.exam_date || '--'}
                </td>
                <td className="px-4 py-3">
                  <ExternalLink size={14} className="text-zinc-600" />
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-12 text-center text-zinc-600">
                  {search ? 'No students match your search' : 'No students enrolled yet'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
