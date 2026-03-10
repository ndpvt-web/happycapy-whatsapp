import { useState } from 'react';
import { Activity, MessageSquare, Users, AlertTriangle, Clock, Zap, RefreshCw } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, StatCard, Badge, PageHeader, Spinner, ErrorBox, EmptyState } from '../ui';

const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

function formatUptime(s) {
  if (!s) return '--';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function StatusDot({ ok }) {
  return (
    <span className="inline-block w-2.5 h-2.5 rounded-full mr-2"
      style={{ background: ok ? 'var(--accent)' : 'var(--danger)' }} />
  );
}

export default function Overview() {
  const { data: health, loading: hl, reload: reloadHealth } = useAPI(api.health, [], 10000);
  const { data: analytics, loading: al } = useAPI(api.analytics, [], 30000);
  const { data: config } = useAPI(api.config, []);

  if (hl && al) return <Spinner />;

  const h = health || {};
  const a = analytics || {};

  const queueMap = {};
  (a.queue_stats || []).forEach(s => { queueMap[s.status] = s.cnt; });
  const escMap = {};
  (a.escalation_stats || []).forEach(s => { escMap[s.status] = s.cnt; });

  const chartData = (a.messages_per_day || []).map(d => ({
    day: d.day?.slice(5) || '',
    messages: d.cnt,
  }));

  const typeData = (a.events_by_type || []).slice(0, 6).map((e, i) => ({
    name: e.event_type?.replace(/_/g, ' ') || 'unknown',
    value: e.cnt,
    color: COLORS[i % COLORS.length],
  }));

  const dirData = (a.messages_by_direction || []).map(d => ({
    name: d.direction === 'in' ? 'Received' : 'Sent',
    value: d.cnt,
  }));

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Real-time WhatsApp bot overview"
        action={
          <button onClick={reloadHealth}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer hover:bg-white/5"
            style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
            <RefreshCw size={14} /> Refresh
          </button>
        } />

      {/* Status Bar */}
      <Card className="mb-6">
        <div className="flex items-center gap-8 text-sm">
          <div className="flex items-center">
            <StatusDot ok={h.running} />
            <span className="font-medium">{h.running ? 'Bot Running' : 'Bot Stopped'}</span>
          </div>
          <div className="flex items-center">
            <StatusDot ok={h.whatsapp_authenticated} />
            <span>WhatsApp {h.whatsapp_authenticated ? 'Connected' : 'Disconnected'}</span>
          </div>
          <div style={{ color: 'var(--text-dim)' }}>
            <Clock size={14} className="inline mr-1" />
            Uptime: {formatUptime(h.uptime_seconds)}
          </div>
          {h.errors_last_hour > 0 && (
            <div style={{ color: 'var(--danger)' }}>
              <AlertTriangle size={14} className="inline mr-1" />
              {h.errors_last_hour} errors (1h)
            </div>
          )}
          {config && (
            <div style={{ color: 'var(--text-dim)' }}>
              Mode: <Badge>{config.mode?.replace(/_/g, ' ')}</Badge>
            </div>
          )}
        </div>
      </Card>

      {/* Stat Cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="Total Events" value={a.total_events?.toLocaleString() || '0'}
          sub="All time" />
        <StatCard label="Active Sessions" value={a.active_sessions || 0}
          sub="Current" color="var(--info)" />
        <StatCard label="Queue Pending" value={queueMap.pending || 0}
          sub={`${queueMap.replied || 0} replied`} color="var(--warning)" />
        <StatCard label="Escalations" value={escMap.pending || 0}
          sub={`${escMap.answered || 0} resolved`} color="var(--danger)" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <Card>
          <div className="text-sm font-medium mb-4">Messages (7 days)</div>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chartData}>
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#1a1a1f', border: '1px solid #27272a', borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: '#fafafa' }}
                />
                <Bar dataKey="messages" fill="#22c55e" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState message="No message data yet" />}
        </Card>

        <Card>
          <div className="text-sm font-medium mb-4">Event Types</div>
          {typeData.length > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="50%" height={200}>
                <PieChart>
                  <Pie data={typeData} dataKey="value" cx="50%" cy="50%"
                    innerRadius={50} outerRadius={80} paddingAngle={2}>
                    {typeData.map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: '#1a1a1f', border: '1px solid #27272a', borderRadius: 8, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2">
                {typeData.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: e.color }} />
                    <span style={{ color: 'var(--text-dim)' }}>{e.name}</span>
                    <span className="font-medium ml-auto">{e.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : <EmptyState message="No events yet" />}
        </Card>
      </div>

      {/* Top Contacts */}
      <Card>
        <div className="text-sm font-medium mb-4">Top Contacts (7 days)</div>
        {(a.top_contacts || []).length > 0 ? (
          <div className="space-y-2">
            {(a.top_contacts || []).map((c, i) => {
              const max = a.top_contacts[0]?.cnt || 1;
              return (
                <div key={i} className="flex items-center gap-3">
                  <div className="w-36 text-xs truncate" style={{ color: 'var(--text-dim)' }}>
                    {c.display_name || c.chat_id?.replace('@s.whatsapp.net', '').replace(/@.*$/, '') || 'Unknown'}
                  </div>
                  <div className="flex-1 h-5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                    <div className="h-full rounded-full transition-all"
                      style={{ width: `${(c.cnt / max) * 100}%`, background: COLORS[i % COLORS.length] }} />
                  </div>
                  <div className="text-xs font-medium w-10 text-right">{c.cnt}</div>
                </div>
              );
            })}
          </div>
        ) : <EmptyState message="No contact activity yet" />}
      </Card>
    </div>
  );
}
