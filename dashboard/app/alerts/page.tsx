'use client';

import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { formatRelativeTime, formatTimestamp } from '@/lib/utils';
import { useAlerts } from '@/lib/hooks/useQueries';
import {
  Check,
  AlertCircle,
  AlertTriangle,
  Info,
  Filter,
  BellRing
} from 'lucide-react';
import type { Alert } from '@/types';

type FilterType = 'all' | 'critical' | 'warning' | 'info';

const severityIcons = {
  critical: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const severityLabels = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
};

function AlertItem({ alert }: { alert: Alert }) {
  const Icon = severityIcons[alert.severity];

  return (
    <Card className={`relative overflow-hidden transition-all duration-300 rounded-sm border ${alert.severity === 'critical'
      ? 'bg-gradient-to-r from-red-500/10 to-transparent border-red-500/30 shadow-[0_0_15px_rgba(239,68,68,0.05)]'
      : alert.severity === 'warning'
        ? 'bg-gradient-to-r from-yellow-500/10 to-transparent border-yellow-500/30 shadow-[0_0_15px_rgba(234,179,8,0.05)]'
        : 'bg-white/[0.03] border-white/[0.05] hover:border-emerald-500/30 hover:shadow-[0_0_15px_rgba(16,185,129,0.05)]'
      }`}>
      <CardContent className="p-4 flex flex-col md:flex-row md:items-start gap-4">
        <Icon className={`h-5 w-5 md:mt-0.5 shrink-0 ${alert.severity === 'critical'
          ? 'text-red-500'
          : alert.severity === 'warning'
            ? 'text-yellow-500'
            : 'text-emerald-500'
          }`} />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className={`px-2 py-0.5 rounded-sm text-[8px] font-mono uppercase tracking-widest border ${alert.severity === 'critical'
              ? 'bg-red-500/10 border-red-500/20 text-red-500'
              : alert.severity === 'warning'
                ? 'bg-yellow-500/10 border-yellow-500/20 text-yellow-500'
                : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500'
              }`}>
              {severityLabels[alert.severity]}
            </span>
            <span className="bg-white/5 border border-white/10 px-2 py-0.5 rounded-sm text-[8px] font-mono uppercase tracking-widest text-white/50">
              {alert.category}
            </span>
            <span className="text-[9px] font-mono uppercase tracking-widest text-white/30">
              {formatTimestamp(alert.timestamp)}
            </span>
            <span className="text-[8px] font-mono uppercase tracking-widest text-white/20">
              ({formatRelativeTime(alert.timestamp)})
            </span>
          </div>
          <p className="text-[11px] font-mono break-words leading-relaxed text-white/70">
            {alert.message}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export default function AlertsPage() {
  const [filter, setFilter] = useState<FilterType>('all');
  const { data: alerts, isLoading } = useAlerts(100);

  // Apply filters
  const filteredAlerts = alerts?.filter((alert) => {
    switch (filter) {
      case 'critical':
        return alert.severity === 'critical';
      case 'warning':
        return alert.severity === 'warning';
      case 'info':
        return alert.severity === 'info';
      default:
        return true;
    }
  });

  const criticalCount = alerts?.filter((a) => a.severity === 'critical').length || 0;
  const warningCount = alerts?.filter((a) => a.severity === 'warning').length || 0;
  const infoCount = alerts?.filter((a) => a.severity === 'info').length || 0;

  return (
    <div className="relative flex flex-col min-h-[calc(100vh-4rem)] bg-[#0B0E14] text-slate-300 p-2 md:p-6 animate-in fade-in duration-500 font-sans space-y-6 overflow-hidden">

      {/* Subtle background grid pattern */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.03] bg-[linear-gradient(rgba(255,255,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,1)_1px,transparent_1px)] bg-[size:40px_40px] [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />

      {/* Top Status Bar */}
      <div className="flex items-center justify-between border-b border-white/[0.05] pb-3 z-10">
        <div className="flex items-center gap-3">
          <BellRing className="h-4 w-4 text-emerald-500" />
          <h1 className="text-xs font-bold tracking-widest uppercase text-white">Alerts <span className="text-white/40 font-mono ml-2 normal-case tracking-normal">System notifications and warnings</span></h1>
        </div>
        <div className="flex items-center gap-3">
          {isLoading ? (
            <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-sm text-[10px] uppercase tracking-widest font-mono text-emerald-500/80">
              <div className="h-2 w-2 border-t-2 border-emerald-500 rounded-full animate-spin" />
              <span>Querying Logs...</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-sm text-[10px] uppercase tracking-widest font-mono text-emerald-500/80">
              <Check className="h-3 w-3" />
              <span>System Nominal</span>
            </div>
          )}
        </div>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 z-10">
        <Card className="relative bg-gradient-to-br from-white/[0.02] to-transparent border-white/[0.05] backdrop-blur-md shadow-lg">
          <CardContent className="pt-6 pb-4">
            <div className="text-2xl font-mono font-bold text-white mb-1">{isLoading ? <div className="h-8 w-16 bg-white/10 rounded-sm animate-pulse" /> : alerts?.length || 0}</div>
            <div className="text-[9px] font-mono tracking-widest uppercase text-white/40">TOTAL ALERTS</div>
          </CardContent>
        </Card>
        <Card className="relative bg-gradient-to-br from-white/[0.02] to-transparent border-white/[0.05] backdrop-blur-md shadow-lg">
          <CardContent className="pt-6 pb-4">
            <div className="text-2xl font-mono font-bold text-red-500 mb-1">{isLoading ? <div className="h-8 w-16 bg-red-500/20 rounded-sm animate-pulse" /> : criticalCount}</div>
            <div className="text-[9px] font-mono tracking-widest uppercase text-white/40">CRITICAL</div>
          </CardContent>
        </Card>
        <Card className="relative bg-gradient-to-br from-white/[0.02] to-transparent border-white/[0.05] backdrop-blur-md shadow-lg">
          <CardContent className="pt-6 pb-4">
            <div className="text-2xl font-mono font-bold text-yellow-500 mb-1">{isLoading ? <div className="h-8 w-16 bg-yellow-500/20 rounded-sm animate-pulse" /> : warningCount}</div>
            <div className="text-[9px] font-mono tracking-widest uppercase text-white/40">WARNINGS</div>
          </CardContent>
        </Card>
        <Card className="relative bg-gradient-to-br from-white/[0.02] to-transparent border-white/[0.05] backdrop-blur-md shadow-lg">
          <CardContent className="pt-6 pb-4">
            <div className="text-2xl font-mono font-bold text-emerald-500 mb-1">{isLoading ? <div className="h-8 w-16 bg-emerald-500/20 rounded-sm animate-pulse" /> : infoCount}</div>
            <div className="text-[9px] font-mono tracking-widest uppercase text-white/40">INFO</div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card className="bg-white/[0.02] border-white/[0.05] z-10 hidden md:block">
        <CardContent className="flex items-center gap-4 py-3">
          <div className="flex items-center gap-2 border-r border-white/10 pr-4">
            <Filter className="h-4 w-4 text-emerald-500/50" />
            <span className="text-[10px] font-mono font-bold tracking-widest uppercase text-white/50">FILTER</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['all', 'ALL'],
                ['critical', 'CRITICAL'],
                ['warning', 'WARNING'],
                ['info', 'INFO'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`flex items-center gap-2 px-3 py-1.5 text-[9px] font-mono tracking-widest uppercase rounded-sm transition-colors border ${filter === value
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-white/5 border-transparent text-white/40 hover:bg-white/10 hover:text-white/60'
                  }`}
              >
                {label}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Alerts list */}
      <div className="space-y-3 z-10 pb-12">
        {isLoading ? (
          Array.from({ length: 5 }).map((_, i) => (
            <Card key={i} className="bg-white/[0.02] border border-white/[0.05] min-h-[90px] flex items-center p-4 rounded-sm">
              <div className="h-5 w-5 rounded-full bg-white/10 animate-pulse shrink-0" />
              <div className="ml-4 flex-1 space-y-2">
                <div className="flex gap-2">
                  <div className="h-4 w-16 bg-white/10 rounded-sm animate-pulse" />
                  <div className="h-4 w-20 bg-white/10 rounded-sm animate-pulse" />
                </div>
                <div className="h-3 w-1/2 bg-white/5 rounded-sm animate-pulse" />
              </div>
            </Card>
          ))
        ) : filteredAlerts && filteredAlerts.length > 0 ? (
          filteredAlerts.map((alert) => (
            <AlertItem key={alert.id} alert={alert} />
          ))
        ) : (
          <Card className="bg-white/[0.02] border border-dashed border-white/10 rounded-sm">
            <CardContent className="py-12 text-center flex flex-col items-center justify-center">
              <span className="text-[10px] font-mono tracking-widest uppercase text-white/30">&gt; NO ACTIVE ALERTS DETECTED</span>
              <span className="text-[9px] font-mono tracking-widest uppercase text-white/20 mt-2">SYSTEM OPERATING WITHIN NOMINAL PARAMETERS</span>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
