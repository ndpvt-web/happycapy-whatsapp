import { useState } from 'react';
import { Settings, Save, RotateCcw } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, Badge, PageHeader, Spinner, ErrorBox, Tabs } from '../ui';

const FIELD_GROUPS = {
  core: [
    { key: 'purpose', label: 'Purpose', type: 'select', options: ['personal_assistant', 'business_support', 'team_coordination', 'monitoring_only'] },
    { key: 'personality_mode', label: 'Personality Mode', type: 'select', options: ['impersonate', 'assistant'] },
    { key: 'mode', label: 'Reply Mode', type: 'select', options: ['auto_reply', 'ask_before_reply', 'monitor_only'] },
    { key: 'tone', label: 'Tone', type: 'select', options: ['casual_friendly', 'professional', 'concise_direct', 'warm_empathetic'] },
    { key: 'admin_number', label: 'Admin Number', type: 'text' },
    { key: 'owner_name', label: 'Owner Name', type: 'text' },
    { key: 'business_template', label: 'Business Template', type: 'text' },
  ],
  privacy: [
    { key: 'privacy_level', label: 'Privacy Level', type: 'select', options: ['strict', 'moderate', 'open'] },
    { key: 'group_policy', label: 'Group Policy', type: 'select', options: ['monitor', 'ignore'] },
    { key: 'allowlist', label: 'Allowlist (comma-separated)', type: 'array' },
    { key: 'blocklist', label: 'Blocklist (comma-separated)', type: 'array' },
  ],
  features: [
    { key: 'tool_calling_enabled', label: 'Tool Calling', type: 'toggle' },
    { key: 'voice_transcription', label: 'Voice Transcription', type: 'toggle' },
    { key: 'escalation_enabled', label: 'Escalation Engine', type: 'toggle' },
    { key: 'auto_reply_when_busy', label: 'Auto-Reply When Busy', type: 'toggle' },
    { key: 'alert_on_auto_reply', label: 'Alert on Auto-Reply', type: 'toggle' },
    { key: 'quiet_hours_enabled', label: 'Quiet Hours', type: 'toggle' },
  ],
  limits: [
    { key: 'rate_limit_per_minute', label: 'Rate Limit (msg/min)', type: 'number' },
    { key: 'max_message_length', label: 'Max Message Length', type: 'number' },
    { key: 'importance_threshold', label: 'Importance Threshold', type: 'number' },
    { key: 'media_max_age_hours', label: 'Media Max Age (hours)', type: 'number' },
  ],
  technical: [
    { key: 'ai_model', label: 'AI Model', type: 'text' },
    { key: 'profile_model', label: 'Profile Model', type: 'text' },
    { key: 'bridge_port', label: 'Bridge Port', type: 'number' },
    { key: 'qr_server_port', label: 'QR Server Port', type: 'number' },
    { key: 'log_level', label: 'Log Level', type: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
  ],
};

function FieldEditor({ field, value, onChange }) {
  if (field.type === 'toggle') {
    return (
      <button onClick={() => onChange(!value)}
        className={`w-11 h-6 rounded-full relative transition-all cursor-pointer`}
        style={{ background: value ? 'var(--accent)' : 'var(--border)' }}>
        <span className="absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all"
          style={{ left: value ? '22px' : '2px' }} />
      </button>
    );
  }
  if (field.type === 'select') {
    return (
      <select value={value || ''} onChange={e => onChange(e.target.value)}
        className="px-3 py-1.5 rounded-lg text-sm outline-none cursor-pointer"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }}>
        {field.options.map(o => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
      </select>
    );
  }
  if (field.type === 'number') {
    return (
      <input type="number" value={value ?? ''} onChange={e => onChange(Number(e.target.value))}
        className="px-3 py-1.5 rounded-lg text-sm outline-none w-28"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />
    );
  }
  if (field.type === 'array') {
    return (
      <input type="text" value={Array.isArray(value) ? value.join(', ') : ''}
        onChange={e => onChange(e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
        placeholder="empty = everyone"
        className="px-3 py-1.5 rounded-lg text-sm outline-none w-64"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />
    );
  }
  return (
    <input type="text" value={value || ''} onChange={e => onChange(e.target.value)}
      className="px-3 py-1.5 rounded-lg text-sm outline-none w-64"
      style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }} />
  );
}

export default function Config() {
  const { data: original, loading, reload } = useAPI(api.config, []);
  const [config, setConfig] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tab, setTab] = useState('core');
  const [error, setError] = useState(null);

  if (loading) return <Spinner />;

  const current = config || original || {};

  const handleChange = (key, value) => {
    setConfig({ ...current, [key]: value });
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const diff = {};
      Object.keys(current).forEach(k => {
        if (JSON.stringify(current[k]) !== JSON.stringify((original || {})[k])) {
          diff[k] = current[k];
        }
      });
      if (Object.keys(diff).length > 0) {
        await api.updateConfig(diff);
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleRestart = async () => {
    try {
      await api.restart();
      alert('Bot restarting...');
    } catch (e) {
      setError(e.message);
    }
  };

  const hasChanges = config && JSON.stringify(config) !== JSON.stringify(original);

  return (
    <div>
      <PageHeader title="Configuration" subtitle="Bot settings and preferences"
        action={
          <div className="flex items-center gap-2">
            <button onClick={handleRestart}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium cursor-pointer transition-all hover:bg-white/5"
              style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
              <RotateCcw size={14} /> Restart Bot
            </button>
            <button onClick={handleSave} disabled={!hasChanges || saving}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium cursor-pointer transition-all disabled:opacity-40"
              style={{ background: hasChanges ? 'var(--accent)' : 'var(--surface-2)', color: hasChanges ? '#000' : 'var(--text-dim)' }}>
              <Save size={14} />
              {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Changes'}
            </button>
          </div>
        } />

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}

      <Tabs active={tab} onChange={setTab} tabs={[
        { id: 'core', label: 'Core' },
        { id: 'privacy', label: 'Privacy' },
        { id: 'features', label: 'Features' },
        { id: 'limits', label: 'Limits' },
        { id: 'technical', label: 'Technical' },
      ]} />

      <Card>
        <div className="space-y-5">
          {(FIELD_GROUPS[tab] || []).map(field => (
            <div key={field.key} className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">{field.label}</div>
                <div className="text-xs" style={{ color: 'var(--text-dim)' }}>{field.key}</div>
              </div>
              <FieldEditor field={field} value={current[field.key]} onChange={v => handleChange(field.key, v)} />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
