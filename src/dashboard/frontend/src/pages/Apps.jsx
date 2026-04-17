import { useState, useMemo, useCallback, useEffect } from 'react';
import { Search, Mic } from 'lucide-react';
import { StatCard, Badge, PageHeader, EmptyState } from '../ui';
import { APP_CATALOG, APP_CATEGORIES, getAppsByCategory, getAppsByStatus, getLogoUrl } from './apps-data';
import { api } from '../api';
import ConnectModal from './ConnectModal';

function AppLogo({ app }) {
  const [imgError, setImgError] = useState(false);
  const logoUrl = getLogoUrl(app.id);

  if (!logoUrl || imgError) {
    return (
      <div
        className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{
          background: `${app.icon_color}18`,
          border: `1px solid ${app.icon_color}30`,
        }}
      >
        {app.id === 'voice-dump'
          ? <Mic size={18} style={{ color: app.icon_color }} />
          : <span className="text-sm font-bold" style={{ color: app.icon_color }}>{app.icon_letter}</span>
        }
      </div>
    );
  }

  return (
    <div
      className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
      style={{
        background: `${app.icon_color}12`,
        border: `1px solid ${app.icon_color}20`,
      }}
    >
      <img
        src={logoUrl}
        alt={app.name}
        className="app-logo w-5 h-5"
        onError={() => setImgError(true)}
        loading="lazy"
      />
    </div>
  );
}

function AppCard({ app, index, isLiveConnected, onConnect }) {
  const isComingSoon = app.status === 'coming_soon';
  const isActive = app.status === 'active';
  const connected = isLiveConnected || isActive;

  const statusLabel = connected ? 'Connected'
    : app.status === 'available' ? 'Available'
    : isComingSoon ? 'Coming Soon'
    : 'Active';

  const statusColor = connected ? 'var(--accent)'
    : app.status === 'available' ? 'var(--info)'
    : 'var(--text-dim)';

  const handleClick = useCallback(() => {
    if (isComingSoon) return;
    onConnect(app);
  }, [isComingSoon, app, onConnect]);

  const staggerClass = `stagger-${Math.min(index % 9 + 1, 9)}`;

  return (
    <div
      className={`rounded-xl p-5 glass-card animate-fade-in-up ${staggerClass}
        ${connected ? 'glass-card-active' : ''}
        ${isComingSoon ? 'opacity-40 cursor-default' : 'cursor-pointer'}`}
      onClick={handleClick}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <AppLogo app={app} />
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">{app.name}</div>
            <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-dim)' }}>
              {app.description}
            </div>
          </div>
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          {connected && (
            <span
              className="w-2 h-2 rounded-full status-pulse"
              style={{ background: 'var(--accent)' }}
            />
          )}
          <Badge color={statusColor}>{statusLabel}</Badge>
        </div>
      </div>
      <div className="flex mt-3">
        <Badge color="var(--text-dim)">{app.category}</Badge>
      </div>
    </div>
  );
}

