import { useState } from "react";
import { api } from "../api";
import type { AgentLive, DataConfig, Meta } from "../types";

export function ArenaDialog({ meta, agents, order, onClose, onError }: {
  meta: Meta; agents: Record<string, AgentLive>; order: string[]; onClose: () => void; onError: (m: string) => void;
}) {
  const list = order.map((id) => agents[id]).filter(Boolean);
  const [picked, setPicked] = useState<Set<string>>(new Set(list.map((a) => a.config.id)));
  const [data, setData] = useState<DataConfig>(() => ({ ...(list[0]?.config.data ?? meta.defaults.data) }));
  const [syncFeatures, setSyncFeatures] = useState(false);
  const [busy, setBusy] = useState(false);

  const toggle = (id: string) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const set = <K extends keyof DataConfig>(k: K, v: DataConfig[K]) => setData((d) => ({ ...d, [k]: v }));

  const go = async () => {
    setBusy(true);
    try {
      await api.arena([...picked], data, syncFeatures);
      onClose();
    } catch (e) { onError((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal form-modal" role="dialog" aria-label="Арена">
        <header className="modal-head"><h2>Арена: все на одних свечах</h2><button className="ghost" onClick={onClose} aria-label="Закрыть">✕</button></header>
        <div className="modal-body">
          <p className="muted">Выбранные агенты остановятся, получат одинаковые данные и стартуют одновременно. Свечи загружаются один раз и общие для всех.</p>
          <section>
            <h4>Участники</h4>
            <div className="pick-list">
              {list.map((a) => (
                <label key={a.config.id} className="check">
                  <input type="checkbox" checked={picked.has(a.config.id)} onChange={() => toggle(a.config.id)} />
                  {a.config.name} <span className="muted">({a.config.model.provider}/{a.config.model.model || "auto"})</span>
                </label>
              ))}
            </div>
          </section>
          <section className="grid2">
            <h4>Данные</h4>
            <label>Источник<select value={data.provider} onChange={(e) => set("provider", e.target.value)}>
              {meta.data_providers.map((d) => <option key={d.name}>{d.name}</option>)}</select></label>
            <label>Символ<input value={data.symbol} onChange={(e) => set("symbol", e.target.value)} /></label>
            <label>Интервал<select value={data.interval} onChange={(e) => set("interval", e.target.value)}>
              {meta.intervals.map((i) => <option key={i}>{i}</option>)}</select></label>
            <label>Свечей<input type="number" value={data.limit} onChange={(e) => set("limit", Number(e.target.value))} /></label>
            <label>Начало<input type="date" value={data.start?.slice(0, 10) ?? ""} onChange={(e) => set("start", e.target.value || null)} /></label>
            <label>Конец<input type="date" value={data.end?.slice(0, 10) ?? ""} onChange={(e) => set("end", e.target.value || null)} /></label>
            <label className="check wide"><input type="checkbox" checked={syncFeatures} onChange={(e) => setSyncFeatures(e.target.checked)} />
              Также выровнять входные признаки (окно, индикаторы, анонимизация) по первому агенту</label>
          </section>
        </div>
        <footer className="modal-foot">
          <span className="spacer" />
          <button className="ghost" onClick={onClose}>Отмена</button>
          <button className="primary" onClick={go} disabled={busy || picked.size === 0}>Старт арены ({picked.size})</button>
        </footer>
      </div>
    </div>
  );
}
