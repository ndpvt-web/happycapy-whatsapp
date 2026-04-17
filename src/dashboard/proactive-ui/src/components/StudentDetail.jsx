import { useState, useEffect } from 'react';
import {
  User, BookOpen, Brain, Heart, MessageSquare,
  Clock, Flame, Calendar, Target, TrendingUp,
} from 'lucide-react';
import { api } from '../lib/api';
import { AFFECT_COLORS, TYPE_COLORS, TYPE_LABELS, formatJid, pct } from '../lib/constants';

function Section({ icon: Icon, title, children, color = 'text-green-400' }) {
  return (
    <div className="bg-surface rounded-xl border border-border overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <Icon size={16} className={color} />
        <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-zinc-800/50 last:border-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-xs text-zinc-200 font-medium">{value || '--'}</span>
    </div>
  );
}

function MasteryBar({ concept }) {
  const level = concept.mastery_level || 0;
  const color = level >= 0.8 ? '#22c55e' : level >= 0.5 ? '#f59e0b' : '#ef4444';
  return (
    <div className="py-2 border-b border-zinc-800/50 last:border-0">
      <div className="flex items-center justify-between mb-1">
        <div className="min-w-0">
          <span className="text-xs text-zinc-200 font-medium">{concept.topic}</span>
          <span className="text-xs text-zinc-600 ml-2">{concept.subject}</span>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs font-mono text-zinc-300">{pct(level)}%</span>
          <span className="text-xs text-zinc-600">EF {(concept.ease_factor || 2.5).toFixed(1)}</span>
          <span className="text-xs text-zinc-600">R{concept.repetition_count || 0}</span>
        </div>
      </div>
      <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct(level)}%`, background: color }}
        />
      </div>
      {concept.next_review_date && (
        <p className="text-xs text-zinc-600 mt-0.5">
          Next review: {concept.next_review_date}
        </p>
      )}
    </div>
  );
}

const SUBJECT_COLORS = {
  Maths: '#a855f7', Physics: '#3b82f6', Chemistry: '#22c55e',
  English: '#f59e0b', Urdu: '#ec4899', Hindi: '#ef4444',
  Biology: '#14b8a6', History: '#f97316', Geography: '#06b6d4',
};

export function StudentDetail({ jid, onBack }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.studentFull(jid)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [jid]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-500">Loading student profile...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-500">Student not found</div>
      </div>
    );
  }

  const { plan, mastery, effectiveness, logs, progress } = data;
  const affectCfg = AFFECT_COLORS[plan.recent_affect] || AFFECT_COLORS.neutral;
  const name = plan.display_name || formatJid(jid);

  // Compute mastery by subject
  const subjectMap = {};
  mastery.forEach((c) => {
    if (!subjectMap[c.subject]) subjectMap[c.subject] = { total: 0, sum: 0 };
    subjectMap[c.subject].total++;
    subjectMap[c.subject].sum += c.mastery_level || 0;
  });
  const subjectData = Object.entries(subjectMap).map(([subj, { total, sum }]) => ({
    subject: subj,
    avg: total > 0 ? sum / total : 0,
    count: total,
    color: SUBJECT_COLORS[subj] || '#8b5cf6',
  })).sort((a, b) => b.avg - a.avg);

  // Effectiveness by type
  const effByType = {};
  effectiveness.forEach((e) => {
    if (!effByType[e.message_type]) effByType[e.message_type] = { total: 0, responded: 0 };
    effByType[e.message_type].total++;
    if (e.response_received) effByType[e.message_type].responded++;
  });
  const effData = Object.entries(effByType).map(([type, d]) => ({
    type,
    label: TYPE_LABELS[type] || type,
    rate: d.total > 0 ? Math.round((d.responded / d.total) * 100) : 0,
    total: d.total,
    responded: d.responded,
    color: TYPE_COLORS[type] || '#71717a',
  })).sort((a, b) => b.rate - a.rate);

  return (
    <div className="space-y-6 pt-6">
      {/* Student header */}
      <div className="bg-surface rounded-xl border border-border p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-full bg-zinc-800 flex items-center justify-center">
              <User size={24} className="text-zinc-500" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-zinc-100">{name}</h2>
              <p className="text-sm text-zinc-500">{formatJid(jid)}</p>
              <div className="flex items-center gap-3 mt-2">
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${affectCfg.bg} ${affectCfg.text}`}>
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: affectCfg.dot }} />
                  {affectCfg.label}
                </span>
                {plan.board && (
                  <span className="text-xs text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded">
                    {plan.board} {plan.class}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-6 text-center">
            <div>
              <Flame size={16} className="mx-auto text-amber-400 mb-1" />
              <p className="text-xl font-bold text-zinc-100">{plan.current_streak || 0}</p>
              <p className="text-xs text-zinc-500">streak</p>
            </div>
            <div>
              <Target size={16} className="mx-auto text-purple-400 mb-1" />
              <p className="text-xl font-bold text-zinc-100">{mastery.length}</p>
              <p className="text-xs text-zinc-500">concepts</p>
            </div>
            <div>
              <TrendingUp size={16} className="mx-auto text-green-400 mb-1" />
              <p className="text-xl font-bold text-zinc-100">{plan.engagement_score || 0}%</p>
              <p className="text-xs text-zinc-500">engagement</p>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Plan details */}
        <Section icon={BookOpen} title="Study Plan" color="text-blue-400">
          <InfoRow label="Study Time" value={plan.study_time} />
          <InfoRow label="Daily Target" value={`${plan.daily_target_hours || 2}h`} />
          <InfoRow label="Exam Date" value={plan.exam_date} />
          <InfoRow label="Focus Subjects" value={
            (() => {
              try {
                const parsed = typeof plan.focus_subjects === 'string'
                  ? JSON.parse(plan.focus_subjects)
                  : plan.focus_subjects;
                return Array.isArray(parsed) ? parsed.join(', ') : plan.focus_subjects || '--';
              } catch { return plan.focus_subjects || '--'; }
            })()
          } />
          <InfoRow label="Timezone" value={plan.timezone} />
          <InfoRow label="Preferred Hour" value={
            plan.preferred_send_hour >= 0 ? `${plan.preferred_send_hour}:00` : 'Auto'
          } />
          <InfoRow label="Max Daily Msgs" value={plan.max_daily_messages} />
          <InfoRow label="Longest Streak" value={`${plan.longest_streak || 0} days`} />
        </Section>

        {/* Mastery profile - CSS bars */}
        <Section icon={Brain} title="Mastery Profile" color="text-purple-400">
          {subjectData.length > 0 ? (
            <div className="space-y-3">
              {subjectData.map((d) => {
                const val = Math.round(d.avg * 100);
                return (
                  <div key={d.subject}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-zinc-300 font-medium">{d.subject}</span>
                      <span className="text-xs text-zinc-500">
                        {val}% <span className="text-zinc-600">({d.count})</span>
                      </span>
                    </div>
                    <div className="h-4 bg-zinc-800 rounded overflow-hidden">
                      <div
                        className="h-full rounded"
                        style={{
                          width: `${val}%`,
                          background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`,
                          minWidth: val > 0 ? '6px' : '0',
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-zinc-600 text-center py-8">No concepts tracked yet</p>
          )}
        </Section>
      </div>

      {/* Concept mastery detail */}
      {mastery.length > 0 && (
        <Section icon={Target} title={`Concepts (${mastery.length})`} color="text-green-400">
          <div className="max-h-72 overflow-y-auto">
            {mastery.map((c) => (
              <MasteryBar key={c.id || c.concept_id} concept={c} />
            ))}
          </div>
        </Section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Response effectiveness - CSS bars */}
        <Section icon={MessageSquare} title="Response to Messages" color="text-cyan-400">
          {effData.length > 0 ? (
            <div className="space-y-2">
              {effData.map((d) => (
                <div key={d.type} className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400 w-20 text-right flex-shrink-0 truncate">
                    {d.label}
                  </span>
                  <div className="flex-1 h-3 bg-zinc-800 rounded overflow-hidden">
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${d.rate}%`,
                        background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`,
                        minWidth: d.rate > 0 ? '4px' : '0',
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-zinc-300 w-10 text-right flex-shrink-0">
                    {d.rate}%
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-zinc-600 text-center py-8">No effectiveness data yet</p>
          )}
        </Section>

        {/* Recent proactive log */}
        <Section icon={Clock} title="Recent Messages" color="text-amber-400">
          <div className="max-h-64 overflow-y-auto space-y-2">
            {logs.length > 0 ? logs.slice(0, 15).map((l) => (
              <div key={l.id} className="py-1.5 border-b border-zinc-800/50 last:border-0">
                <div className="flex items-center justify-between mb-0.5">
                  <span
                    className="text-xs font-medium px-1.5 py-0.5 rounded"
                    style={{
                      color: TYPE_COLORS[l.message_type] || '#71717a',
                      background: `${TYPE_COLORS[l.message_type] || '#71717a'}15`,
                    }}
                  >
                    {TYPE_LABELS[l.message_type] || l.message_type}
                  </span>
                  <span className="text-xs text-zinc-600">{l.sent_at}</span>
                </div>
                <p className="text-xs text-zinc-400 line-clamp-2">{l.message_text}</p>
              </div>
            )) : (
              <p className="text-xs text-zinc-600 text-center py-8">No messages sent yet</p>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
