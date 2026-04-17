import { useState } from 'react';
import {
  LayoutDashboard, Users, MessageSquare, FileSpreadsheet,
  Settings, Brain, Activity, Shield, RefreshCw, ChevronRight,
  Send, Bell, Calendar, Zap, Smartphone, UsersRound, Radio, Blocks
} from 'lucide-react';
import Overview from './pages/Overview';
import WhatsAppPage from './pages/WhatsApp';
import Contacts from './pages/Contacts';
import Groups from './pages/Groups';
import Messages from './pages/Messages';
import Broadcast from './pages/Broadcast';
import Proactive from './pages/Proactive';
import Spreadsheets from './pages/Spreadsheets';
import Intelligence from './pages/Intelligence';
import Identity from './pages/Identity';
import Config from './pages/Config';
import Logs from './pages/Logs';
import Apps from './pages/Apps';

const NAV = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'whatsapp', label: 'WhatsApp', icon: Smartphone },
  { id: 'contacts', label: 'Contacts', icon: Users },
  { id: 'groups', label: 'Groups', icon: UsersRound },
  { id: 'messages', label: 'Messages', icon: MessageSquare },
  { id: 'broadcast', label: 'Broadcast', icon: Radio },
  { id: 'proactive', label: 'Proactive', icon: Zap },
  { id: 'spreadsheets', label: 'Spreadsheets', icon: FileSpreadsheet },
  { id: 'intelligence', label: 'Intelligence', icon: Brain },
  { id: 'identity', label: 'Identity', icon: Shield },
  { id: 'apps', label: 'Connected Apps', icon: Blocks },
  { id: 'config', label: 'Configuration', icon: Settings },
  { id: 'logs', label: 'Live Logs', icon: Activity },
];

function Sidebar({ active, onNav }) {
  return (
    <aside className="w-60 h-screen fixed left-0 top-0 flex flex-col"
      style={{ background: 'var(--surface)', borderRight: '1px solid var(--border)' }}>
      {/* Logo */}
      <div className="px-5 py-5 flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center text-lg font-bold"
          style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
          A
        </div>
        <div>
          <div className="text-sm font-semibold tracking-tight">Aegis</div>
          <div className="text-xs" style={{ color: 'var(--text-dim)' }}>Personal Assistant</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-2 space-y-0.5">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => onNav(id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all cursor-pointer
              ${active === id
                ? 'text-white'
                : 'hover:bg-white/5'
              }`}
            style={active === id ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { color: 'var(--text-dim)' }}>
            <Icon size={18} />
            {label}
          </button>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 text-xs" style={{ color: 'var(--text-dim)', borderTop: '1px solid var(--border)' }}>
        Pruned with Aristotelian Analysis
      </div>
    </aside>
  );
}

export default function App() {
  const [page, setPage] = useState('overview');

  const pages = {
    overview: <Overview />,
    whatsapp: <WhatsAppPage />,
    contacts: <Contacts />,
    groups: <Groups />,
    messages: <Messages />,
    broadcast: <Broadcast />,
    proactive: <Proactive />,
    spreadsheets: <Spreadsheets />,
    intelligence: <Intelligence />,
    identity: <Identity />,
    apps: <Apps />,
    config: <Config />,
    logs: <Logs />,
  };

  return (
    <div className="flex min-h-screen">
      <Sidebar active={page} onNav={setPage} />
      <main className="ml-60 flex-1 p-6 max-w-7xl">
        {pages[page] || <Overview />}
      </main>
    </div>
  );
}
