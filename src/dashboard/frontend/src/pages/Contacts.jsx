import { useState } from 'react';
import { Users, Search, ChevronRight } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, Badge, PageHeader, Spinner, EmptyState, Table } from '../ui';

function ContactDetail({ jid, onBack }) {
  const { data, loading } = useAPI(() => api.contactDetail(jid), [jid]);

  if (loading) return <Spinner />;
  if (!data) return <EmptyState message="Contact not found" />;

  const p = data.profile_data || {};

  return (
    <div>
      <button onClick={onBack} className="text-sm mb-4 cursor-pointer hover:underline"
        style={{ color: 'var(--accent)' }}>
        &larr; Back to contacts
      </button>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card className="col-span-2">
          <h2 className="text-lg font-bold mb-4">{data.display_name || jid.replace('@s.whatsapp.net', '')}</h2>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Tone:</span>{' '}
              <Badge>{p.tone || 'unknown'}</Badge>
            </div>
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Relationship:</span>{' '}
              <Badge color="var(--info)">{p.relationship || 'unknown'}</Badge>
            </div>
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Language:</span> {p.language || 'en'}
            </div>
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Emoji usage:</span> {p.emoji_usage || 'unknown'}
            </div>
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Messages analyzed:</span> {data.total_messages_analyzed || 0}
            </div>
            <div>
              <span style={{ color: 'var(--text-dim)' }}>Frequency:</span> {p.interaction_frequency || 'unknown'}
            </div>
          </div>
          {p.summary && (
            <div className="mt-4 text-sm p-3 rounded-lg" style={{ background: 'var(--surface-2)' }}>
              {p.summary}
            </div>
          )}
          {p.topics && p.topics.length > 0 && (
            <div className="mt-3 flex gap-2 flex-wrap">
              {p.topics.map((t, i) => <Badge key={i} color="var(--info)">{t}</Badge>)}
            </div>
          )}
        </Card>

        <Card>
          <div className="text-sm font-medium mb-3">Knowledge Entities</div>
          {(data.knowledge_entities || []).length > 0 ? (
            <div className="space-y-2">
              {data.knowledge_entities.map((e, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span>{e.name}</span>
                  <Badge color={e.entity_type === 'person' ? 'var(--info)' : 'var(--warning)'}>
                    {e.entity_type}
                  </Badge>
                </div>
              ))}
            </div>
          ) : <EmptyState message="No entities yet" />}
        </Card>
      </div>

      <Card>
        <div className="text-sm font-medium mb-3">Recent Messages</div>
        {(data.recent_samples || []).length > 0 ? (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {data.recent_samples.map((s, i) => (
              <div key={i} className={`flex gap-3 text-sm ${s.role === 'user' ? '' : 'flex-row-reverse'}`}>
                <div className={`max-w-md px-3 py-2 rounded-xl ${s.role === 'user' ? 'rounded-bl-none' : 'rounded-br-none'}`}
                  style={{
                    background: s.role === 'user' ? 'var(--surface-2)' : 'var(--accent-dim)',
                    color: s.role === 'user' ? 'var(--text)' : 'var(--accent)',
                  }}>
                  {s.content}
                </div>
              </div>
            ))}
          </div>
        ) : <EmptyState message="No messages sampled yet" />}
      </Card>
    </div>
  );
}

export default function Contacts() {
  const { data, loading } = useAPI(api.contacts, []);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);

  if (selected) return <ContactDetail jid={selected} onBack={() => setSelected(null)} />;

  if (loading) return <Spinner />;

  const contacts = (data?.contacts || []).filter(c => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (c.display_name || '').toLowerCase().includes(s) ||
           (c.push_name || '').toLowerCase().includes(s) ||
           (c.jid || '').includes(s);
  });

  return (
    <div>
      <PageHeader title="Contacts" subtitle={`${contacts.length} contacts with profiles`} />

      <div className="mb-4 relative">
        <Search size={16} className="absolute left-3 top-3" style={{ color: 'var(--text-dim)' }} />
        <input type="text" placeholder="Search contacts..."
          value={search} onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }} />
      </div>

      <Card>
        {contacts.length > 0 ? (
          <div className="space-y-1">
            {contacts.map((c, i) => {
              const p = c.profile || {};
              const name = c.display_name || c.push_name || c.jid?.replace('@s.whatsapp.net', '') || 'Unknown';
              return (
                <div key={i} onClick={() => setSelected(c.jid)}
                  className="flex items-center gap-4 px-3 py-3 rounded-lg cursor-pointer hover:bg-white/3 transition-all">
                  <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold"
                    style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                    {name[0]?.toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{name}</div>
                    <div className="text-xs truncate" style={{ color: 'var(--text-dim)' }}>
                      {p.summary?.slice(0, 60) || c.jid?.replace('@s.whatsapp.net', '')}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {p.tone && <Badge>{p.tone}</Badge>}
                    <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                      {c.total_messages_analyzed || 0} msgs
                    </span>
                    <ChevronRight size={16} style={{ color: 'var(--text-dim)' }} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : <EmptyState message="No contacts found" />}
      </Card>
    </div>
  );
}
