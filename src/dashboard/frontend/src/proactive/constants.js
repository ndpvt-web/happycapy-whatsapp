export const AFFECT_COLORS = {
  neutral:    { bg: 'bg-zinc-700', text: 'text-zinc-300', dot: '#71717a', label: 'Neutral' },
  confident:  { bg: 'bg-green-900/50', text: 'text-green-400', dot: '#22c55e', label: 'Confident' },
  frustrated: { bg: 'bg-red-900/50', text: 'text-red-400', dot: '#ef4444', label: 'Frustrated' },
  anxious:    { bg: 'bg-amber-900/50', text: 'text-amber-400', dot: '#f59e0b', label: 'Anxious' },
  bored:      { bg: 'bg-blue-900/50', text: 'text-blue-400', dot: '#3b82f6', label: 'Bored' },
};

export const TYPE_COLORS = {
  reminder:             '#3b82f6',
  checkin:              '#06b6d4',
  nudge:                '#f59e0b',
  countdown:            '#ef4444',
  achievement:          '#22c55e',
  review:               '#a855f7',
  challenge:            '#ec4899',
  recovery:             '#f97316',
  curiosity:            '#14b8a6',
  scaffolding:          '#6366f1',
  celebration_specific: '#eab308',
  autonomy_check:       '#8b5cf6',
  deload:               '#64748b',
};

export const TYPE_LABELS = {
  reminder:             'Reminder',
  checkin:              'Check-in',
  nudge:                'Nudge',
  countdown:            'Countdown',
  achievement:          'Achievement',
  review:               'SM-2 Review',
  challenge:            'Challenge',
  recovery:             'Recovery',
  curiosity:            'Curiosity',
  scaffolding:          'Scaffolding',
  celebration_specific: 'Celebration',
  autonomy_check:       'Autonomy',
  deload:               'Deload',
};

export function formatJid(jid) {
  if (!jid) return '';
  return jid.split('@')[0];
}

export function pct(val) {
  return Math.round((val || 0) * 100);
}