export default function Apps() {
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [modalApp, setModalApp] = useState(null);
  const [liveStatus, setLiveStatus] = useState({});
  const [authNotice, setAuthNotice] = useState(null);

  // Handle OAuth redirect return (auth_success / auth_error in URL hash params)
  useEffect(() => {
    const hash = window.location.hash;
    const params = new URLSearchParams(hash.split('?')[1] || '');
    const success = params.get('connected');
    const error = params.get('auth_error');
    if (success) {
      setAuthNotice({ type: 'success', app: success });
      // Clean URL
      window.location.hash = '#/apps';
    } else if (error) {
      setAuthNotice({ type: 'error', message: error });
      window.location.hash = '#/apps';
    }
  }, []);

  // Auto-dismiss notice after 5s
  useEffect(() => {
    if (authNotice) {
      const t = setTimeout(() => setAuthNotice(null), 5000);
      return () => clearTimeout(t);
    }
  }, [authNotice]);

  // Fetch live connection status on mount
  useEffect(() => {
    api.authStatus()
      .then(setLiveStatus)
      .catch(() => {}); // silently fail if KV not configured yet
  }, []);

  const isConnected = useCallback((appId) => {
    return !!liveStatus[appId]?.connected;
  }, [liveStatus]);

  const handleStatusChange = useCallback((appId, connected) => {
    setLiveStatus(prev => ({
      ...prev,
      [appId]: connected ? { connected: true, saved_at: new Date().toISOString() } : undefined,
    }));
  }, []);

  const handleConnect = useCallback((app) => {
    // OAuth apps: redirect to the OAuth start endpoint
    if (app.auth_type === 'oauth' && app.oauth_start_url) {
      window.location.href = app.oauth_start_url;
      return;
    }
    // Token/other apps: open the modal
    setModalApp(app);
  }, []);

  const activeCount = useMemo(() => getAppsByStatus(APP_CATALOG, 'active').length, []);
  const availableCount = useMemo(() => getAppsByStatus(APP_CATALOG, 'available').length, []);
  const comingSoonCount = useMemo(() => getAppsByStatus(APP_CATALOG, 'coming_soon').length, []);

  const filteredApps = useMemo(() => {
    const byCategory = getAppsByCategory(APP_CATALOG, activeCategory);
    if (!searchQuery.trim()) return byCategory;
    const q = searchQuery.toLowerCase();
    return byCategory.filter(app => app.name.toLowerCase().includes(q));
  }, [activeCategory, searchQuery]);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Connected Apps"
        subtitle="Manage your WhatsApp agent integrations"
      />

      {authNotice && (
        <div
          className="rounded-xl px-4 py-3 mb-4 text-sm font-medium animate-fade-in"
          style={{
            background: authNotice.type === 'success' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
            border: `1px solid ${authNotice.type === 'success' ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
            color: authNotice.type === 'success' ? 'var(--accent)' : '#ef4444',
          }}
        >
          {authNotice.type === 'success'
            ? `${authNotice.app.charAt(0).toUpperCase() + authNotice.app.slice(1)} connected successfully!`
            : `Connection failed: ${authNotice.message}`}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <StatCard label="Active" value={activeCount} color="var(--accent)" />
        <StatCard label="Available" value={availableCount} color="var(--info)" />
        <StatCard label="Coming Soon" value={comingSoonCount} color="var(--text-dim)" />
      </div>

      {/* Search */}
      <div
        className="flex items-center gap-3 mb-4 rounded-xl px-4 py-2.5 transition-all duration-200 focus-within:border-white/15"
        style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
      >
        <Search size={16} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
        <input
          type="text"
          placeholder="Search apps..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          className="flex-1 bg-transparent text-sm outline-none"
          style={{ color: 'var(--text)' }}
        />
      </div>

      {/* Category pills */}
      <div className="flex flex-wrap gap-2 mb-5">
        {APP_CATEGORIES.map(cat => {
          const isActive = activeCategory === cat.id;
          return (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className="px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-200 cursor-pointer"
              style={{
                background: isActive ? 'var(--accent-dim)' : 'var(--surface-2)',
                color: isActive ? 'var(--accent)' : 'var(--text-dim)',
                border: isActive ? '1px solid rgba(34,197,94,0.2)' : '1px solid transparent',
              }}
            >
              {cat.label}
            </button>
          );
        })}
      </div>

      {/* App grid */}
      {filteredApps.length === 0 ? (
        <EmptyState message="No apps match your search" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredApps.map((app, i) => (
            <AppCard
              key={app.id}
              app={app}
              index={i}
              isLiveConnected={isConnected(app.id)}
              onConnect={handleConnect}
            />
          ))}
        </div>
      )}

      {/* Connect Modal */}
      {modalApp && (
        <ConnectModal
          app={modalApp}
          isConnected={isConnected(modalApp.id) || modalApp.status === 'active'}
          onClose={() => setModalApp(null)}
          onStatusChange={handleStatusChange}
        />
      )}
    </div>
  );
}
