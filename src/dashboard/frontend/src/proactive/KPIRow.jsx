import { Users, MessageSquare, Flame, Brain, Target, AlertTriangle } from 'lucide-react';
import { pct } from './constants';

function KPICard({ icon: Icon, label, value, sub, color = 'var(--accent)' }) {
  return (
    <div className="rounded-xl p-4 flex items-start gap-3"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div className="p-2 rounded-lg" style={{ background: 'var(--bg)', color }}>
        <Icon size={18} />
      </div>
      <div className="min-w-0">
        <p className="text-xs uppercase tracking-wider" style={{ color: 'var(--text-dim)' }}>{label}</p>
        <p className="text-2xl font-bold mt-0.5" style={{ color }}>{value}</p>
        {sub && <p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{sub}</p>}
      </div>
    </div>
  );
}

export function KPIRow({ stats, mastery, affect }) {
  if (!stats) return null;

  const avgMastery = mastery?.summary?.avg_mastery;
  const conceptsDue = mastery?.summary?.due_now || 0;
  const struggling = mastery?.summary?.struggling || 0;

  const affectAlerts = affect?.distribution
    ? Object.entries(affect.distribution)
        .filter(([k]) => k === 'frustrated' || k === 'anxious')
        .reduce((sum, [, v]) => sum + v, 0)
    : 0;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <KPICard icon={Users} label="Students" value={stats.total_students}
        sub={`${stats.active_today} active today`} color="var(--info)" />
      <KPICard icon={MessageSquare} label="Messages Sent" value={stats.messages_sent}
        sub="all time" color="#06b6d4" />
      <KPICard icon={Flame} label="Avg Streak" value={`${stats.avg_streak}d`}
        sub="study days" color="var(--warning)" />
      <KPICard icon={Brain} label="Avg Mastery"
        value={avgMastery != null ? `${pct(avgMastery)}%` : '--'}
        sub={`${conceptsDue} due for review`} color="#a855f7" />
      <KPICard icon={Target} label="Concepts"
        value={mastery?.summary?.total_concepts || 0}
        sub={`${mastery?.summary?.mastered || 0} mastered`} color="var(--accent)" />
      <KPICard icon={AlertTriangle} label="Affect Alerts" value={affectAlerts}
        sub={`${struggling} struggling`}
        color={affectAlerts > 0 ? 'var(--danger)' : 'var(--text-dim)'} />
    </div>
  );
}
