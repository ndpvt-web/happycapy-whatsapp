import { useState } from 'react';
import { Users, MessageSquare, RefreshCw, ArrowLeft, Tag, Globe, Clock, BarChart3, User, ChevronDown, ChevronUp, Calendar } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, StatCard, Badge, PageHeader, Spinner, ErrorBox, EmptyState, Tabs } from '../ui';

/* ── Markdown section renderer ── */
function MdSection({ content }) {
  if (!content) return null;
  const lines = content.split('\n');
  const elements = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('### ')) {
      elements.push(<h4 key={i} className="text-sm font-semibold mt-4 mb-2" style={{ color: 'var(--accent)' }}>{line.slice(4)}</h4>);
    } else if (line.startsWith('## ')) {
      elements.push(<h3 key={i} className="text-base font-bold mt-5 mb-2">{line.slice(3)}</h3>);
    } else if (line.startsWith('# ')) {
      // skip top-level headers (title)
    } else if (line.startsWith('| ') && lines[i + 1]?.startsWith('|---')) {
      // Table: collect header + separator + rows
      const headers = line.split('|').filter(Boolean).map(h => h.trim());
      i++; // skip separator
      const rows = [];
      while (i + 1 < lines.length && lines[i + 1].startsWith('|')) {
        i++;
        rows.push(lines[i].split('|').filter(Boolean).map(c => c.trim()));
      }
      elements.push(
        <div key={i} className="overflow-x-auto my-3">
          <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr>{headers.map((h, hi) => (
                <th key={hi} className="text-left py-2 px-2 font-medium" style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-dim)' }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>{rows.map((row, ri) => (
              <tr key={ri} style={{ borderBottom: '1px solid var(--border)' }}>
                {row.map((cell, ci) => (
                  <td key={ci} className="py-2 px-2 text-xs" style={{ color: 'var(--text-dim)' }}>{cell}</td>
                ))}
              </tr>
            ))}</tbody>
          </table>
        </div>
      );
    } else if (line.startsWith('- **') || line.startsWith('- ')) {
      elements.push(
        <div key={i} className="flex gap-2 ml-2 mb-1">
          <span style={{ color: 'var(--accent)' }}>&#8226;</span>
          <span className="text-sm" style={{ color: 'var(--text-dim)' }}
            dangerouslySetInnerHTML={{ __html: line.slice(2).replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text)">$1</strong>') }} />
        </div>
      );
    } else if (line.startsWith('**') && line.endsWith('**')) {
      elements.push(<div key={i} className="text-sm font-semibold mt-3 mb-1">{line.replace(/\*\*/g, '')}</div>);
    } else if (line.match(/^[⚠✅⭐]/)) {
      elements.push(
        <div key={i} className="text-sm mb-1" style={{ color: 'var(--text-dim)' }}
          dangerouslySetInnerHTML={{ __html: line.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text)">$1</strong>') }} />
      );
    } else if (line.startsWith('*Generated')) {
      // skip
    } else if (line.startsWith('---')) {
      elements.push(<hr key={i} className="my-4" style={{ borderColor: 'var(--border)' }} />);
    } else if (line.trim()) {
      elements.push(
        <p key={i} className="text-sm mb-2" style={{ color: 'var(--text-dim)' }}
          dangerouslySetInnerHTML={{ __html: line.replace(/\"([^"<>=]+?)\"/g, '<em>"$1"</em>').replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text)">$1</strong>') }} />
      );
    }
    i++;
  }
  return <div>{elements}</div>;
}

/* ── Member Profile Card ── */
function MemberCard({ member, rank }) {
  const [open, setOpen] = useState(false);
  const colors = ['var(--accent)', 'var(--info)', 'var(--warning)', '#a78bfa', '#f472b6', '#fb923c'];
  const color = colors[rank % colors.length];
  return (
    <Card className="transition-all">
      <div className="flex items-center justify-between cursor-pointer" onClick={() => setOpen(!open)}>
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg flex items-center justify-center text-sm font-bold flex-shrink-0"
            style={{ background: `${color}20`, color }}>
            {rank + 1}
          </div>
          <div>
            <div className="text-sm font-medium">{member.name}</div>
            <div className="text-xs" style={{ color: 'var(--text-dim)' }}>{member.msg_count} messages</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-24 h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
            <div className="h-full rounded-full" style={{ width: `${Math.min(100, member.pct || 0)}%`, background: color }} />
          </div>
          {open ? <ChevronUp size={14} style={{ color: 'var(--text-dim)' }} /> : <ChevronDown size={14} style={{ color: 'var(--text-dim)' }} />}
        </div>
      </div>
      {open && member.profile && (
        <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-dim)' }}
            dangerouslySetInnerHTML={{ __html: member.profile.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text)">$1</strong>') }} />
        </div>
      )}
    </Card>
  );
}

/* ── Activity Bar Chart (pure CSS) ── */
function ActivityChart({ stats }) {
  if (!stats || stats.length === 0) return null;
  const max = Math.max(...stats.map(s => s.msgs));
  const top = stats.slice(0, 15);
  return (
    <div className="space-y-1.5">
      {top.map((s, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="w-28 text-xs truncate text-right" style={{ color: 'var(--text-dim)' }}>{s.name}</div>
          <div className="flex-1 h-5 rounded overflow-hidden" style={{ background: 'var(--surface-2)' }}>
            <div className="h-full rounded flex items-center px-2"
              style={{ width: `${(s.msgs / max) * 100}%`, background: i < 3 ? 'var(--accent)' : 'var(--accent-dim)', minWidth: '2rem' }}>
              <span className="text-xs font-medium" style={{ color: i < 3 ? '#000' : 'var(--accent)' }}>{s.msgs}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Group Detail with Analysis ── */
function GroupDetail({ jid, onBack }) {
  const { data, loading, error } = useAPI(() => api.groupAnalysis(jid), [jid]);
  const [tab, setTab] = useState('overview');
  const [memberSearch, setMemberSearch] = useState('');

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <EmptyState message="No analysis data" />;

  const maxMsgs = Math.max(...(data.member_stats || []).map(s => s.msgs), 1);
  const profiles = (data.member_profiles || []).map(p => ({
    ...p,
    pct: (p.msg_count / maxMsgs) * 100,
  }));
  const filteredProfiles = profiles.filter(p =>
    p.name.toLowerCase().includes(memberSearch.toLowerCase())
  );

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'dynamics', label: 'Dynamics' },
    { id: 'members', label: `Members (${profiles.length})` },
  ];

  return (
    <div>
      <button onClick={onBack}
        className="flex items-center gap-2 mb-4 text-sm cursor-pointer hover:bg-white/5 px-3 py-1.5 rounded-lg transition-all"
        style={{ color: 'var(--text-dim)' }}>
        <ArrowLeft size={14} /> Back to Groups
      </button>

      <PageHeader title={data.group_name} subtitle={data.group_jid} />

      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="Total Messages" value={data.total_messages?.toLocaleString()} sub={data.date_range} />
        <StatCard label="Unique Senders" value={data.unique_senders} color="var(--info)" sub="Active members" />
        <StatCard label="Member Profiles" value={profiles.length} color="var(--warning)" sub="AI-analyzed" />
        <StatCard label="Avg / Member" value={data.unique_senders ? Math.round(data.total_messages / data.unique_senders) : 0} color="#a78bfa" sub="Messages per member" />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {/* Overview Tab */}
      {tab === 'overview' && (
        <div className="grid grid-cols-2 gap-4">
          <Card>
            <div className="flex items-center gap-2 mb-4">
              <BarChart3 size={16} style={{ color: 'var(--accent)' }} />
              <span className="text-sm font-semibold">Top Contributors</span>
            </div>
            <ActivityChart stats={data.member_stats} />
          </Card>
          <div className="space-y-4">
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <Calendar size={16} style={{ color: 'var(--info)' }} />
                <span className="text-sm font-semibold">Activity Range</span>
              </div>
              <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{data.date_range || 'N/A'}</div>
            </Card>
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <Users size={16} style={{ color: 'var(--warning)' }} />
                <span className="text-sm font-semibold">Member Tiers</span>
              </div>
              <div className="space-y-2">
                {[
                  { label: 'Power Users (300+)', count: data.member_stats?.filter(s => s.msgs >= 300).length || 0, color: 'var(--accent)' },
                  { label: 'Active (100-299)', count: data.member_stats?.filter(s => s.msgs >= 100 && s.msgs < 300).length || 0, color: 'var(--info)' },
                  { label: 'Moderate (30-99)', count: data.member_stats?.filter(s => s.msgs >= 30 && s.msgs < 100).length || 0, color: 'var(--warning)' },
                  { label: 'Observers (<30)', count: data.member_stats?.filter(s => s.msgs < 30).length || 0, color: 'var(--text-dim)' },
                ].map(tier => (
                  <div key={tier.label} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2.5 h-2.5 rounded-full" style={{ background: tier.color }} />
                      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{tier.label}</span>
                    </div>
                    <span className="text-sm font-semibold">{tier.count}</span>
                  </div>
                ))}
              </div>
            </Card>
            {data.has_dynamics && (
              <Card className="cursor-pointer hover:bg-white/3" onClick={() => setTab('dynamics')}>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-semibold">Full Dynamics Analysis</div>
                    <div className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>Group identity, culture, social dynamics</div>
                  </div>
                  <Badge color="var(--accent)">View</Badge>
                </div>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* Dynamics Tab */}
      {tab === 'dynamics' && (
        <Card>
          {data.has_dynamics ? (
            <MdSection content={data.dynamics} />
          ) : (
            <EmptyState message="No dynamics analysis available for this group" />
          )}
        </Card>
      )}

      {/* Members Tab */}
      {tab === 'members' && (
        <div>
          <div className="mb-4">
            <input type="text" value={memberSearch} onChange={e => setMemberSearch(e.target.value)}
              placeholder="Search members..."
              className="w-full px-4 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }} />
          </div>
          {filteredProfiles.length === 0 ? (
            <EmptyState message="No member profiles found" />
          ) : (
            <div className="space-y-2">
              {filteredProfiles.map((m, i) => (
                <MemberCard key={m.name} member={m} rank={i} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Main Groups List ── */
export default function Groups() {
  const { data, loading, error, reload } = useAPI(() => api.groups(100), [], 30000);
  const [selected, setSelected] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState('');

  if (selected) return <GroupDetail jid={selected} onBack={() => setSelected(null)} />;

  async function handleSync() {
    setSyncing(true);
    try { await api.syncGroups(); reload(); } catch (e) { /* silent */ } finally { setSyncing(false); }
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
                        {topics.length > 2 && <Badge color="var(--text-dim)">+{topics.length - 2}</Badge>}
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
