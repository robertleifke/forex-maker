'use client';

import { useEffect, useRef, useMemo, useState, useCallback } from 'react';
import { createChart, ColorType, LineStyle, CrosshairMode } from 'lightweight-charts';
import type { UTCTimestamp } from 'lightweight-charts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  formatNumber,
  normalizeSnapshotMid,
  sourceToVenue,
  VENUE_LABELS,
  VENUE_COLORS,
} from '@/lib/utils';
import { usePriceHistory } from '@/lib/hooks/useQueries';
import type { BlendedPriceResponse, PriceSnapshot } from '@/types';

const TIME_WINDOWS = [
  { label: '5M', minutes: 5 },
  { label: '10M', minutes: 10 },
  { label: '1H', minutes: 60 },
  { label: '24H', minutes: 1440 },
  { label: '1W', minutes: 10080 },
  { label: '1M', minutes: 43200 },
  { label: '1Y', minutes: 525600 },
  { label: 'ALL', minutes: Infinity },
] as const;

const CHART_TYPES = [
  { label: 'AREA', value: 'area' },
  { label: 'SPREAD', value: 'spread' },
  { label: 'DELTA', value: 'delta' },
] as const;

type ChartType = typeof CHART_TYPES[number]['value'];

interface VenuePriceChartProps {
  blended?: BlendedPriceResponse;
}

function bucketMs(windowMinutes: number): number {
  if (windowMinutes <= 10) return 30_000;       // 30s
  if (windowMinutes <= 60) return 60_000;        // 1m
  if (windowMinutes <= 1440) return 300_000;     // 5m
  if (windowMinutes <= 10080) return 1_800_000;  // 30m
  return 3_600_000;                              // 1h
}

function buildSeriesData(snapshots: PriceSnapshot[], windowMinutes = 60): {
  byVenue: Record<string, { time: UTCTimestamp; value: number }[]>;
  venues: string[];
} {
  const venueSet = new Set<string>();
  const buckets = new Map<number, Record<string, number>>();

  for (const snap of snapshots) {
    const venue = sourceToVenue(snap.source);
    const mid = normalizeSnapshotMid(snap.source, Number(snap.mid));
    if (mid === null) continue;

    venueSet.add(venue);
    const bkt = bucketMs(windowMinutes);
    const bucket = Math.floor(snap.timestamp / bkt) * bkt;
    if (!buckets.has(bucket)) buckets.set(bucket, {});
    const b = buckets.get(bucket)!;
    if (!(venue in b)) b[venue] = mid;
  }

  const sorted = Array.from(buckets.entries()).sort((a, b) => a[0] - b[0]);
  const venues = Array.from(venueSet);

  const byVenue: Record<string, { time: number; value: number }[]> = {};
  for (const venue of venues) byVenue[venue] = [];

  for (const [ts, values] of sorted) {
    const timeSec = Math.floor(ts / 1000) as UTCTimestamp;
    for (const venue of venues) {
      if (venue in values) {
        byVenue[venue].push({ time: timeSec, value: values[venue] });
      }
    }
  }

  return { byVenue, venues };
}

function buildSpreadData(snapshots: PriceSnapshot[], windowMinutes = 60): { time: UTCTimestamp; value: number }[] {
  const { byVenue, venues } = buildSeriesData(snapshots, windowMinutes);
  if (venues.length < 2) return [];

  // Merge all timestamps
  const tsMap = new Map<UTCTimestamp, Record<string, number>>();
  for (const venue of venues) {
    for (const pt of byVenue[venue]) {
      if (!tsMap.has(pt.time)) tsMap.set(pt.time, {});
      tsMap.get(pt.time)![venue] = pt.value;
    }
  }

  return Array.from(tsMap.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([ts, vals]) => {
      const prices = Object.values(vals).filter((v) => v && isFinite(v));
      if (prices.length < 2) return null;
      const min = Math.min(...prices);
      const max = Math.max(...prices);
      return { time: ts, value: Math.round(((max - min) / min) * 10000) };
    })
    .filter(Boolean) as { time: UTCTimestamp; value: number }[];
}

function buildDeltaData(snapshots: PriceSnapshot[], vwapNgn: number | null, windowMinutes = 60): Record<string, { time: UTCTimestamp; value: number }[]> {
  const { byVenue, venues } = buildSeriesData(snapshots, windowMinutes);

  const tsAvg = new Map<UTCTimestamp, number[]>();
  for (const venue of venues) {
    for (const pt of byVenue[venue]) {
      if (!tsAvg.has(pt.time)) tsAvg.set(pt.time, []);
      tsAvg.get(pt.time)!.push(pt.value);
    }
  }

  const result: Record<string, { time: UTCTimestamp; value: number }[]> = {};
  for (const venue of venues) {
    result[venue] = byVenue[venue].map((pt) => {
      const avgs = tsAvg.get(pt.time) ?? [];
      const base = vwapNgn ?? (avgs.length ? avgs.reduce((a, b) => a + b, 0) / avgs.length : pt.value);
      return { time: pt.time, value: Math.round(((pt.value - base) / base) * 10000) };
    });
  }
  return result;
}

