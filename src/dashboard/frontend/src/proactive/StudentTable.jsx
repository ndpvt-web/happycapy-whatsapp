import { useState, useMemo } from 'react';
import { Search, ChevronUp, ChevronDown, ExternalLink } from 'lucide-react';
import { AFFECT_COLORS, formatJid } from './constants';

function AffectBadge({ affect }) {
  const cfg = AFFECT_COLORS[affect] || AFFECT_COLORS.neutral;
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ background: `${cfg.dot}20`, color: cfg.dot }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: cfg.dot }} />
      {cfg.label}
    </span>
  );
}

function EngagementBar({ score }) {
  const s = score || 0;
  const color = s >= 70 ? 'var(--accent)' : s >= 40 ? 'var(--warning)' : 'var(--danger)';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div className="h-full rounded-full" style={{ width: `${s}%`, background: color }} />
      </div>
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{s}%</span>
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
      list = list.filter((s) =>
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
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
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
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div className="p-4 flex items-center justify-between"
        style={{ borderBottom: '1px solid var(--border)' }}>
        <h2 className="text-sm font-semibold">Students ({students.length})</h2>
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-dim)' }} />
          <input type="text" placeholder="Search students..."
            value={search} onChange={(e) => setSearch(e.target.value)}
            className="pl-8 pr-3 py-1.5 text-sm rounded-lg focus:outline-none w-52"
            style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: 'rgba(0,0,0,0.2)' }}>
              {columns.map((col) => (
                <th key={col.key}
                  className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider cursor-pointer select-none"
                  style={{ color: 'var(--text-dim)' }}
                  onClick={() => toggleSort(col.key)}>
                  <span className="inline-flex items-center gap-1">
                    {col.label} <SortIcon col={col.key} />
                  </span>
                </th>
              ))}
              <th className="px-4 py-2.5 w-10" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.jid}
                className="cursor-pointer transition-colors hover:bg-white/3"
                onClick={() => onSelect(s.jid)}
                style={{ borderBottom: '1px solid rgba(39,39,42,0.5)' }}>
                <td className="px-4 py-3">
                  <div>
                    <p className="font-medium">{s.name || s.display_name || formatJid(s.jid)}</p>
                    <p className="text-xs" style={{ color: 'var(--text-dim)' }}>{formatJid(s.jid)}</p>
                  </div>
                </td>
                <td className="px-4 py-3" style={{ color: 'var(--text-dim)' }}>{s.board || '--'}</td>
                <td className="px-4 py-3" style={{ color: 'var(--text-dim)' }}>{s.class || '--'}</td>
                <td className="px-4 py-3">
                  <span className="font-mono">
                    {s.current_streak || 0}<span className="text-xs ml-0.5" style={{ color: 'var(--text-dim)' }}>d</span>
                  </span>
                </td>
                <td className="px-4 py-3"><EngagementBar score={s.engagement_score} /></td>
                <td className="px-4 py-3"><AffectBadge affect={s.recent_affect || 'neutral'} /></td>
                <td className="px-4 py-3 text-xs" style={{ color: 'var(--text-dim)' }}>{s.exam_date || '--'}</td>
                <td className="px-4 py-3"><ExternalLink size={14} style={{ color: 'var(--text-dim)' }} /></td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-12 text-center" style={{ color: 'var(--text-dim)' }}>
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
