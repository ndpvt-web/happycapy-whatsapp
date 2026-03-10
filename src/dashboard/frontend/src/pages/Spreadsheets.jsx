import { useState, useEffect } from 'react';
import { FileSpreadsheet, Download, ChevronRight, ArrowLeft } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, Badge, PageHeader, Spinner, EmptyState } from '../ui';

function SpreadsheetViewer({ name, onBack }) {
  const [activeSheet, setActiveSheet] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.spreadsheetData(name, 200, activeSheet)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [name, activeSheet]);

  if (loading) return <Spinner />;
  if (!data) return <EmptyState message="Could not load spreadsheet" />;

  const headers = data.headers || [];
  const rows = data.rows || [];
  const sheets = data.sheets || [];

  // Status color helper
  const statusColor = (val) => {
    const s = String(val).toLowerCase();
    if (['completed', 'closed', 'true'].includes(s)) return 'var(--accent)';
    if (['in_progress', 'open', 'pending_review'].includes(s)) return 'var(--warning)';
    if (['scheduled', 'follow_up'].includes(s)) return 'var(--info)';
    if (['high', 'urgent'].includes(s)) return 'var(--danger)';
    if (['medium'].includes(s)) return 'var(--warning)';
    if (['low'].includes(s)) return 'var(--accent)';
    if (s === 'false') return 'var(--danger)';
    return null;
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="flex items-center gap-1 text-sm cursor-pointer hover:underline"
            style={{ color: 'var(--accent)' }}>
            <ArrowLeft size={14} /> Back
          </button>
          <div className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: 'var(--accent-dim)' }}>
            <FileSpreadsheet size={16} style={{ color: 'var(--accent)' }} />
          </div>
          <h2 className="text-lg font-bold">{name}</h2>
          <Badge>{rows.length} / {data.total} rows</Badge>
        </div>
        <a href={`/api/spreadsheets/${encodeURIComponent(name)}/download`}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors hover:opacity-80"
          style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
          <Download size={12} /> Download .xlsx
        </a>
      </div>

      {/* Sheet tabs */}
      {sheets.length > 1 && (
        <div className="flex gap-1 mb-4 p-1 rounded-lg" style={{ background: 'var(--surface)' }}>
          {sheets.map(s => (
            <button key={s}
              onClick={() => setActiveSheet(s === activeSheet ? null : s)}
              className="px-4 py-2 rounded-md text-sm font-medium transition-all cursor-pointer"
              style={{
                background: (activeSheet === s || (!activeSheet && s === sheets[0]))
                  ? 'var(--surface-2)' : 'transparent',
                color: (activeSheet === s || (!activeSheet && s === sheets[0]))
                  ? 'var(--accent)' : 'var(--text-dim)',
                border: (activeSheet === s || (!activeSheet && s === sheets[0]))
                  ? '1px solid var(--border)' : '1px solid transparent',
              }}>
              {s}
              <span className="ml-2 text-xs" style={{ opacity: 0.6 }}>
                {activeSheet === s || (!activeSheet && s === sheets[0]) ? `${rows.length}` : ''}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Table */}
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th className="text-left py-2.5 px-3 text-xs font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-dim)' }}>#</th>
                {headers.map((h, i) => (
                  <th key={i} className="text-left py-2.5 px-3 text-xs font-semibold uppercase tracking-wider whitespace-nowrap"
                    style={{ color: 'var(--text-dim)' }}>{h.replace(/_/g, ' ')}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-white/3 transition-colors"
                  style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="py-2.5 px-3 text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>{ri + 1}</td>
                  {headers.map((h, ci) => {
                    const val = row[h];
                    const color = statusColor(val);
                    return (
                      <td key={ci} className="py-2.5 px-3 max-w-xs truncate">
                        {color ? (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium"
                            style={{ background: `color-mix(in srgb, ${color} 15%, transparent)`, color }}>
                            {String(val)}
                          </span>
                        ) : (
                          <span>{val != null ? String(val) : ''}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

export default function Spreadsheets() {
  const { data, loading } = useAPI(api.spreadsheets, []);
  const [selected, setSelected] = useState(null);

  if (selected) return <SpreadsheetViewer name={selected} onBack={() => setSelected(null)} />;

  if (loading) return <Spinner />;

  const files = data?.spreadsheets || [];

  return (
    <div>
      <PageHeader title="Spreadsheets" subtitle="Excel files managed by the bot" />

      {files.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {files.map((f, i) => (
            <Card key={i} className="cursor-pointer hover:border-emerald-500/30 transition-all"
              onClick={() => setSelected(f.name)}>
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg flex items-center justify-center"
                  style={{ background: 'var(--accent-dim)' }}>
                  <FileSpreadsheet size={20} style={{ color: 'var(--accent)' }} />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{f.name}</div>
                  <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
                    {f.size_kb} KB &middot; Modified {f.modified?.slice(0, 10)}
                  </div>
                </div>
                <ChevronRight size={16} style={{ color: 'var(--text-dim)' }} />
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState message="No spreadsheets yet. The bot creates them when logging data via the spreadsheet integration." />
        </Card>
      )}
    </div>
  );
}
