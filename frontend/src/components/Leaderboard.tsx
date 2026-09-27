import { useMemo, useState } from "react";
import { ms, pct, PROVIDER_LABEL, STATUS_LABEL, usd } from "../format";
import type { AgentLive } from "../types";
import { LinesChart, type LineData } from "./Charts";

type SortKey = "return" | "forecast" | "cost" | "sharpe" | "dd";

const dataKey = (a: AgentLive) => {
  const d = a.config.data;
  return `${d.provider}:${d.symbol}:${d.interval}:${d.start ?? ""}:${d.end ?? ""}:${d.limit}`;
};

export function Leaderboard({ agents, order, colorOf, onOpen }: {
  agents: Record<string, AgentLive>; order: string[]; colorOf: (id: string) => string; onOpen: (id: string) => void;
}) {
  const list = order.map((id) => agents[id]).filter(Boolean);
  const groups = useMemo(() => {
    const g = new Map<string, AgentLive[]>();
    for (const a of list) g.set(dataKey(a), [...(g.get(dataKey(a)) ?? []), a]);
    return [...g.entries()].sort((x, y) => y[1].length - x[1].length);
  }, [list]);
  const [group, setGroup] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("return");
  const activeKey = group && groups.some(([k]) => k === group) ? group : groups[0]?.[0] ?? null;
  const members = groups.find(([k]) => k === activeKey)?.[1] ?? [];

  const lines: LineData[] = useMemo(() => members.slice(0, 8).map((a) => ({
    id: a.config.id, name: a.config.name, color: colorOf(a.config.id),
    points: a.equity.map((e) => ({ ts: e.ts, value: (e.equity / a.config.broker.initial_cash - 1) * 100 })),
  })), [members, colorOf]);

  const val = (a: AgentLive): number => {
    const m = a.metrics;
    if (!m) return -Infinity;
    switch (sort) {
      case "return": return m.trading.return_pct;
      case "forecast": return m.forecast.accuracy_pct ?? -Infinity;
      case "cost": return -(m.cost.total_usd);
      case "sharpe": return m.trading.sharpe ?? -Infinity;
      case "dd": return -m.trading.max_drawdown_pct;
    }
  };
  const rows = [...members].sort((a, b) => val(b) - val(a));
  const th = (k: SortKey, label: string, title?: string) => (
    <th className={`r sortable ${sort === k ? "on" : ""}`} onClick={() => setSort(k)} title={title}>{label}{sort === k ? " ▾" : ""}</th>
  );

  if (!list.length) return <p className="muted center">Добавьте агентов, чтобы сравнить их.</p>;

  return (
    <div className="leaderboard">
      <div className="lb-head">
        <label>Набор данных
          <select value={activeKey ?? ""} onChange={(e) => setGroup(e.target.value)}>
            {groups.map(([k, g]) => <option key={k} value={k}>{g[0].config.data.symbol} {g[0].config.data.interval} · {g[0].config.data.provider}{g[0].config.data.start ? ` · с ${g[0].config.data.start}` : ""} — агентов: {g.length}</option>)}
          </select>
        </label>
        <p className="muted small">Сравниваются только агенты на одних и тех же свечах. Используйте «Арену», чтобы выровнять данные.</p>
      </div>

      <figure className="card">
        <figcaption>Доходность, % от стартового капитала {members.length > 8 && <span className="muted">(на графике первые 8 агентов)</span>}</figcaption>
        <LinesChart lines={lines} height={260} baseline={0} />
        <ul className="legend">
          {lines.map((l) => <li key={l.id}><span className="swatch" style={{ background: l.color }} />{l.name}</li>)}
        </ul>
      </figure>

      <div className="table-wrap card">
        <table className="data">
          <thead><tr>
            <th>Агент</th><th>Модель</th><th>Статус</th>
            {th("return", "Доходность")}<th className="r">B&H</th>{th("dd", "Просадка")}{th("sharpe", "Sharpe")}
            <th className="r">Сделки</th><th className="r">Win</th>
            {th("forecast", "Прогноз", "точность прогноза; в скобках доля самого частого исхода")}<th className="r">Brier</th>
            {th("cost", "Затраты")}<th className="r">PnL/$</th><th className="r">Задержка</th><th className="r">Ошибки</th>
          </tr></thead>
          <tbody>{rows.map((a) => {
            const m = a.metrics;
            return (
              <tr key={a.config.id} onClick={() => onOpen(a.config.id)} className="clickable">
                <td><span className="swatch" style={{ background: colorOf(a.config.id) }} /> {a.config.name}{a.config.features.anonymize && <span className="tag">anon</span>}</td>
                <td className="small">{PROVIDER_LABEL[a.config.model.provider]}<br /><code>{a.config.model.model || "auto"}</code></td>
                <td>{STATUS_LABEL[a.status]}</td>
                <td className={`r ${(m?.trading.return_pct ?? 0) > 0 ? "pos" : (m?.trading.return_pct ?? 0) < 0 ? "neg" : ""}`}>{pct(m?.trading.return_pct)}</td>
                <td className="r">{pct(m?.trading.buy_hold_return_pct)}</td>
                <td className="r">{m ? pct(-m.trading.max_drawdown_pct) : "—"}</td>
                <td className="r">{m?.trading.sharpe ?? "—"}</td>
                <td className="r">{m?.trading.trades ?? "—"}</td>
                <td className="r">{pct(m?.trading.win_rate_pct, 0, false)}</td>
                <td className="r">{m?.forecast.accuracy_pct != null ? `${m.forecast.accuracy_pct}% (${m.forecast.majority_class_pct}%)` : "—"}</td>
                <td className="r">{m?.forecast.brier ?? "—"}</td>
                <td className="r">{usd(m?.cost.total_usd)}</td>
                <td className="r">{m?.cost.pnl_per_usd != null ? `$${m.cost.pnl_per_usd}` : "—"}</td>
                <td className="r">{ms(m?.cost.avg_latency_ms)}</td>
                <td className="r">{m?.cost.errors ?? "—"}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
    </div>
  );
}
