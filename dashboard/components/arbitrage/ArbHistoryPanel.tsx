'use client';

import { useState } from 'react';
import type { ArbitrageHistoryItem } from '@/types';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { formatAddress, formatCurrency, formatNumber, formatRelativeTime, VENUE_LABELS, txExplorerUrl } from '@/lib/utils';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const PAGE_SIZE = 5;

type StatusFilter = 'all' | 'completed' | 'half_open';

function badgeClass(status: string) {
  if (status === 'completed') return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
  if (status === 'half_open') return 'bg-amber-500/10 text-amber-300 border-amber-500/20';
  return 'bg-red-500/10 text-red-400 border-red-500/20';
}

function badgeLabel(status: string) {
  const labels: Record<string, string> = {
    completed: 'Completed',
    half_open: 'Half Open',
    buy_failed: 'Buy Failed',
    sell_preflight_failed: 'Sell Preflight Failed',
    abandoned: 'Abandoned',
    execution_error: 'Execution Error',
  };
  return labels[status] ?? status.replace(/_/g, ' ');
}

function normalizeReason(reason?: string) {
  if (!reason) return '';
  let text = reason.trim();
  const tupleMatch = text.match(/\('([^']+)'(?:,\s*'[^']+')+\)/);
  if (tupleMatch) text = text.replace(tupleMatch[0], tupleMatch[1]);
  text = text.replace(/:\s*0x[0-9a-fA-F]{8,}/g, '');
  text = text.replace(/\s+(Direction:|Trade size:|Estimated sell:|Min out:|Wallet:)/g, '\n$1');
  return text;
}

function outcomeText(item: ArbitrageHistoryItem) {
  const status = badgeLabel(item.latest_status);
  const reason = normalizeReason(item.reason);
  if (!reason) return status;
  if (reason.startsWith(status)) return reason;
  return `${status}: ${reason}`;
}

