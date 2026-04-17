import { useState, useEffect, useCallback } from 'react';
import { api } from './lib/api';
import { Header } from './components/Header';
import { KPIRow } from './components/KPIRow';
import { StudentTable } from './components/StudentTable';
import { AffectPanel } from './components/AffectPanel';
import { EffectivenessPanel } from './components/EffectivenessPanel';
import { MasteryPanel } from './components/MasteryPanel';
import { DecisionPanel } from './components/DecisionPanel';
import { StudentDetail } from './components/StudentDetail';

export default function App() {
  const [view, setView] = useState('overview');
  const [selectedJid, setSelectedJid] = useState(null);
  const [stats, setStats] = useState(null);
  const [students, setStudents] = useState(null);
  const [affect, setAffect] = useState(null);
  const [effectiveness, setEffectiveness] = useState(null);
  const [mastery, setMastery] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, st, af, ef, ma] = await Promise.all([
        api.stats(),
        api.students(),
        api.affectSummary(),
        api.effectiveness(),
        api.mastery(),
      ]);
      setStats(s);
      setStudents(st);
      setAffect(af);
      setEffectiveness(ef);
      setMastery(ma);
      setLastRefresh(new Date());
    } catch (e) {
      console.error('Load failed:', e);
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
      <div className="min-h-screen bg-zinc-950">
        <Header
          onBack={() => setView('overview')}
          lastRefresh={lastRefresh}
          onRefresh={loadAll}
        />
        <main className="max-w-7xl mx-auto px-4 pb-12">
          <StudentDetail jid={selectedJid} onBack={() => setView('overview')} />
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      <Header lastRefresh={lastRefresh} onRefresh={loadAll} />
      <main className="max-w-7xl mx-auto px-4 pb-12">
        {loading && !stats ? (
          <div className="flex items-center justify-center h-64">
            <div className="text-zinc-500 text-lg">Loading intelligence data...</div>
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
      </main>
    </div>
  );
}
