import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { AgentLive, WsEvent } from "./types";

/** All agent windows, kept live from the backend WebSocket.
 *  Events are buffered and applied once per animation frame so fast replays
 *  (delay 0) don't re-render on every candle. */
export function useLiveAgents() {
  const [agents, setAgents] = useState<Record<string, AgentLive>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const buffer = useRef<WsEvent[]>([]);
  const frame = useRef<number | null>(null);

  const refreshOne = useCallback(async (id: string) => {
    try {
      const s = await api.state(id);
      setAgents((prev) => ({ ...prev, [id]: s }));
    } catch { /* deleted meanwhile */ }
  }, []);

  const reload = useCallback(async () => {
    const list = await api.agents();
    const snaps = await Promise.all(list.map((a) => api.state(a.config.id)));
    setAgents(Object.fromEntries(snaps.map((s) => [s.config.id, s])));
    setOrder(list.map((a) => a.config.id));
  }, []);

  const flush = useCallback(() => {
    frame.current = null;
    const events = buffer.current;
    buffer.current = [];
    let needReload = false;
    const refresh = new Set<string>();
    setAgents((prev) => {
      const next = { ...prev };
      for (const ev of events) {
        if (ev.type === "agents") { needReload = true; continue; }
        const a = next[ev.agent_id];
        if (!a) { needReload = true; continue; }
        if (ev.type === "status") {
          let upd: AgentLive = { ...a, status: ev.status, run_id: ev.run_id, error: ev.error };
          if (ev.status === "loading") {
            upd = { ...upd, candles: [], equity: [], steps: [], fills: [], trades: [], metrics: null, last_step: null };
          }
          if (ev.status === "running" && a.candles.length === 0) refresh.add(ev.agent_id);
          if (["finished", "stopped", "error", "paused"].includes(ev.status)) refresh.add(ev.agent_id);
          next[ev.agent_id] = upd;
        } else if (ev.type === "tick") {
          // Guards make ticks idempotent, so a snapshot that raced with them never duplicates points.
          const last = a.candles[a.candles.length - 1];
          const lastEq = a.equity[a.equity.length - 1];
          const lastStep = a.steps[a.steps.length - 1];
          const newStep = ev.step && (!lastStep || ev.step.step > lastStep.step) ? ev.step : null;
          next[ev.agent_id] = {
            ...a,
            run_id: ev.run_id,
            candles: !last || ev.candle.ts > last.ts ? [...a.candles, ev.candle] : a.candles,
            equity: !lastEq || ev.equity.ts > lastEq.ts ? [...a.equity, ev.equity] : a.equity,
            steps: newStep ? [...a.steps, newStep] : a.steps,
            fills: newStep?.fills.length ? [...a.fills, ...newStep.fills] : a.fills,
            last_step: ev.step ?? a.last_step,
            metrics: ev.metrics,
          };
        }
      }
      return next;
    });
    if (needReload) void reload();
    refresh.forEach((id) => void refreshOne(id));
  }, [reload, refreshOne]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let timer: number | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { retry = 0; setConnected(true); void reload(); };
      ws.onmessage = (m) => {
        buffer.current.push(JSON.parse(m.data) as WsEvent);
        if (frame.current === null) frame.current = requestAnimationFrame(flush);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(connect, Math.min(10_000, 500 * 2 ** retry++));
      };
    };
    connect();
    return () => { closed = true; window.clearTimeout(timer); ws?.close(); };
  }, [flush, reload]);

  return { agents, order, connected, reload, refreshOne };
}
