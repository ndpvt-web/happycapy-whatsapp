import { useState, useCallback, useEffect, useRef } from 'react';
import { X, Check, Trash2, ExternalLink, Loader2, Shield } from 'lucide-react';
import { api } from '../api';

export default function ConnectModal({ app, isConnected, onClose, onStatusChange }) {
  const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    if (inputRef.current && !isConnected) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isConnected]);

  useEffect(() => {
    const handleEsc = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  const handleSave = useCallback(async () => {
    if (!token.trim()) { setError('Please enter a token'); return; }
    setSaving(true);
    setError('');
    try {
      await api.saveToken(app.id, token.trim(), app.auth_type);
      setSuccess('Connected successfully');
      setToken('');
      onStatusChange?.(app.id, true);
      setTimeout(onClose, 800);
    } catch (err) {
      setError(err.message || 'Failed to save token');
    } finally {
      setSaving(false);
    }
  }, [token, app, onClose, onStatusChange]);

  const handleDisconnect = useCallback(async () => {
    setDisconnecting(true);
    setError('');
    try {
      if (app.auth_type === 'oauth') {
        await api.oauthDisconnect(app.id);
      } else {
        await api.deleteToken(app.id);
      }
      setSuccess('Disconnected');
      onStatusChange?.(app.id, false);
      setTimeout(onClose, 600);
    } catch (err) {
      setError(err.message || 'Failed to disconnect');
    } finally {
      setDisconnecting(false);
    }
  }, [app, onClose, onStatusChange]);

  const isGws = app.auth_type === 'gws';
  const isNone = app.auth_type === 'none';
  const isOAuth = app.auth_type === 'oauth';
  const isToken = app.auth_type === 'token';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in"
      style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl p-6 animate-scale-in"
        style={{
          background: 'linear-gradient(135deg, rgba(30,30,35,0.98) 0%, rgba(20,20,24,0.98) 100%)',
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: `${app.icon_color}18`, border: `1px solid ${app.icon_color}30` }}
            >
              <span className="text-sm font-bold" style={{ color: app.icon_color }}>
                {app.icon_letter}
              </span>
            </div>
            <div>
              <div className="font-semibold text-sm">{app.name}</div>
              <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
                {isConnected ? 'Connected' : 'Connect to your agent'}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg transition-colors cursor-pointer"
            style={{ color: 'var(--text-dim)' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Help text */}
        {app.token_help && (
          <div
            className="text-xs rounded-lg px-3 py-2.5 mb-4"
            style={{ background: 'var(--surface)', color: 'var(--text-dim)', border: '1px solid var(--border)' }}
          >
            <Shield size={12} className="inline mr-1.5 -mt-0.5" style={{ color: 'var(--accent)' }} />
            {app.token_help}
          </div>
        )}

        {/* Status messages */}
        {error && (
          <div className="text-xs rounded-lg px-3 py-2 mb-3" style={{ background: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.2)' }}>
            {error}
          </div>
        )}
        {success && (
          <div className="text-xs rounded-lg px-3 py-2 mb-3" style={{ background: 'rgba(34,197,94,0.1)', color: 'var(--accent)', border: '1px solid rgba(34,197,94,0.2)' }}>
            <Check size={12} className="inline mr-1 -mt-0.5" /> {success}
          </div>
        )}

        {/* Body - varies by auth type */}
        {(isGws || isNone) && (
          <div className="text-xs text-center py-4" style={{ color: 'var(--text-dim)' }}>
            {isGws ? 'Managed by Google Workspace CLI. No manual setup needed.' : 'Built-in feature. No configuration required.'}
          </div>
        )}

        {isOAuth && !isConnected && (
          <div className="flex flex-col gap-3">
            <div className="text-xs text-center py-2" style={{ color: 'var(--text-dim)' }}>
              OAuth setup required. Register your OAuth app first, then the Connect button will redirect you.
            </div>
            {app.connect_url && (
              <a
                href={app.connect_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all cursor-pointer"
                style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid rgba(34,197,94,0.2)' }}
              >
                <ExternalLink size={14} /> Set up OAuth App
              </a>
            )}
          </div>
        )}

        {isToken && !isConnected && (
          <div className="flex flex-col gap-3">
            <input
              ref={inputRef}
              type="password"
              placeholder="Paste your API token here..."
              value={token}
              onChange={e => { setToken(e.target.value); setError(''); }}
              onKeyDown={e => { if (e.key === 'Enter') handleSave(); }}
              className="w-full px-4 py-2.5 rounded-xl text-sm outline-none transition-all"
              style={{
                background: 'var(--surface)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
              }}
            />
            <button
              onClick={handleSave}
              disabled={saving || !token.trim()}
              className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all cursor-pointer disabled:opacity-40 disabled:cursor-default"
              style={{ background: 'var(--accent)', color: '#000' }}
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              {saving ? 'Saving...' : 'Connect'}
            </button>
          </div>
        )}

        {isConnected && (isToken || isOAuth) && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2 py-2">
              <span className="w-2 h-2 rounded-full status-pulse" style={{ background: 'var(--accent)' }} />
              <span className="text-sm" style={{ color: 'var(--accent)' }}>Connected and active</span>
            </div>
            <button
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all cursor-pointer disabled:opacity-40"
              style={{ background: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.2)' }}
            >
              {disconnecting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
              {disconnecting ? 'Disconnecting...' : 'Disconnect'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