const CHART_OPTIONS = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: 'rgba(255,255,255,0.3)',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 10,
  },
  grid: {
    vertLines: { color: 'rgba(255,255,255,0.02)' },
    horzLines: { color: 'rgba(255,255,255,0.04)' },
  },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: { color: 'rgba(255,255,255,0.2)', style: LineStyle.Dashed, width: 1, labelBackgroundColor: '#0B0E14' },
    horzLine: { color: 'rgba(255,255,255,0.2)', style: LineStyle.Dashed, width: 1, labelBackgroundColor: '#0B0E14' },
  },
  rightPriceScale: {
    borderColor: 'rgba(255,255,255,0.05)',
    textColor: 'rgba(255,255,255,0.3)',
  },
  timeScale: {
    borderColor: 'rgba(255,255,255,0.05)',
    timeVisible: true,
    secondsVisible: false,
    fixLeftEdge: false,
    fixRightEdge: false,
  },
  watermark: { visible: false },
  handleScroll: true,
  handleScale: true,
} as const;

export function VenuePriceChart({ blended }: VenuePriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  const seriesRef = useRef<any[]>([]);
  const [chartType, setChartType] = useState<ChartType>('area');
  const [windowMinutes, setWindowMinutes] = useState<number>(60);
  const [spread, setSpread] = useState<number | null>(null);
  const [activeVenues, setActiveVenues] = useState<string[]>([]);
  const windowAppliedRef = useRef<string>('');

  const applyWindow = useCallback((minutes: number) => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!isFinite(minutes)) {
      chart.timeScale().fitContent();
      return;
    }
    const nowSec = Math.floor(Date.now() / 1000);
    chart.timeScale().setVisibleRange({
      from: (nowSec - minutes * 60) as UTCTimestamp,
      to: nowSec as UTCTimestamp,
    });
  }, []);

  const { data: snapshots } = usePriceHistory(isFinite(windowMinutes) ? windowMinutes : undefined);

  const vwapNgn = useMemo(
    () => (blended && blended.vwap > 0 ? 1 / blended.vwap : null),
    [blended],
  );

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      width: containerRef.current.clientWidth,
      height: 340,
    });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // Rebuild series when data or chart type changes
  useEffect(() => {
    const windowKey = `${windowMinutes}:${chartType}`;
    const windowChanged = windowAppliedRef.current !== windowKey;
    const chart = chartRef.current;
    if (!chart || !snapshots?.length) return;

    // Clear existing series
    for (const s of seriesRef.current) {
      try { chart.removeSeries(s); } catch {}
    }
    seriesRef.current = [];

    if (chartType === 'spread') {
      const data = buildSpreadData(snapshots, windowMinutes);
      const series = chart.addAreaSeries({
        lineColor: 'rgba(16,185,129,0.8)',
        topColor: 'rgba(16,185,129,0.15)',
        bottomColor: 'rgba(16,185,129,0.01)',
        lineWidth: 2,
        priceFormat: { type: 'custom', formatter: (v: number) => `${Math.round(v)} bps` },
      });
      series.setData(data);
      // 110 bps reference line
      series.createPriceLine({
        price: 110,
        color: 'rgba(245,158,11,0.5)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '110 bps',
      });
      seriesRef.current = [series];
      if (windowChanged) { applyWindow(windowMinutes); windowAppliedRef.current = windowKey; }
      return;
    }

    if (chartType === 'delta') {
      const byVenue = buildDeltaData(snapshots, vwapNgn, windowMinutes);
      const venues = Object.keys(byVenue);
      setActiveVenues(venues);
      const newSeries = [];
      for (const venue of venues) {
        const color = VENUE_COLORS[venue] || '#888';
        const series = chart.addLineSeries({
          color,
          lineWidth: 2,
          title: VENUE_LABELS[venue]?.name || venue,
          priceFormat: { type: 'custom', formatter: (v: number) => `${v > 0 ? '+' : ''}${Math.round(v)} bps` },
          crosshairMarkerVisible: true,
          crosshairMarkerRadius: 4,
          lastValueVisible: true,
        });
        series.setData(byVenue[venue]);
        newSeries.push(series);
      }
      // Zero line
      if (newSeries[0]) {
        newSeries[0].createPriceLine({ price: 0, color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
      }
      seriesRef.current = newSeries;
      if (windowChanged) { applyWindow(windowMinutes); windowAppliedRef.current = windowKey; }
      return;
    }

    // AREA
    const { byVenue, venues } = buildSeriesData(snapshots, windowMinutes);
    setActiveVenues(venues);

    // Compute current spread from last point across venues
    const lastPrices: number[] = [];
    const newSeries = [];

    for (const venue of venues) {
      const color = VENUE_COLORS[venue] || '#888';
      const data = byVenue[venue];
      if (!data.length) continue;
      lastPrices.push(data[data.length - 1].value);

      const series = chart.addAreaSeries({
        lineColor: color,
        topColor: color + '26',
        bottomColor: color + '03',
        lineWidth: 2,
        title: VENUE_LABELS[venue]?.name || venue,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 4,
        lastValueVisible: true,
      });

      series.setData(data);
      newSeries.push(series);
    }

    // VWAP line
    if (vwapNgn && isFinite(vwapNgn) && newSeries[0]) {
      newSeries[0].createPriceLine({
        price: vwapNgn,
        color: 'rgba(16,185,129,0.4)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `VWAP ${formatNumber(vwapNgn, 1)}`,
      });
    }

    if (lastPrices.length >= 2) {
      const min = Math.min(...lastPrices);
      const max = Math.max(...lastPrices);
      setSpread(Math.round(((max - min) / min) * 10000));
    }

    seriesRef.current = newSeries;
    if (windowChanged) { applyWindow(windowMinutes); windowAppliedRef.current = windowKey; }
  }, [snapshots, chartType, vwapNgn, applyWindow, windowMinutes]);

  const noData = !snapshots?.length;

  return (
    <Card className="col-span-full bg-white/[0.02] border-white/[0.05]">
      <CardHeader className="flex flex-row items-center justify-between border-b border-white/[0.05] pb-3 mb-2">
        <CardTitle className="text-[10px] font-mono font-bold tracking-widest uppercase text-white/50">
          Cross-Venue Price Trajectory (NGN/USD)
        </CardTitle>
        <div className="flex items-center gap-3">
          {spread !== null && chartType !== 'spread' && chartType !== 'delta' && (
            <div className="flex items-center gap-2 text-[9px] font-mono uppercase tracking-widest">
              <span className="text-white/30">SPREAD:</span>
              <span className={spread > 100 ? 'text-yellow-500/80' : 'text-emerald-400'}>{spread} BPS</span>
            </div>
          )}
          {/* Time window */}
          <div className="flex gap-1 bg-black/20 p-1 rounded-sm border border-white/[0.02]">
            {TIME_WINDOWS.map(({ label, minutes }) => (
              <button
                key={label}
                onClick={() => { setWindowMinutes(minutes); applyWindow(minutes); }}
                className={`px-2 py-1 text-[9px] font-mono tracking-widest uppercase rounded-sm transition-colors ${
                  windowMinutes === minutes
                    ? 'bg-white/10 text-white/70 border border-white/10'
                    : 'text-white/30 hover:text-white/60 hover:bg-white/5 border border-transparent'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {/* Chart type */}
          <div className="flex gap-1 bg-black/20 p-1 rounded-sm border border-white/[0.02]">
            {CHART_TYPES.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setChartType(value)}
                className={`px-3 py-1 text-[9px] font-mono tracking-widest uppercase rounded-sm transition-colors ${
                  chartType === value
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    : 'text-white/30 hover:text-white/60 hover:bg-white/5 border border-transparent'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-2">
        <div className="relative">
          <div ref={containerRef} className="w-full" />
          {noData && (
            <div className="absolute inset-0 flex items-center justify-center text-[10px] font-mono uppercase tracking-widest text-white/30 animate-pulse">
              WAITING FOR ORACLE HISTORY...
            </div>
          )}
        </div>
        {chartType === 'area' && activeVenues.length > 0 && (
          <div className="flex items-center gap-4 mt-3 px-2">
            {activeVenues.map((venue) => (
              <div key={venue} className="flex items-center gap-1.5">
                <div className="w-3 h-[2px]" style={{ backgroundColor: VENUE_COLORS[venue] || '#888' }} />
                <span className="text-[9px] font-mono uppercase tracking-widest text-white/40">
                  {VENUE_LABELS[venue]?.name || venue}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-between items-center text-[9px] text-white/20 mt-3 font-mono tracking-wide border-t border-white/[0.02] pt-3 px-2">
          <span>&gt; DRAG TO PAN — SCROLL TO ZOOM{snapshots?.length ? ` — ${snapshots.length} DATAPOINTS` : ''}</span>
          <span>&gt; OPTIMAL VECTOR: SPREAD &gt; 110 BPS</span>
        </div>
      </CardContent>
    </Card>
  );
}
