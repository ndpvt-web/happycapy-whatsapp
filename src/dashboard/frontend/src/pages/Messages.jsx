import { useState } from 'react';
import { MessageSquare, AlertOctagon, Clock, CheckCircle } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, Badge, PageHeader, Spinner, EmptyState, Tabs } from '../ui';

function QueueView() {
  const { data, loading } = useAPI(() => api.queue(null, 100), [], 15000);

  if (loading) return <Spinner />;
  const msgs = data?.messages || [];

  const statusColors = {
    pending: 'var(--warning)',
    replied: 'var(--accent)',
    escalated: 'var(--danger)',
    deferred: 'var(--info)',
    ignored: 'var(--text-dim)',
  };

  return msgs.length > 0 ? (
    <div className="space-y-2">
      {msgs.map((m, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 rounded-lg"
          style={{ background: 'var(--surface-2)' }}>
          <div className="flex-shrink-0">
            {m.status === 'pending' && <Clock size={16} style={{ color: 'var(--warning)' }} />}
            {m.status === 'replied' && <CheckCircle size={16} style={{ color: 'var(--accent)' }} />}
            {m.status === 'escalated' && <AlertOctagon size={16} style={{ color: 'var(--danger)' }} />}
            {!['pending', 'replied', 'escalated'].includes(m.status) &&
              <MessageSquare size={16} style={{ color: 'var(--text-dim)' }} />}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{m.sender_name || m.sender_id?.replace('@s.whatsapp.net', '')}</span>
              <Badge color={statusColors[m.status] || 'var(--text-dim)'}>{m.status}</Badge>
              {m.priority !== 'normal' && (
                <Badge color={m.priority === 'urgent' ? 'var(--danger)' : 'var(--warning)'}>{m.priority}</Badge>
              )}
            </div>
            <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-dim)' }}>
              {m.content_preview || '(no preview)'}
            </div>
          </div>
          <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-dim)' }}>
            <span>Score: {m.importance_score}/10</span>
            <span>{m.created_at?.slice(11, 16)}</span>
          </div>
        </div>
      ))}
    </div>
  ) : <EmptyState message="Message queue is empty" />;
}

function EscalationView() {
  const { data, loading } = useAPI(api.escalations, [], 15000);

  if (loading) return <Spinner />;
  const escs = data?.escalations || [];

  return escs.length > 0 ? (
    <div className="space-y-2">
      {escs.map((e, i) => (
        <Card key={i}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-mono font-bold" style={{ color: 'var(--danger)' }}>{e.code}</span>
              <Badge color={e.status === 'pending' ? 'var(--danger)' : 'var(--accent)'}>{e.status}</Badge>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{e.created_at}</span>
          </div>
          <div className="text-sm mb-1">{e.question_preview}</div>
          <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
            From: {e.sender_name || e.sender_id?.replace('@s.whatsapp.net', '')}
          </div>
          {e.admin_response && (
            <div className="mt-2 p-2 rounded-lg text-sm" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
              Response: {e.admin_response}
            </div>
          )}
        </Card>
      ))}
    </div>
  ) : <EmptyState message="No escalations" />;
}

export default function Messages() {
  const [tab, setTab] = useState('queue');

  return (
    <div>
      <PageHeader title="Messages" subtitle="Message queue, escalations, and routing" />
      <Tabs active={tab} onChange={setTab} tabs={[
        { id: 'queue', label: 'Message Queue' },
        { id: 'escalations', label: 'Escalations' },
      ]} />
      {tab === 'queue' ? <QueueView /> : <EscalationView />}
    </div>
  );
}
