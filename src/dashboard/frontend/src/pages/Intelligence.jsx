import { useState, useEffect } from 'react';
import { Brain, GitBranch, BookOpen, Calendar, FileText, X, ChevronRight, ArrowLeft } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, Badge, PageHeader, Spinner, EmptyState, Tabs } from '../ui';

const typeColors = {
  person: 'var(--info)', place: 'var(--accent)', topic: 'var(--warning)',
  event: 'var(--danger)', organization: '#8b5cf6', preference: '#ec4899',
  other: 'var(--text-dim)',
};

function KnowledgeGraph() {
  const { data, loading } = useAPI(api.knowledgeGraph, []);
  const [filter, setFilter] = useState('all');

  if (loading) return <Spinner />;

  const entities = data?.entities || [];
  const relationships = data?.relationships || [];
  const typeStats = data?.entity_type_stats || [];

  // Group relationships by contact for clarity
  const relsByContact = {};
  for (const r of relationships) {
    const key = r.contact_name || 'Unknown';
    if (!relsByContact[key]) relsByContact[key] = [];
    relsByContact[key].push(r);
  }

  // Filter entities by type if selected
  const filteredEntities = filter === 'all'
    ? entities
    : entities.filter(e => e.entity_type === filter);

  // Deduplicate entities by display_name (collapse Contact/You across JIDs)
  const seen = new Set();
  const uniqueEntities = filteredEntities.filter(e => {
    const key = `${e.display_name}|${e.entity_type}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return (
    <div>
      {/* Type Stats - clickable filters */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mb-6">
        {typeStats.map((t, i) => (
          <button key={i} onClick={() => setFilter(filter === t.entity_type ? 'all' : t.entity_type)}
            className="rounded-lg p-3 text-center transition-all cursor-pointer"
            style={{
              background: filter === t.entity_type ? 'var(--surface-2)' : 'var(--surface)',
              border: `1px solid ${filter === t.entity_type ? (typeColors[t.entity_type] || 'var(--border)') : 'var(--border)'}`,
            }}>
            <div className="text-2xl font-bold" style={{ color: typeColors[t.entity_type] || 'var(--text)' }}>
              {t.cnt}
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>
              {t.entity_type}
            </div>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Entities */}
        <Card>
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-medium">
              Entities {filter !== 'all' && <Badge color={typeColors[filter]}>{filter}</Badge>}
            </div>
            {filter !== 'all' && (
              <button onClick={() => setFilter('all')} className="text-xs cursor-pointer"
                style={{ color: 'var(--accent)' }}>Show all</button>
            )}
          </div>
          {uniqueEntities.length > 0 ? (
            <div className="space-y-1 max-h-[28rem] overflow-y-auto pr-1">
              {uniqueEntities.map((e, i) => (
                <div key={i} className="flex items-center justify-between py-2 px-2 rounded-lg hover:bg-white/5 transition-colors">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium truncate">{e.display_name}</span>
                    <Badge color={typeColors[e.entity_type] || 'var(--text-dim)'}>{e.entity_type}</Badge>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-xs px-2 py-0.5 rounded-full"
                      style={{ background: 'var(--surface-2)', color: 'var(--text-dim)' }}>
                      {e.contact_name}
                    </span>
                    <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
                      {e.mention_count}x
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyState message="No entities extracted yet" />}
        </Card>

        {/* Relationships grouped by contact */}
        <Card>
          <div className="text-sm font-medium mb-3">Relationships</div>
          {Object.keys(relsByContact).length > 0 ? (
            <div className="space-y-4 max-h-[28rem] overflow-y-auto pr-1">
              {Object.entries(relsByContact).map(([contact, rels]) => (
                <div key={contact}>
                  <div className="text-xs font-medium mb-2 px-2 py-1 rounded"
                    style={{ background: 'var(--surface-2)', color: 'var(--accent)' }}>
                    {contact}
                  </div>
                  <div className="space-y-1">
                    {rels.map((r, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors">
                        <span className="font-medium shrink-0">{r.source_display}</span>
                        <span className="px-2 py-0.5 rounded text-xs shrink-0"
                          style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                          {r.relationship_type?.replace(/_/g, ' ')}
                        </span>
                        <span className="font-medium truncate">{r.target_display}</span>
                        {r.weight > 1 && (
                          <span className="text-xs shrink-0" style={{ color: 'var(--text-dim)' }}>
                            {r.weight}x
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyState message="No relationships found" />}
        </Card>
      </div>
    </div>
  );
}

function Lessons() {
  const { data, loading } = useAPI(api.lessons, []);
  if (loading) return <Spinner />;

  const lessons = data?.lessons || [];
  const stats = data?.stats || [];

  return (
    <div>
      {stats.length > 0 && (
        <div className="flex gap-2 mb-4 flex-wrap">
          {stats.map((s, i) => (
            <Badge key={i} color="var(--info)">{s.category}: {s.cnt}</Badge>
          ))}
        </div>
      )}
      {lessons.length > 0 ? (
        <div className="space-y-2">
          {lessons.map((l, i) => (
            <Card key={i}>
              <div className="flex items-center gap-2 mb-1">
                <Badge>{l.category}</Badge>
                <Badge color="var(--text-dim)">applied {l.times_applied}x</Badge>
                <span className="text-xs ml-auto" style={{ color: 'var(--text-dim)' }}>{l.created_at?.slice(0, 10)}</span>
              </div>
              <div className="text-sm">{l.lesson}</div>
              {l.context && <div className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{l.context}</div>}
            </Card>
          ))}
        </div>
      ) : <EmptyState message="No lessons learned yet" />}
    </div>
  );
}

function FileViewer({ scope, filename, onClose }) {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.memoryRead(scope, filename)
      .then(d => { setContent(d.content); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [scope, filename]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}>
      <div className="w-full max-w-3xl max-h-[80vh] flex flex-col rounded-xl overflow-hidden"
        style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2">
            <FileText size={16} style={{ color: 'var(--accent)' }} />
            <span className="text-sm font-medium">{filename}</span>
            <span className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: 'var(--surface-2)', color: 'var(--text-dim)' }}>
              {scope === 'global' ? 'Global' : scope}
            </span>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-white/10 transition-colors cursor-pointer">
            <X size={16} />
          </button>
        </div>
        {/* Content */}
        <div className="overflow-y-auto flex-1 p-4">
          {loading ? <Spinner /> : error ? (
            <div className="text-sm" style={{ color: 'var(--danger)' }}>{error}</div>
          ) : (
            <pre className="text-sm leading-relaxed whitespace-pre-wrap font-mono"
              style={{ color: 'var(--text)' }}>
              {content}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function MemoryView() {
  const { data, loading } = useAPI(api.memory, []);
  const [viewing, setViewing] = useState(null); // { scope, filename }

  if (loading) return <Spinner />;

  const global = data?.global || {};
  const contacts = data?.contacts || [];

  return (
    <div>
      {viewing && (
        <FileViewer
          scope={viewing.scope}
          filename={viewing.filename}
          onClose={() => setViewing(null)}
        />
      )}

      {/* Global Memory */}
      {Object.keys(global).length > 0 && (
        <Card className="mb-4">
          <div className="text-sm font-medium mb-3 flex items-center gap-2">
            <Brain size={14} style={{ color: 'var(--accent)' }} />
            Global Memory
          </div>
          <div className="space-y-1">
            {Object.entries(global).map(([name, info]) => (
              <button key={name}
                onClick={() => setViewing({ scope: 'global', filename: name })}
                className="w-full flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-white/5 transition-colors cursor-pointer text-left"
                style={{ border: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2">
                  <FileText size={14} style={{ color: 'var(--accent)' }} />
                  <span className="text-sm font-medium">{name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                    {info.lines} lines
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                    {info.size_bytes} bytes
                  </span>
                  <ChevronRight size={14} style={{ color: 'var(--text-dim)' }} />
                </div>
              </button>
            ))}
          </div>
          {/* Preview of global MEMORY.md */}
          {global['MEMORY.md']?.preview && (
            <div className="mt-3 p-3 rounded-lg text-xs font-mono leading-relaxed"
              style={{ background: 'var(--bg)', color: 'var(--text-dim)', border: '1px solid var(--border)' }}>
              {global['MEMORY.md'].preview.slice(0, 300)}
            </div>
          )}
        </Card>
      )}

      {/* Per-contact Memory */}
      <Card>
        <div className="text-sm font-medium mb-3 flex items-center gap-2">
          <BookOpen size={14} style={{ color: 'var(--info)' }} />
          Per-Contact Memory
          <span className="text-xs px-2 py-0.5 rounded-full"
            style={{ background: 'var(--surface-2)', color: 'var(--text-dim)' }}>
            {contacts.length} contacts
          </span>
        </div>
        {contacts.length > 0 ? (
          <div className="space-y-3">
            {contacts.map((c, i) => (
              <div key={i} className="rounded-lg p-3"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}>
                <div className="text-xs font-mono mb-2" style={{ color: 'var(--text-dim)' }}>
                  {c.display_name && c.display_name !== c.hash
                    ? <><span style={{ color: 'var(--accent)' }}>{c.display_name}</span> <span style={{ opacity: 0.5 }}>({c.hash})</span></>
                    : <>Contact: {c.hash}</>}
                </div>
                <div className="flex gap-2 flex-wrap">
                  {Object.entries(c.files).map(([name, info]) => (
                    <button key={name}
                      onClick={() => setViewing({ scope: c.hash, filename: name })}
                      className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-white/5 transition-colors cursor-pointer"
                      style={{ border: '1px solid var(--border)' }}>
                      <FileText size={12} style={{ color: 'var(--accent)' }} />
                      <span className="text-sm">{name}</span>
                      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                        {info.lines}L
                      </span>
                      <ChevronRight size={12} style={{ color: 'var(--text-dim)' }} />
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : <EmptyState message="No per-contact memory yet" />}
      </Card>
    </div>
  );
}

function CronView() {
  const { data, loading } = useAPI(api.cronJobs, []);
  if (loading) return <Spinner />;

  const jobs = data?.jobs || [];

  return jobs.length > 0 ? (
    <div className="space-y-2">
      {jobs.map((j, i) => (
        <Card key={i}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">{j.name}</div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{j.message?.slice(0, 80)}</div>
            </div>
            <div className="flex items-center gap-2">
              <Badge color={j.enabled ? 'var(--accent)' : 'var(--text-dim)'}>
                {j.enabled ? 'active' : 'disabled'}
              </Badge>
              <Badge color="var(--info)">{j.kind}</Badge>
            </div>
          </div>
        </Card>
      ))}
    </div>
  ) : <EmptyState message="No scheduled jobs" />;
}

export default function Intelligence() {
  const [tab, setTab] = useState('kg');

  return (
    <div>
      <PageHeader title="Intelligence" subtitle="Knowledge graph, memory, lessons, and scheduling" />
      <Tabs active={tab} onChange={setTab} tabs={[
        { id: 'kg', label: 'Knowledge Graph' },
        { id: 'lessons', label: 'Lessons Learned' },
        { id: 'memory', label: 'Memory Files' },
        { id: 'cron', label: 'Scheduled Jobs' },
      ]} />
      {tab === 'kg' && <KnowledgeGraph />}
      {tab === 'lessons' && <Lessons />}
      {tab === 'memory' && <MemoryView />}
      {tab === 'cron' && <CronView />}
    </div>
  );
}
