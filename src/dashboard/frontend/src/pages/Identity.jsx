import { useState } from 'react';
import { Shield, Save } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, PageHeader, Spinner, ErrorBox, Tabs } from '../ui';

export default function Identity() {
  const { data, loading, reload } = useAPI(api.identity, []);
  const [tab, setTab] = useState('SOUL.md');
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  if (loading) return <Spinner />;

  const content = edits[tab] ?? data?.[tab] ?? '';
  const hasChanges = edits[tab] !== undefined && edits[tab] !== data?.[tab];

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.updateIdentity(tab, content);
      setSaved(true);
      setEdits(prev => { const n = { ...prev }; delete n[tab]; return n; });
      reload();
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader title="Identity" subtitle="SOUL.md and USER.md define who the bot is"
        action={
          <button onClick={handleSave} disabled={!hasChanges || saving}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium cursor-pointer transition-all disabled:opacity-40"
            style={{ background: hasChanges ? 'var(--accent)' : 'var(--surface-2)', color: hasChanges ? '#000' : 'var(--text-dim)' }}>
            <Save size={14} />
            {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Changes'}
          </button>
        } />

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}

      <Tabs active={tab} onChange={setTab} tabs={[
        { id: 'SOUL.md', label: 'SOUL.md' },
        { id: 'USER.md', label: 'USER.md' },
      ]} />

      <Card>
        <div className="text-xs mb-2" style={{ color: 'var(--text-dim)' }}>
          {tab === 'SOUL.md'
            ? 'Defines the bot personality, identity rules, and communication style. Changes take effect on next message.'
            : 'Owner profile information. The bot uses this for personalized responses.'}
        </div>
        <textarea
          value={content}
          onChange={e => setEdits(prev => ({ ...prev, [tab]: e.target.value }))}
          className="w-full h-[500px] p-4 rounded-lg text-sm font-mono leading-relaxed outline-none resize-none"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }}
          spellCheck={false}
        />
        <div className="flex justify-between mt-2 text-xs" style={{ color: 'var(--text-dim)' }}>
          <span>{content.split('\n').length} lines</span>
          <span>{content.length} characters</span>
        </div>
      </Card>
    </div>
  );
}
