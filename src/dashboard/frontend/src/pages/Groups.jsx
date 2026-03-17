import { useState } from 'react';
import { Users, MessageSquare, RefreshCw, ArrowLeft, Tag, Globe, Clock } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, StatCard, Badge, PageHeader, Spinner, ErrorBox, EmptyState } from '../ui';

function GroupDetail({ jid, onBack }) {
  const { data, loading, error } = useAPI(() => api.groupDetail(jid), [jid]);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <EmptyState message="Group not found" />;

  const g = data.group || data;
  const profile = g.profile || {};
  const topics = profile.topics || [];

  return (
    <div>
      <button onClick={onBack}
        className="flex items-center gap-2 mb-4 text-sm cursor-pointer hover:bg-white/5 px-3 py-1.5 rounded-lg transition-all"
        style={{ color: 'var(--text-dim)' }}>
        <ArrowLeft size={14} /> Back to Groups
      </button>

      <PageHeader title={g.group_name || jid} subtitle={jid} />

      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatCard label="Messages" value={g.total_messages || 0} sub="Total analyzed" />
        <StatCard label="Members" value={g.member_count || '--'} color="var(--info)" sub="In group" />
        <StatCard label="Activity" value={profile.activity_level || 'unknown'} color="var(--warning)" sub="Level" />
      </div>

      {/* Profile */}
      {profile.purpose && (
        <Card className="mb-4">
          <div className="text-sm font-medium mb-3">Purpose</div>
          <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{profile.purpose}</div>
        </Card>
      )}

      {profile.summary && (
        <Card className="mb-4">
          <div className="text-sm font-medium mb-3">Summary</div>
          <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{profile.summary}</div>
        </Card>
      )}

      {/* Topics */}
      {topics.length > 0 && (
        <Card className="mb-4">
          <div className="flex items-center gap-2 mb-3">
            <Tag size={14} style={{ color: 'var(--text-dim)' }} />
            <span className="text-sm font-medium">Topics</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {topics.map((t, i) => <Badge key={i} color="var(--info)">{t}</Badge>)}
          </div>
        </Card>
      )}

      {/* Languages */}
      {profile.languages_used && (
        <Card className="mb-4">
          <div className="flex items-center gap-2 mb-3">
            <Globe size={14} style={{ color: 'var(--text-dim)' }} />
            <span className="text-sm font-medium">Languages</span>
          </div>
          <div className="flex gap-2">
            {profile.languages_used.map((l, i) => <Badge key={i}>{l.toUpperCase()}</Badge>)}
          </div>
        </Card>
      )}

      {/* Response Guidelines */}
      {profile.response_guidelines && (
        <Card className="mb-4">
          <div className="text-sm font-medium mb-3">Response Guidelines</div>
          <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{profile.response_guidelines}</div>
        </Card>
      )}

      {/* Group Dynamics */}
      {profile.group_dynamics && (
        <Card>
          <div className="text-sm font-medium mb-3">Group Dynamics</div>
          <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{profile.group_dynamics}</div>
        </Card>
      )}
    </div>
  );
}

export default function Groups() {
  const { data, loading, error, reload } = useAPI(() => api.groups(100), [], 30000);
  const [selected, setSelected] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState('');

  if (selected) return <GroupDetail jid={selected} onBack={() => setSelected(null)} />;

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

  if (loading) return <><PageHeader title="Groups" subtitle="WhatsApp group management and profiles" /><Spinner /></>;
  if (error) return <><PageHeader title="Groups" /><ErrorBox message={error} /></>;

  const groups = data?.groups || [];
  const filtered = groups.filter(g =>
    (g.group_name || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div>
      <PageHeader title="Groups" subtitle={`${groups.length} WhatsApp groups`}
        action={
          <div className="flex gap-2">
            <button onClick={handleSync} disabled={syncing}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer hover:bg-white/5"
              style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
              <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing...' : 'Sync Groups'}
            </button>
          </div>
        } />

      {/* Search */}
      <div className="mb-4">
        <input type="text" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search groups..."
          className="w-full px-4 py-2.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }} />
      </div>

      {filtered.length === 0 ? <EmptyState message="No groups found" /> : (
        <div className="space-y-2">
          {filtered.map((g) => {
            const profile = g.profile || {};
            const topics = profile.topics || [];
            return (
              <Card key={g.group_jid} className="cursor-pointer hover:bg-white/3 transition-all"
                onClick={() => setSelected(g.group_jid)}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: 'var(--accent-dim)' }}>
                      <Users size={18} style={{ color: 'var(--accent)' }} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{g.group_name || g.group_jid}</div>
                      <div className="text-xs truncate" style={{ color: 'var(--text-dim)' }}>
                        {profile.purpose ? profile.purpose.slice(0, 100) : g.group_jid}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 flex-shrink-0 ml-4">
                    <div className="text-right">
                      <div className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-dim)' }}>
                        <MessageSquare size={12} />
                        <span>{g.total_messages || 0} msgs</span>
                      </div>
                      <div className="flex items-center gap-1 text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>
                        <Clock size={12} />
                        <span>{g.last_active?.slice(0, 10) || '--'}</span>
                      </div>
                    </div>
                    {topics.length > 0 && (
                      <div className="flex gap-1">
                        {topics.slice(0, 2).map((t, i) => (
                          <Badge key={i} color="var(--info)">{t.length > 20 ? t.slice(0, 20) + '...' : t}</Badge>
                        ))}
                        {topics.length > 2 && (
                          <Badge color="var(--text-dim)">+{topics.length - 2}</Badge>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
