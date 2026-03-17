import { useState } from 'react';
import { Send, Radio, Users, UsersRound, Clock, CheckCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, StatCard, Badge, PageHeader, Spinner, ErrorBox, EmptyState, Tabs } from '../ui';

function CampaignHistory() {
  const { data, loading, error } = useAPI(api.campaigns, [], 30000);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;

  const campaigns = data?.campaigns || [];
  if (campaigns.length === 0) return <EmptyState message="No broadcast campaigns yet" />;

  return (
    <div className="space-y-2">
      {campaigns.map((c, i) => (
        <Card key={i}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Radio size={14} style={{ color: 'var(--accent)' }} />
              <span className="text-sm font-medium">{c.name || `Campaign #${i + 1}`}</span>
              <Badge color={c.status === 'completed' ? 'var(--accent)' : c.status === 'failed' ? 'var(--danger)' : 'var(--warning)'}>
                {c.status || 'unknown'}
              </Badge>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
              {c.created_at || c.sent_at || '--'}
            </span>
          </div>
          <div className="text-sm mb-2" style={{ color: 'var(--text-dim)' }}>
            {c.message?.slice(0, 200) || '(no message preview)'}
          </div>
          <div className="flex gap-4 text-xs" style={{ color: 'var(--text-dim)' }}>
            <span className="flex items-center gap-1">
              <Users size={12} /> {c.recipient_count || c.recipients?.length || 0} recipients
            </span>
            {c.sent_count !== undefined && (
              <span className="flex items-center gap-1">
                <CheckCircle size={12} style={{ color: 'var(--accent)' }} /> {c.sent_count} sent
              </span>
            )}
            {c.failed_count > 0 && (
              <span className="flex items-center gap-1">
                <AlertTriangle size={12} style={{ color: 'var(--danger)' }} /> {c.failed_count} failed
              </span>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

const FILTER_TABS = [
  { id: 'all', label: 'All' },
  { id: 'contacts', label: 'Contacts' },
  { id: 'groups', label: 'Groups' },
];

function NewBroadcast({ onSent }) {
  const { data, loading, error, reload } = useAPI(api.broadcastContacts, []);
  const [message, setMessage] = useState('');
  const [selectedRecipients, setSelectedRecipients] = useState(new Set());
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [syncing, setSyncing] = useState(false);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;

  const contacts = (data?.contacts || []).map(c => ({ ...c, _type: 'contact' }));
  const groups = (data?.groups || []).map(g => ({ ...g, _type: 'group' }));
  const allRecipients = [...contacts, ...groups];

  // Filter by type
  const typeFiltered = filter === 'contacts' ? contacts
    : filter === 'groups' ? groups
    : allRecipients;

  // Filter by search
  const filtered = typeFiltered.filter(c =>
    (c.name || c.jid || '').toLowerCase().includes(search.toLowerCase())
  );

  function toggleRecipient(jid) {
    const next = new Set(selectedRecipients);
    if (next.has(jid)) next.delete(jid);
    else next.add(jid);
    setSelectedRecipients(next);
  }

  function selectAll() {
    const allFilteredJids = filtered.map(c => c.jid);
    const allSelected = allFilteredJids.every(j => selectedRecipients.has(j));
    if (allSelected) {
      const next = new Set(selectedRecipients);
      allFilteredJids.forEach(j => next.delete(j));
      setSelectedRecipients(next);
    } else {
      const next = new Set(selectedRecipients);
      allFilteredJids.forEach(j => next.add(j));
      setSelectedRecipients(next);
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      await api.syncGroups();
      reload();
    } catch (e) {
      // silent
    } finally {
      setSyncing(false);
    }
  }

  async function handleSend() {
    if (!message.trim()) return;
    if (selectedRecipients.size === 0) return;
    setSending(true);
    setResult(null);
    try {
      await api.sendBroadcast(message, Array.from(selectedRecipients));
      setResult({ ok: true, msg: `Broadcast sent to ${selectedRecipients.size} recipients` });
      setMessage('');
      setSelectedRecipients(new Set());
      onSent?.();
    } catch (e) {
      setResult({ ok: false, msg: e.message });
    } finally {
      setSending(false);
    }
  }

  // Count selected by type
  const selectedContacts = contacts.filter(c => selectedRecipients.has(c.jid)).length;
  const selectedGroups = groups.filter(g => selectedRecipients.has(g.jid)).length;

  return (
    <div>
      {result && (
        <div className="mb-4 px-4 py-3 rounded-lg text-sm"
          style={result.ok
            ? { background: 'rgba(34,197,94,0.1)', color: 'var(--accent)', border: '1px solid rgba(34,197,94,0.2)' }
            : { background: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.2)' }}>
          {result.msg}
        </div>
      )}

      {/* Message */}
      <Card className="mb-4">
        <div className="text-sm font-medium mb-3">Message</div>
        <textarea value={message} onChange={e => setMessage(e.target.value)}
          placeholder="Type your broadcast message..."
          rows={4}
          className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />
        <div className="text-xs mt-2" style={{ color: 'var(--text-dim)' }}>
          {message.length} characters
        </div>
      </Card>

      {/* Recipients */}
      <Card className="mb-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-medium">
            Recipients ({selectedRecipients.size} selected
            {selectedContacts > 0 && selectedGroups > 0
              ? ` \u2014 ${selectedContacts} contacts, ${selectedGroups} groups`
              : selectedContacts > 0 ? ` \u2014 ${selectedContacts} contacts`
              : selectedGroups > 0 ? ` \u2014 ${selectedGroups} groups`
              : ''})
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleSync} disabled={syncing}
              className="flex items-center gap-1 text-xs px-2 py-1 rounded cursor-pointer hover:bg-white/5 transition-all"
              style={{ color: 'var(--text-dim)' }}>
              <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing...' : 'Sync Groups'}
            </button>
            <button onClick={selectAll}
              className="text-xs px-2 py-1 rounded cursor-pointer hover:bg-white/5 transition-all"
              style={{ color: 'var(--accent)' }}>
              {filtered.length > 0 && filtered.every(c => selectedRecipients.has(c.jid)) ? 'Deselect All' : 'Select All'}
            </button>
          </div>
        </div>

        {/* Filter tabs: All / Contacts / Groups */}
        <div className="flex gap-1 mb-3 p-1 rounded-lg" style={{ background: 'var(--surface-2)' }}>
          {FILTER_TABS.map(t => {
            const count = t.id === 'all' ? allRecipients.length
              : t.id === 'contacts' ? contacts.length
              : groups.length;
            return (
              <button key={t.id}
                onClick={() => setFilter(t.id)}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-xs font-medium transition-all cursor-pointer"
                style={filter === t.id
                  ? { background: 'var(--surface)', color: 'var(--text)', boxShadow: '0 1px 3px rgba(0,0,0,0.2)' }
                  : { color: 'var(--text-dim)' }}>
                {t.id === 'contacts' && <Users size={12} />}
                {t.id === 'groups' && <UsersRound size={12} />}
                {t.label} ({count})
              </button>
            );
          })}
        </div>

        <input type="text" value={search} onChange={e => setSearch(e.target.value)}
          placeholder={filter === 'groups' ? 'Search groups...' : filter === 'contacts' ? 'Search contacts...' : 'Search contacts & groups...'}
          className="w-full px-3 py-2 rounded-lg text-sm outline-none mb-3"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />

        <div className="max-h-80 overflow-y-auto space-y-1">
          {filtered.map(c => (
            <div key={c.jid}
              onClick={() => toggleRecipient(c.jid)}
              className="flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-all hover:bg-white/3"
              style={selectedRecipients.has(c.jid)
                ? { background: 'var(--accent-dim)', border: '1px solid rgba(34,197,94,0.3)' }
                : { border: '1px solid transparent' }}>
              <div className="w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0"
                style={selectedRecipients.has(c.jid)
                  ? { borderColor: 'var(--accent)', background: 'var(--accent)' }
                  : { borderColor: 'var(--border)' }}>
                {selectedRecipients.has(c.jid) && (
                  <CheckCircle size={12} style={{ color: '#fff' }} />
                )}
              </div>
              <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ background: c._type === 'group' ? 'rgba(59,130,246,0.15)' : 'var(--accent-dim)' }}>
                {c._type === 'group'
                  ? <UsersRound size={14} style={{ color: 'var(--info)' }} />
                  : <Users size={14} style={{ color: 'var(--accent)' }} />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm truncate">{c.name || c.jid}</div>
                <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
                  {c._type === 'group'
                    ? `${c.member_count || 0} members`
                    : `${c.messages || 0} messages`}
                </div>
              </div>
              <Badge color={c._type === 'group' ? 'var(--info)' : 'var(--accent)'}>
                {c._type === 'group' ? 'Group' : 'Contact'}
              </Badge>
            </div>
          ))}
          {filtered.length === 0 && <EmptyState message={filter === 'groups' ? 'No groups found. Try syncing groups first.' : 'No recipients found'} />}
        </div>
      </Card>

      {/* Send */}
      <button onClick={handleSend}
        disabled={sending || !message.trim() || selectedRecipients.size === 0}
        className="flex items-center gap-2 px-6 py-3 rounded-lg text-sm font-medium transition-all cursor-pointer"
        style={{
          background: (message.trim() && selectedRecipients.size > 0) ? 'var(--accent)' : 'var(--surface-2)',
          color: (message.trim() && selectedRecipients.size > 0) ? '#000' : 'var(--text-dim)',
          opacity: sending ? 0.6 : 1,
        }}>
        <Send size={14} />
        {sending ? 'Sending...' : `Send to ${selectedRecipients.size} recipients`}
      </button>
    </div>
  );
}

export default function Broadcast() {
  const [tab, setTab] = useState('history');
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div>
      <PageHeader title="Broadcast" subtitle="Send messages to multiple contacts and groups at once" />
      <Tabs active={tab} onChange={setTab} tabs={[
        { id: 'history', label: 'Campaign History' },
        { id: 'new', label: 'New Broadcast' },
      ]} />
      {tab === 'history'
        ? <CampaignHistory key={refreshKey} />
        : <NewBroadcast onSent={() => { setTab('history'); setRefreshKey(k => k + 1); }} />
      }
    </div>
  );
}
