import { useState, useEffect } from 'react';
import {
  User, BookOpen, Brain, MessageSquare,
  Clock, Flame, Target, TrendingUp,
} from 'lucide-react';
import { proactiveApi } from './api';
import { AFFECT_COLORS, TYPE_COLORS, TYPE_LABELS, formatJid, pct } from './constants';

function Section({ icon: Icon, title, children, color = 'var(--accent)' }) {
  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div className="px-4 py-3 flex items-center gap-2"
        style={{ borderBottom: '1px solid var(--border)' }}>
        <Icon size={16} style={{ color }} />
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className="flex justify-between py-1.5 last:border-0"
      style={{ borderBottom: '1px solid rgba(39,39,42,0.5)' }}>
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{label}</span>
      <span className="text-xs font-medium">{value || '--'}</span>
    </div>
  );
}

function MasteryBar({ concept }) {
  const level = concept.mastery_level || 0;
  const color = level >= 0.8 ? 'var(--accent)' : level >= 0.5 ? 'var(--warning)' : 'var(--danger)';
  return (
    <div className="py-2 last:border-0" style={{ borderBottom: '1px solid rgba(39,39,42,0.5)' }}>
      <div className="flex items-center justify-between mb-1">
        <div className="min-w-0">
          <span className="text-xs font-medium">{concept.topic}</span>
          <span className="text-xs ml-2" style={{ color: 'var(--text-dim)' }}>{concept.subject}</span>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs font-mono">{pct(level)}%</span>
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>EF {(concept.ease_factor || 2.5).toFixed(1)}</span>
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>R{concept.repetition_count || 0}</span>
        </div>
      </div>
      <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div className="h-full rounded-full" style={{ width: `${pct(level)}%`, background: color }} />
      </div>
      {concept.next_review_date && (
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>
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
    proactiveApi.studentFull(jid)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [jid]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div style={{ color: 'var(--text-dim)' }}>Loading student profile...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div style={{ color: 'var(--text-dim)' }}>Student not found</div>
      </div>
    );
  }

  const { plan, mastery, effectiveness, logs } = data;
  const affectCfg = AFFECT_COLORS[plan.recent_affect] || AFFECT_COLORS.neutral;
  const name = plan.display_name || formatJid(jid);

  const subjectMap = {};
  mastery.forEach((c) => {
    if (!subjectMap[c.subject]) subjectMap[c.subject] = { total: 0, sum: 0 };
    subjectMap[c.subject].total++;
    subjectMap[c.subject].sum += c.mastery_level || 0;
  });
  const subjectData = Object.entries(subjectMap).map(([subj, { total, sum }]) => ({
    subject: subj, avg: total > 0 ? sum / total : 0, count: total,
    color: SUBJECT_COLORS[subj] || '#8b5cf6',
  })).sort((a, b) => b.avg - a.avg);

  const effByType = {};
  effectiveness.forEach((e) => {
    if (!effByType[e.message_type]) effByType[e.message_type] = { total: 0, responded: 0 };
    effByType[e.message_type].total++;
    if (e.response_received) effByType[e.message_type].responded++;
  });
  const effData = Object.entries(effByType).map(([type, d]) => ({
    type, label: TYPE_LABELS[type] || type,
    rate: d.total > 0 ? Math.round((d.responded / d.total) * 100) : 0,
    total: d.total, responded: d.responded,
    color: TYPE_COLORS[type] || '#71717a',
  })).sort((a, b) => b.rate - a.rate);

  return (
    <div className="space-y-6">
      {/* Back button */}
      <button onClick={onBack}
        className="text-xs px-3 py-1.5 rounded-lg transition-colors cursor-pointer hover:bg-white/5"
        style={{ color: 'var(--text-dim)', border: '1px solid var(--border)' }}>
        Back to overview
      </button>

      {/* Student header */}
      <div className="rounded-xl p-6"
        style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-full flex items-center justify-center"
              style={{ background: 'var(--surface-2)' }}>
              <User size={24} style={{ color: 'var(--text-dim)' }} />
            </div>
            <div>
              <h2 className="text-xl font-bold">{name}</h2>
              <p className="text-sm" style={{ color: 'var(--text-dim)' }}>{formatJid(jid)}</p>
              <div className="flex items-center gap-3 mt-2">
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
                  style={{ background: `${affectCfg.dot}20`, color: affectCfg.dot }}>
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: affectCfg.dot }} />
                  {affectCfg.label}
                </span>
                {plan.board && (
                  <span className="text-xs px-2 py-0.5 rounded"
                    style={{ background: 'var(--surface-2)', color: 'var(--text-dim)' }}>
                    {plan.board} {plan.class}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-6 text-center">
            <div>
              <Flame size={16} className="mx-auto mb-1" style={{ color: 'var(--warning)' }} />
              <p className="text-xl font-bold">{plan.current_streak || 0}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>streak</p>
            </div>
            <div>
              <Target size={16} className="mx-auto mb-1" style={{ color: '#a855f7' }} />
              <p className="text-xl font-bold">{mastery.length}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>concepts</p>
            </div>
            <div>
              <TrendingUp size={16} className="mx-auto mb-1" style={{ color: 'var(--accent)' }} />
              <p className="text-xl font-bold">{plan.engagement_score || 0}%</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>engagement</p>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Section icon={BookOpen} title="Study Plan" color="var(--info)">
          <InfoRow label="Study Time" value={plan.study_time} />
          <InfoRow label="Daily Target" value={`${plan.daily_target_hours || 2}h`} />
          <InfoRow label="Exam Date" value={plan.exam_date} />
          <InfoRow label="Focus Subjects" value={
            (() => {
              try {
                const parsed = typeof plan.focus_subjects === 'string'
                  ? JSON.parse(plan.focus_subjects) : plan.focus_subjects;
                return Array.isArray(parsed) ? parsed.join(', ') : plan.focus_subjects || '--';
              } catch { return plan.focus_subjects || '--'; }
            })()
          } />
          <InfoRow label="Timezone" value={plan.timezone} />
          <InfoRow label="Preferred Hour" value={plan.preferred_send_hour >= 0 ? `${plan.preferred_send_hour}:00` : 'Auto'} />
          <InfoRow label="Max Daily Msgs" value={plan.max_daily_messages} />
          <InfoRow label="Longest Streak" value={`${plan.longest_streak || 0} days`} />
        </Section>

        <Section icon={Brain} title="Mastery Profile" color="#a855f7">
          {subjectData.length > 0 ? (
            <div className="space-y-3">
              {subjectData.map((d) => {
                const val = Math.round(d.avg * 100);
                return (
                  <div key={d.subject}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium">{d.subject}</span>
                      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                        {val}% <span style={{ color: '#52525b' }}>({d.count})</span>
                      </span>
                    </div>
                    <div className="h-4 rounded overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                      <div className="h-full rounded"
                        style={{ width: `${val}%`, background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`, minWidth: val > 0 ? '6px' : '0' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-center py-8" style={{ color: 'var(--text-dim)' }}>No concepts tracked yet</p>
          )}
        </Section>
      </div>

      {mastery.length > 0 && (
        <Section icon={Target} title={`Concepts (${mastery.length})`} color="var(--accent)">
          <div className="max-h-72 overflow-y-auto">
            {mastery.map((c) => <MasteryBar key={c.id || c.concept_id} concept={c} />)}
          </div>
        </Section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Section icon={MessageSquare} title="Response to Messages" color="#06b6d4">
          {effData.length > 0 ? (
            <div className="space-y-2">
              {effData.map((d) => (
                <div key={d.type} className="flex items-center gap-2">
                  <span className="text-xs w-20 text-right flex-shrink-0 truncate"
                    style={{ color: 'var(--text-dim)' }}>{d.label}</span>
                  <div className="flex-1 h-3 rounded overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                    <div className="h-full rounded"
                      style={{ width: `${d.rate}%`, background: `linear-gradient(90deg, ${d.color}cc, ${d.color})`, minWidth: d.rate > 0 ? '4px' : '0' }} />
                  </div>
                  <span className="text-xs font-mono w-10 text-right flex-shrink-0">{d.rate}%</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-center py-8" style={{ color: 'var(--text-dim)' }}>No effectiveness data yet</p>
          )}
        </Section>

        <Section icon={Clock} title="Recent Messages" color="var(--warning)">
          <div className="max-h-64 overflow-y-auto space-y-2">
            {logs.length > 0 ? logs.slice(0, 15).map((l) => (
              <div key={l.id} className="py-1.5 last:border-0"
                style={{ borderBottom: '1px solid rgba(39,39,42,0.5)' }}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-xs font-medium px-1.5 py-0.5 rounded"
                    style={{ color: TYPE_COLORS[l.message_type] || '#71717a',
                      background: `${TYPE_COLORS[l.message_type] || '#71717a'}15` }}>
                    {TYPE_LABELS[l.message_type] || l.message_type}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{l.sent_at}</span>
                </div>
                <p className="text-xs line-clamp-2" style={{ color: 'var(--text-dim)' }}>{l.message_text}</p>
              </div>
            )) : (
              <p className="text-xs text-center py-8" style={{ color: 'var(--text-dim)' }}>No messages sent yet</p>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
