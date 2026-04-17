import { Brain, RefreshCw, ArrowLeft, Clock } from 'lucide-react';

export function Header({ onBack, lastRefresh, onRefresh }) {
  return (
    <header className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
        <div className="flex items-center gap-3">
          {onBack && (
            <button
              onClick={onBack}
              className="p-2 rounded-lg hover:bg-zinc-800 transition-colors text-zinc-400 hover:text-zinc-200"
            >
              <ArrowLeft size={18} />
            </button>
          )}
          <Brain className="text-green-500" size={24} />
          <div>
            <h1 className="text-base font-semibold text-zinc-100 leading-tight">
              Proactive Intelligence
            </h1>
            <p className="text-xs text-zinc-500">Babloo Learning Companion</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {lastRefresh && (
            <span className="text-xs text-zinc-600 flex items-center gap-1">
              <Clock size={12} />
              {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={onRefresh}
            className="p-2 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 transition-colors text-zinc-400 hover:text-green-400"
            title="Refresh data"
          >
            <RefreshCw size={16} />
          </button>
          <a
            href="/"
            className="text-xs px-3 py-1.5 rounded-md bg-zinc-900 border border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
          >
            Main Dashboard
          </a>
        </div>
      </div>
    </header>
  );
}
