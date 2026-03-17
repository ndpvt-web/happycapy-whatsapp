import { useState } from 'react';
import { Wifi, WifiOff, QrCode, LogOut, RotateCcw, Shield, Clock, HardDrive } from 'lucide-react';
import { api } from '../api';
import { useAPI } from '../hooks';
import { Card, StatCard, Badge, PageHeader, Spinner, ErrorBox, EmptyState } from '../ui';

function formatUptime(s) {
  if (!s) return '--';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatKB(kb) {
  if (!kb) return '0 KB';
  if (kb > 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${Math.round(kb)} KB`;
}

export default function WhatsApp() {
  const { data: wa, loading: wl, error: we, reload: reloadWa } = useAPI(api.whatsappStatus, [], 5000);
  const { data: health, loading: hl, reload: reloadHealth } = useAPI(api.health, [], 10000);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);

  async function handleRestart() {
    setActionLoading('restart');
    setActionMsg(null);
    try {
      await api.restart();
      setActionMsg('Bot restart initiated');
      setTimeout(() => { reloadWa(); reloadHealth(); }, 3000);
    } catch (e) {
      setActionMsg(`Restart failed: ${e.message}`);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleLogout() {
    if (!confirm('This will disconnect WhatsApp and require re-scanning QR code. Continue?')) return;
    setActionLoading('logout');
    setActionMsg(null);
    try {
      await api.whatsappLogout();
      setActionMsg('Logged out. Re-scan QR code to reconnect.');
      setTimeout(() => { reloadWa(); reloadHealth(); }, 2000);
    } catch (e) {
      setActionMsg(`Logout failed: ${e.message}`);
    } finally {
      setActionLoading(null);
    }
  }

  if (wl && hl) return <Spinner />;

  const w = wa || {};
  const h = health || {};
  const dbSizes = h.database_sizes_kb || {};

  return (
    <div>
      <PageHeader title="WhatsApp" subtitle="Connection status, session management, and controls"
        action={
          <button onClick={() => { reloadWa(); reloadHealth(); }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer hover:bg-white/5"
            style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
            <RotateCcw size={14} /> Refresh
          </button>
        } />

      {we && <ErrorBox message={we} />}

      {/* Connection Status Card */}
      <Card className="mb-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center"
                style={{ background: w.connected ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)' }}>
                {w.connected ? <Wifi size={24} style={{ color: 'var(--accent)' }} /> : <WifiOff size={24} style={{ color: 'var(--danger)' }} />}
              </div>
              <div>
                <div className="text-lg font-semibold">
                  {w.connected ? 'Connected' : 'Disconnected'}
                </div>
                <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
                  {w.authenticated ? 'Authenticated' : 'Not authenticated'}
                  {w.bridge_running ? ' \u2022 Bridge running' : ' \u2022 Bridge stopped'}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge color={w.connected ? 'var(--accent)' : 'var(--danger)'}>
                {w.connected ? 'ONLINE' : 'OFFLINE'}
              </Badge>
              {w.has_qr && <Badge color="var(--warning)">QR Available</Badge>}
            </div>
          </div>
        </div>
      </Card>

      {/* QR Code */}
      {w.has_qr && w.qr && (
        <Card className="mb-6">
          <div className="flex items-center gap-3 mb-4">
            <QrCode size={18} style={{ color: 'var(--warning)' }} />
            <span className="text-sm font-medium">Scan QR Code to Connect</span>
          </div>
          <div className="flex justify-center p-4 rounded-lg" style={{ background: '#fff' }}>
            <img src={`data:image/png;base64,${w.qr}`} alt="WhatsApp QR" className="w-64 h-64" />
          </div>
        </Card>
      )}

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="Bot Status" value={h.running ? 'Running' : 'Stopped'}
          color={h.running ? 'var(--accent)' : 'var(--danger)'} sub={`PID: ${h.pid || '--'}`} />
        <StatCard label="Uptime" value={formatUptime(h.uptime_seconds)}
          color="var(--info)" sub="Since last start" />
        <StatCard label="Errors (1h)" value={h.errors_last_hour || 0}
          color={h.errors_last_hour > 0 ? 'var(--danger)' : 'var(--accent)'}
          sub="Last hour" />
        <StatCard label="Auth Status" value={w.authenticated ? 'Valid' : 'Expired'}
          color={w.authenticated ? 'var(--accent)' : 'var(--warning)'}
          sub={w.qr_server_reachable ? 'QR server OK' : 'QR server down'} />
      </div>

      {/* Database Sizes */}
      <Card className="mb-6">
        <div className="flex items-center gap-2 mb-4">
          <HardDrive size={16} style={{ color: 'var(--text-dim)' }} />
          <span className="text-sm font-medium">Database Storage</span>
        </div>
        <div className="grid grid-cols-3 gap-4">
          {Object.entries(dbSizes).map(([name, kb]) => (
            <div key={name} className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--surface-2)' }}>
              <span className="text-sm capitalize" style={{ color: 'var(--text-dim)' }}>{name}</span>
              <span className="text-sm font-medium">{formatKB(kb)}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Actions */}
      <Card>
        <div className="text-sm font-medium mb-4">Actions</div>
        <div className="flex gap-3">
          <button onClick={handleRestart} disabled={actionLoading === 'restart'}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all cursor-pointer"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid rgba(34,197,94,0.3)' }}>
            <RotateCcw size={14} className={actionLoading === 'restart' ? 'animate-spin' : ''} />
            {actionLoading === 'restart' ? 'Restarting...' : 'Restart Bot'}
          </button>
          <button onClick={handleLogout} disabled={actionLoading === 'logout'}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all cursor-pointer"
            style={{ background: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.3)' }}>
            <LogOut size={14} />
            {actionLoading === 'logout' ? 'Logging out...' : 'Logout WhatsApp'}
          </button>
        </div>
        {actionMsg && (
          <div className="mt-3 text-sm" style={{ color: 'var(--text-dim)' }}>{actionMsg}</div>
        )}
      </Card>
    </div>
  );
}
