import { useState, useRef, useEffect } from 'react';
import { Activity, ArrowDown, Pause, Play } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, PageHeader, Spinner } from '../ui';

export default function Logs() {
  const [paused, setPaused] = useState(false);
  const { data, loading } = useAPI(() => api.logs(200), [], paused ? null : 3000);
  const bottomRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!paused && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [data, paused]);

  if (loading) return <Spinner />;

  const lines = data?.lines || [];

  const colorLine = (line) => {
    if (line.includes('Error') || line.includes('error') || line.includes('Traceback'))
      return 'var(--danger)';
    if (line.includes('Warning') || line.includes('warning'))
      return 'var(--warning)';
    if (line.includes('[bridge]'))
      return 'var(--info)';
    if (line.includes('Connected') || line.includes('Started') || line.includes('success'))
      return 'var(--accent)';
    return 'var(--text-dim)';
  };

  return (
    <div>
      <PageHeader title="Live Logs" subtitle={`${data?.total || 0} total lines`}
        action={
          <div className="flex items-center gap-2">
            <button onClick={() => setPaused(!paused)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium cursor-pointer transition-all"
              style={{ border: '1px solid var(--border)', color: paused ? 'var(--warning)' : 'var(--accent)' }}>
              {paused ? <Play size={14} /> : <Pause size={14} />}
              {paused ? 'Resume' : 'Live'}
            </button>
            <button onClick={() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' })}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium cursor-pointer transition-all hover:bg-white/5"
              style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
              <ArrowDown size={14} /> Bottom
            </button>
          </div>
        } />

      <Card>
        <div ref={containerRef} className="h-[600px] overflow-y-auto font-mono text-xs leading-5">
          {lines.map((line, i) => (
            <div key={i} className="px-2 py-0.5 hover:bg-white/3" style={{ color: colorLine(line) }}>
              {line}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </Card>
    </div>
  );
}
