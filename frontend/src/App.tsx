import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { SERIES_VARS } from "./format";
import { useLiveAgents } from "./store";
import type { AgentConfig, Meta } from "./types";
import { AddCard, AgentCard } from "./components/AgentCard";
import { AgentDetail } from "./components/AgentDetail";
import { AgentForm } from "./components/AgentForm";
import { ArenaDialog } from "./components/ArenaDialog";
import { Leaderboard } from "./components/Leaderboard";

type View = "grid" | "leaderboard";

export default function App() {
  const { agents, order, connected, reload } = useLiveAgents();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [view, setView] = useState<View>("grid");
  const [editing, setEditing] = useState<AgentConfig | "new" | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [arena, setArena] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const csvRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api.meta().then(setMeta).catch((e) => setToast(`Бэкенд недоступен: ${e.message}`)); }, []);
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  // Colour follows the agent (its position in the workspace), never its rank.
  const colorOf = useCallback((id: string) => {
    const i = order.indexOf(id);
    return i >= 0 && i < SERIES_VARS.length ? SERIES_VARS[i] : "var(--text-muted)";
  }, [order]);

  const runAll = async (action: "start" | "pause" | "stop") => {
    const targets = order.filter((id) => {
      const s = agents[id]?.status;
      if (action === "start") return !["running", "paused", "loading"].includes(s);
      if (action === "pause") return s === "running";
      return ["running", "paused", "loading"].includes(s);
    });
    await Promise.all(targets.map((id) => api.control(id, action).catch((e) => setToast(e.message))));
  };

  const exportYaml = async () => {
    const text = await api.exportYaml();
    const url = URL.createObjectURL(new Blob([text], { type: "text/yaml" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "invj-workspace.yaml" });
    a.click();
    URL.revokeObjectURL(url);
  };

  const importYaml = async (f: File) => {
    try {
      const ids = await api.importYaml(await f.text());
      setToast(`Импортировано агентов: ${ids.length}`);
      void reload();
    } catch (e) { setToast((e as Error).message); }
  };

  const uploadCsv = async (f: File) => {
    const symbol = prompt("Имя набора данных (символ):", f.name.replace(/\.csv$/i, ""));
    if (!symbol) return;
    try {
      const r = await api.uploadCsv(symbol, await f.text());
      setToast(`CSV «${r.symbol}» загружен: ${r.candles} свечей. Источник данных: csv`);
    } catch (e) { setToast((e as Error).message); }
  };

  const open = openId ? agents[openId] : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <b>InvJ</b><span className="muted">арена решений: Jev · Laya · GLiNER · LLM</span>
        </div>
        <nav className="seg">
          <button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")}>Агенты</button>
          <button className={view === "leaderboard" ? "on" : ""} onClick={() => setView("leaderboard")}>Лидерборд</button>
        </nav>
        <div className="actions">
          <button onClick={() => runAll("start")} disabled={!order.length}>▶ Все</button>
          <button onClick={() => runAll("pause")} disabled={!order.length}>⏸ Все</button>
          <button onClick={() => runAll("stop")} disabled={!order.length}>■ Все</button>
          <button onClick={() => setArena(true)} disabled={!order.length || !meta}>Арена</button>
          <details className="menu">
            <summary>Ещё</summary>
            <div className="menu-pop">
              <button onClick={exportYaml}>Экспорт конфигов (YAML)</button>
              <button onClick={() => importRef.current?.click()}>Импорт конфигов (YAML)</button>
              <button onClick={() => csvRef.current?.click()}>Загрузить CSV со свечами</button>
            </div>
          </details>
          <span className={`conn ${connected ? "ok" : "bad"}`} title={connected ? "live-соединение активно" : "нет соединения с бэкендом"}>
            {connected ? "● live" : "○ offline"}
          </span>
          {meta && !meta.openrouter_key_configured && <span className="tag warn" title="Задайте OPENROUTER_API_KEY в .env для Jev и LLM">нет ключа OpenRouter</span>}
        </div>
        <input ref={importRef} type="file" accept=".yaml,.yml" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void importYaml(f); e.target.value = ""; }} />
        <input ref={csvRef} type="file" accept=".csv" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadCsv(f); e.target.value = ""; }} />
      </header>

      <main>
        {view === "grid" ? (
          <div className="grid">
            {order.map((id) => agents[id] && (
              <AgentCard key={id} agent={agents[id]} color={colorOf(id)}
                onOpen={() => setOpenId(id)} onEdit={() => setEditing(agents[id].config)} onError={setToast} />
            ))}
            <AddCard onClick={() => setEditing("new")} />
          </div>
        ) : (
          <Leaderboard agents={agents} order={order} colorOf={colorOf} onOpen={setOpenId} />
        )}
      </main>

      {editing && meta && (
        <AgentForm meta={meta} initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void reload(); }} />
      )}
      {open && <AgentDetail agent={open} color={colorOf(open.config.id)} onClose={() => setOpenId(null)} />}
      {arena && meta && <ArenaDialog meta={meta} agents={agents} order={order} onClose={() => setArena(false)} onError={setToast} />}
      {toast && <div className="toast" role="status" onClick={() => setToast(null)}>{toast}</div>}
    </div>
  );
}
