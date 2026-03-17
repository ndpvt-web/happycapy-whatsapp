import { useState, useEffect, useCallback } from 'react';
import { RefreshCw } from 'lucide-react';
import { proactiveApi } from '../proactive/api';
import { KPIRow } from '../proactive/KPIRow';
import { StudentTable } from '../proactive/StudentTable';
import { AffectPanel } from '../proactive/AffectPanel';
import { EffectivenessPanel } from '../proactive/EffectivenessPanel';
import { MasteryPanel } from '../proactive/MasteryPanel';
import { DecisionPanel } from '../proactive/DecisionPanel';
import { StudentDetail } from '../proactive/StudentDetail';
import { PageHeader, Spinner, ErrorBox } from '../ui';

export default function Proactive() {
  const [view, setView] = useState('overview');
  const [selectedJid, setSelectedJid] = useState(null);
  const [stats, setStats] = useState(null);
  const [students, setStudents] = useState(null);
  const [affect, setAffect] = useState(null);
  const [effectiveness, setEffectiveness] = useState(null);
  const [mastery, setMastery] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, st, af, ef, ma] = await Promise.all([
        proactiveApi.stats(),
        proactiveApi.students(),
        proactiveApi.affectSummary(),
        proactiveApi.effectiveness(),
        proactiveApi.mastery(),
      ]);
      setStats(s);
      setStudents(st);
      setAffect(af);
      setEffectiveness(ef);
      setMastery(ma);
    } catch (e) {
      console.error('Proactive load failed:', e);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Auto-refresh every 60s
  useEffect(() => {
    const t = setInterval(loadAll, 60000);
    return () => clearInterval(t);
  }, [loadAll]);

  const openStudent = (jid) => {
    setSelectedJid(jid);
    setView('student');
  };

  if (view === 'student' && selectedJid) {
    return (
      <div>
        <StudentDetail jid={selectedJid} onBack={() => setView('overview')} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Proactive Intelligence"
        subtitle="SM-2 mastery, affective model, feedback loop analytics"
        action={
          <button onClick={loadAll}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer hover:bg-white/5"
            style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      {error && <ErrorBox message={error} />}

      {loading && !stats ? (
        <Spinner />
      ) : (!stats?.total_students && !(students?.students || []).length) ? (
        <div className="flex flex-col items-center justify-center py-20" style={{ color: 'var(--text-dim)' }}>
          <div className="text-4xl mb-4" style={{ opacity: 0.3 }}>&#9889;</div>
          <div className="text-lg font-medium mb-2">No Proactive Data Yet</div>
          <div className="text-sm text-center max-w-md">
            The proactive intelligence system will start tracking student progress, mastery levels, and affective states
            as students interact through WhatsApp. Data will appear here automatically.
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          <KPIRow stats={stats} mastery={mastery} affect={affect} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <EffectivenessPanel data={effectiveness} />
            <AffectPanel data={affect} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <MasteryPanel data={mastery} />
            <DecisionPanel data={effectiveness} />
          </div>

          <StudentTable
            students={students?.students || []}
            onSelect={openStudent}
          />
        </div>
      )}
    </div>
  );
}
