import { Users, MessageSquare, Flame, Brain, Target, AlertTriangle } from 'lucide-react';
import { pct } from '../lib/constants';

function KPICard({ icon: Icon, label, value, sub, color = 'text-green-400' }) {
  return (
    <div className="bg-surface rounded-xl border border-border p-4 flex items-start gap-3">
      <div className={`p-2 rounded-lg bg-zinc-900 ${color}`}>
        <Icon size={18} />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-zinc-500 uppercase tracking-wider">{label}</p>
        <p className="text-2xl font-bold text-zinc-100 mt-0.5">{value}</p>
        {sub && <p className="text-xs text-zinc-500 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

export function KPIRow({ stats, mastery, affect }) {
  if (!stats) return null;

  const avgMastery = mastery?.summary?.avg_mastery;
  const conceptsDue = mastery?.summary?.due_now || 0;
  const struggling = mastery?.summary?.struggling || 0;

  // Count non-neutral affect states
  const affectAlerts = affect?.distribution
    ? Object.entries(affect.distribution)
        .filter(([k]) => k === 'frustrated' || k === 'anxious')
        .reduce((sum, [, v]) => sum + v, 0)
    : 0;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <KPICard
        icon={Users}
        label="Students"
        value={stats.total_students}
        sub={`${stats.active_today} active today`}
        color="text-blue-400"
      />
      <KPICard
        icon={MessageSquare}
        label="Messages Sent"
        value={stats.messages_sent}
        sub="all time"
        color="text-cyan-400"
      />
      <KPICard
        icon={Flame}
        label="Avg Streak"
        value={`${stats.avg_streak}d`}
        sub="study days"
        color="text-amber-400"
      />
      <KPICard
        icon={Brain}
        label="Avg Mastery"
        value={avgMastery != null ? `${pct(avgMastery)}%` : '--'}
        sub={`${conceptsDue} due for review`}
        color="text-purple-400"
      />
      <KPICard
        icon={Target}
        label="Concepts"
        value={mastery?.summary?.total_concepts || 0}
        sub={`${mastery?.summary?.mastered || 0} mastered`}
        color="text-green-400"
      />
      <KPICard
        icon={AlertTriangle}
        label="Affect Alerts"
        value={affectAlerts}
        sub={`${struggling} struggling`}
        color={affectAlerts > 0 ? 'text-red-400' : 'text-zinc-400'}
      />
    </div>
  );
}