export function ArbHistoryPanel({
  items,
  isLoading = false,
}: {
  items?: ArbitrageHistoryItem[];
  isLoading?: boolean;
}) {
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [page, setPage] = useState(0);

  const filtered = (items ?? []).filter((item) => {
    if (filter === 'completed') return item.latest_status === 'completed';
    if (filter === 'half_open') return item.latest_status === 'half_open';
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  const filters: { key: StatusFilter; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'completed', label: 'Completed' },
    { key: 'half_open', label: 'Half Open' },
  ];

  return (
    <Card className="border-white/5 bg-[#12161C] shadow-none">
      <CardHeader className="border-b border-white/5 px-4 py-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-[11px] font-mono uppercase tracking-[0.22em] text-white/55">ARB HISTORY</div>
          <div className="flex items-center gap-1">
            {filters.map(({ key, label }) => {
              const count = key === 'all' ? (items?.length ?? 0)
                : key === 'completed' ? (items?.filter(i => i.latest_status === 'completed').length ?? 0)
                : (items?.filter(i => i.latest_status === 'half_open').length ?? 0);
              return (
                <button
                  key={key}
                  onClick={() => { setFilter(key); setPage(0); }}
                  className={`px-2.5 py-1 rounded-[3px] text-[9px] font-mono font-bold tracking-widest border transition-colors ${
                    filter === key
                      ? key === 'completed' ? 'bg-emerald-500/15 border-emerald-500/35 text-emerald-400'
                        : key === 'half_open' ? 'bg-amber-500/15 border-amber-500/35 text-amber-300'
                        : 'bg-white/10 border-white/20 text-white/70'
                      : 'bg-white/[0.02] border-white/[0.07] text-white/30 hover:text-white/50'
                  }`}
                >
                  {label} {count > 0 && <span className="ml-1 opacity-60">{count}</span>}
                </button>
              );
            })}
          </div>
        </div>
      </CardHeader>

      <CardContent className="px-4 py-4">
        {isLoading ? (
          <div className="flex items-center gap-2 py-8 text-[10px] font-mono tracking-widest text-white/25">
            <div className="h-2 w-2 border border-white/30 rounded-full animate-spin" />
            LOADING
          </div>
        ) : paged.length === 0 ? (
          <div className="py-8 text-[12px] font-mono uppercase tracking-[0.18em] text-white/35">
            No history yet
          </div>
        ) : (
          <div className="space-y-3">
            {paged.map((item) => {
              const isCompleted = item.latest_status === 'completed';
              const buyVenueLabel = VENUE_LABELS[item.buy_venue]?.name ?? item.buy_venue;
              const sellVenueLabel = VENUE_LABELS[item.sell_venue]?.name ?? item.sell_venue;
              const stableSymbol = item.buy_wallet?.stable_symbol ?? 'USDT';

              return (
                <div key={item.opportunity_id} className="rounded-sm border border-white/[0.06] bg-black/20 p-4">
                  {/* Header row */}
                  <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className={`rounded-sm border px-2 py-0.5 text-[9px] font-mono uppercase tracking-[0.18em] ${
                          item.pipeline === 'cex_dex'
                            ? 'bg-violet-500/10 border-violet-500/25 text-violet-400'
                            : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                        }`}>
                          {item.pipeline === 'cex_dex' ? 'CEX-DEX' : 'DEX-DEX'}
                        </span>
                        <span className="text-[12px] font-semibold text-white/85">{buyVenueLabel}</span>
                        <span className="text-white/30">→</span>
                        <span className="text-[12px] font-semibold text-white/85">{sellVenueLabel}</span>
                      </div>
                      <div className="text-[10px] font-mono uppercase tracking-[0.14em] text-white/30">
                        {item.direction.replace(/_/g, ' ')}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <span className={`rounded-sm border px-2 py-0.5 text-[9px] font-mono uppercase tracking-[0.18em] ${badgeClass(item.latest_status)}`}>
                        {badgeLabel(item.latest_status)}
                      </span>
                      <span className="text-[10px] font-mono text-white/30">updated {formatRelativeTime(item.updated_at)}</span>
                    </div>
                  </div>

                  {/* Body grid */}
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    {/* Volume */}
                    <div className="rounded-sm border border-white/[0.05] bg-white/[0.02] p-3">
                      <div className="mb-2 text-[9px] font-mono uppercase tracking-[0.18em] text-white/30">Volume</div>
                      <div className="space-y-2 text-[11px] font-mono">
                        {/* Buy leg */}
                        <div>
                          <div className="text-[9px] uppercase tracking-widest text-white/25 mb-1">{buyVenueLabel}</div>
                          <div className="flex items-center gap-1.5 text-white/70">
                            <span className="tabular-nums text-white/80">
                              {item.executed_size_usd != null ? formatCurrency(item.executed_size_usd) : '—'} {stableSymbol}
                            </span>
                            <span className="text-white/25">→</span>
                            <span className="tabular-nums text-white/80">
                              {item.buy_amount_cngn != null ? `${formatNumber(item.buy_amount_cngn, 0)} cNGN` : '—'}
                            </span>
                          </div>
                        </div>
                        {/* Sell leg — only if trade completed */}
                        {isCompleted && item.buy_amount_cngn != null && item.executed_size_usd != null && (
                          <div>
                            <div className="text-[9px] uppercase tracking-widest text-white/25 mb-1">{sellVenueLabel}</div>
                            <div className="flex items-center gap-1.5 text-white/70">
                              <span className="tabular-nums text-white/80">
                                {formatNumber(item.buy_amount_cngn, 0)} cNGN
                              </span>
                              <span className="text-white/25">→</span>
                              <span className="tabular-nums text-white/80">
                                {item.actual_profit_usd != null
                                  ? formatCurrency(Number(item.executed_size_usd) + Number(item.actual_profit_usd))
                                  : formatCurrency(Number(item.executed_size_usd))}{' '}
                                {item.sell_wallet?.stable_symbol ?? 'USDC'}
                              </span>
                            </div>
                          </div>
                        )}
                        {/* Profit */}
                        {isCompleted && item.actual_profit_usd != null && (
                          <div className="flex items-center justify-between border-t border-white/[0.05] pt-1.5 mt-0.5">
                            <span className="text-white/35 text-[10px]">Net profit</span>
                            <span className={`tabular-nums font-semibold ${item.actual_profit_usd >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {item.actual_profit_usd >= 0 ? '+' : ''}{formatCurrency(item.actual_profit_usd)}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Wallets */}
                    <div className="rounded-sm border border-white/[0.05] bg-white/[0.02] p-3">
                      <div className="mb-2 text-[9px] font-mono uppercase tracking-[0.18em] text-white/30">Wallets</div>
                      <div className="space-y-1.5 text-[11px] font-mono text-white/55">
                        {item.buy_wallet ? (
                          <div>
                            <span className="text-white/30 text-[9px] uppercase tracking-widest block mb-0.5">{buyVenueLabel}</span>
                            <span>{item.buy_wallet.stable_balance != null ? `${formatNumber(item.buy_wallet.stable_balance, 2)} ${item.buy_wallet.stable_symbol ?? 'stable'}` : '—'}</span>
                            <span className="text-white/30"> | </span>
                            <span>{item.buy_wallet.cngn_balance != null ? `${formatNumber(item.buy_wallet.cngn_balance, 2)} cNGN` : '—'}</span>
                          </div>
                        ) : <div className="text-white/25">—</div>}
                        {item.sell_wallet ? (
                          <div>
                            <span className="text-white/30 text-[9px] uppercase tracking-widest block mb-0.5">{sellVenueLabel}</span>
                            <span>{item.sell_wallet.stable_balance != null ? `${formatNumber(item.sell_wallet.stable_balance, 2)} ${item.sell_wallet.stable_symbol ?? 'stable'}` : '—'}</span>
                            <span className="text-white/30"> | </span>
                            <span>{item.sell_wallet.cngn_balance != null ? `${formatNumber(item.sell_wallet.cngn_balance, 2)} cNGN` : '—'}</span>
                          </div>
                        ) : <div className="text-white/25">—</div>}
                      </div>
                    </div>

                    {/* Outcome + TXs */}
                    <div className="rounded-sm border border-white/[0.05] bg-white/[0.02] p-3">
                      <div className="mb-2 text-[9px] font-mono uppercase tracking-[0.18em] text-white/30">Outcome</div>
                      <div className="space-y-1.5 text-[11px]">
                        <div className="whitespace-pre-wrap break-words font-mono leading-relaxed text-white/55">
                          {outcomeText(item)}
                        </div>
                        <div className="text-[10px] font-mono text-white/25">
                          routed {formatRelativeTime(item.routed_at)}
                        </div>
                        {item.buy_tx_hash && (() => {
                          const url = txExplorerUrl(item.buy_venue, item.buy_tx_hash);
                          return url ? (
                            <a href={url} target="_blank" rel="noopener noreferrer"
                              className="break-all font-mono text-[10px] text-blue-400/75 hover:text-blue-400 underline underline-offset-2 transition-colors">
                              Buy: {formatAddress(item.buy_tx_hash)}
                            </a>
                          ) : (
                            <div className="break-all font-mono text-[10px] text-blue-400/75">
                              Buy: {formatAddress(item.buy_tx_hash)}
                            </div>
                          );
                        })()}
                        {item.sell_tx_hash && (() => {
                          const url = txExplorerUrl(item.sell_venue, item.sell_tx_hash);
                          return url ? (
                            <a href={url} target="_blank" rel="noopener noreferrer"
                              className="break-all font-mono text-[10px] text-purple-400/75 hover:text-purple-400 underline underline-offset-2 transition-colors">
                              Sell: {formatAddress(item.sell_tx_hash)}
                            </a>
                          ) : (
                            <div className="break-all font-mono text-[10px] text-purple-400/75">
                              Sell: {formatAddress(item.sell_tx_hash)}
                            </div>
                          );
                        })()}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between pt-1">
                <span className="text-[10px] font-mono text-white/30">
                  {currentPage * PAGE_SIZE + 1}–{Math.min((currentPage + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setPage(p => Math.max(0, p - 1))}
                    disabled={currentPage === 0}
                    className="p-1 rounded-[3px] border border-white/[0.07] text-white/40 hover:text-white/70 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <span className="text-[10px] font-mono text-white/40 px-1">
                    {currentPage + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                    disabled={currentPage >= totalPages - 1}
                    className="p-1 rounded-[3px] border border-white/[0.07] text-white/40 hover:text-white/70 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
