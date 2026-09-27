import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, Fill } from "../types";
import { cssVar } from "../format";

const t = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

// Some environments report tags like "en-US@posix" that Intl rejects; charts then throw on every tick.
function safeLocale(): string {
  try {
    return Intl.DateTimeFormat.supportedLocalesOf([navigator.language])[0] ?? "ru-RU";
  } catch {
    return "ru-RU";
  }
}
const LOCALE = safeLocale();

function chartOptions() {
  return {
    autoSize: true,
    localization: { locale: LOCALE },
    layout: {
      background: { type: ColorType.Solid, color: cssVar("--surface-1") },
      textColor: cssVar("--text-muted"),
      fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      fontSize: 11,
      attributionLogo: false,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: cssVar("--grid") },
    },
    rightPriceScale: { borderColor: cssVar("--baseline") },
    timeScale: { borderColor: cssVar("--baseline"), timeVisible: true, secondsVisible: false },
    crosshair: { vertLine: { labelBackgroundColor: cssVar("--text-secondary") }, horzLine: { labelBackgroundColor: cssVar("--text-secondary") } },
  };
}

/** Re-theme charts when the OS colour scheme flips. */
function useThemeChange(cb: () => void) {
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", cb);
    return () => mq.removeEventListener("change", cb);
  }, [cb]);
}

function markersFor(fills: Fill[]): SeriesMarker<Time>[] {
  const long = cssVar("--series-1");
  const short = cssVar("--series-2");
  const exit = cssVar("--text-secondary");
  return [...fills]
    .sort((a, b) => a.ts - b.ts)
    .map((f) => {
      if (f.reason === "open_long") return { time: t(f.ts), position: "belowBar", shape: "arrowUp", color: long, text: "L" };
      if (f.reason === "open_short") return { time: t(f.ts), position: "aboveBar", shape: "arrowDown", color: short, text: "S" };
      return { time: t(f.ts), position: f.side === "buy" ? "belowBar" : "aboveBar", shape: "circle", color: exit, text: "X" };
    });
}

export function PriceChart({ candles, fills, height = 200 }: { candles: Candle[]; fills: Fill[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const loaded = useRef<{ len: number; first: number | null }>({ len: 0, first: null });

  const theme = () => {
    chart.current?.applyOptions(chartOptions());
    series.current?.applyOptions({
      upColor: cssVar("--up"), downColor: cssVar("--down"), wickUpColor: cssVar("--up"), wickDownColor: cssVar("--down"),
    });
  };
  useThemeChange(theme);

  useEffect(() => {
    if (!el.current) return;
    chart.current = createChart(el.current, chartOptions());
    series.current = chart.current.addSeries(CandlestickSeries, { borderVisible: false });
    markers.current = createSeriesMarkers(series.current, []);
    theme();
    return () => {
      chart.current?.remove();
      chart.current = series.current = markers.current = null;
      loaded.current = { len: 0, first: null };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const bar = (c: Candle) => ({ time: t(c.ts), open: c.open, high: c.high, low: c.low, close: c.close });
    const prev = loaded.current;
    const first = candles[0]?.ts ?? null;
    if (prev.len > 0 && first === prev.first && candles.length >= prev.len) {
      for (let i = prev.len - 1; i < candles.length; i++) s.update(bar(candles[i]));
    } else {
      s.setData(candles.map(bar));
      chart.current?.timeScale().fitContent();
    }
    loaded.current = { len: candles.length, first };
  }, [candles]);

  useEffect(() => {
    markers.current?.setMarkers(markersFor(fills));
  }, [fills, candles.length === 0]);

  return <div ref={el} style={{ height, width: "100%" }} />;
}

export interface LineData { id: string; name: string; color: string; points: { ts: number; value: number }[] }

/** One or more lines on ONE shared axis (e.g. equity, or % return when comparing agents). */
export function LinesChart({ lines, height = 160, baseline }: { lines: LineData[]; height?: number; baseline?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Map<string, ISeriesApi<"Line">>>(new Map());

  const resolve = (c: string) => (c.startsWith("var(") ? cssVar(c.slice(4, -1)) : c);
  const theme = () => {
    chart.current?.applyOptions(chartOptions());
    for (const l of lines) series.current.get(l.id)?.applyOptions({ color: resolve(l.color) });
  };
  useThemeChange(theme);

  useEffect(() => {
    if (!el.current) return;
    chart.current = createChart(el.current, chartOptions());
    const map = series.current;
    return () => { chart.current?.remove(); chart.current = null; map.clear(); };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const keep = new Set(lines.map((l) => l.id));
    let changed = false;
    for (const [id, s] of series.current) if (!keep.has(id)) { c.removeSeries(s); series.current.delete(id); changed = true; }
    for (const l of lines) {
      let s = series.current.get(l.id);
      if (!s) {
        changed = true;
        s = c.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: lines.length > 1 ? l.name : "" });
        if (baseline !== undefined && series.current.size === 0) {
          s.createPriceLine({ price: baseline, color: cssVar("--baseline"), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false });
        }
        series.current.set(l.id, s);
      }
      s.applyOptions({ color: resolve(l.color) });
      const seen = new Set<number>();
      const pts = l.points.filter((p) => !seen.has(p.ts) && seen.add(p.ts));
      if (pts.length < 2) changed = true; // fresh run: re-fit as it grows from empty
      s.setData(pts.map((p) => ({ time: t(p.ts), value: p.value })));
    }
    if (changed) c.timeScale().fitContent(); // keep the user's zoom during live updates
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines]);

  return <div ref={el} style={{ height, width: "100%" }} />;
}
