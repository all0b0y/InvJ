import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ACTION_LABEL, ms, num, pct, PROVIDER_LABEL, STATUS_LABEL, time, usd } from "../format";
import type { AgentLive, Metrics, RunInfo, Step } from "../types";
import { LinesChart, PriceChart } from "./Charts";

type Tab = "steps" | "trades" | "metrics" | "runs";

export function AgentDetail({ agent, color, onClose }: { agent: AgentLive; color: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("steps");
  const [selected, setSelected] = useState<number | null>(null);
  const [pastRun, setPastRun] = useState<{ run: RunInfo; steps: Step[] } | null>(null);
  const cfg = agent.config;

  const steps = pastRun ? pastRun.steps : agent.steps;
  const runId = pastRun ? pastRun.run.id : agent.run_id;
  const metrics = pastRun ? pastRun.run.metrics : agent.metrics;

  const equityLines = useMemo(
    () => [{ id: "eq", name: "Капитал", color, points: agent.equity.map((e) => ({ ts: e.ts, value: e.equity })) }],
    [agent.equity, color],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal detail-modal" role="dialog" aria-label={`Агент ${cfg.name}`}>
        <header className="modal-head">
          <span className="swatch" style={{ background: color }} aria-hidden />
          <div className="title-block">
            <h2>{cfg.name}</h2>
            <div className="sub">
              {PROVIDER_LABEL[cfg.model.provider]} · <code>{cfg.model.model || "auto"}</code> · {cfg.data.symbol} {cfg.data.interval}
              {cfg.features.anonymize && " · anon"} · {STATUS_LABEL[agent.status]}
            </div>
          </div>
          <button className="ghost" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        <div className="modal-body detail-body">
          {pastRun ? (
            <div className="banner">Просмотр прошлого запуска <code>{pastRun.run.id}</code> от {new Date(pastRun.run.started_at * 1000).toLocaleString("ru-RU")}
              <button className="ghost" onClick={() => { setPastRun(null); setSelected(null); }}>вернуться к текущему</button></div>
          ) : (
            <div className="detail-charts">
              <figure>
                <figcaption>Цена и сделки <span className="muted">(L — лонг, S — шорт, X — закрытие)</span></figcaption>
                {agent.candles.length ? <PriceChart candles={agent.candles} fills={agent.fills} height={300} /> : <div className="empty-chart">нет данных</div>}
              </figure>
              <figure>
                <figcaption>Капитал, $</figcaption>
                {agent.equity.length ? <LinesChart lines={equityLines} height={140} baseline={cfg.broker.initial_cash} /> : <div className="empty-chart small">—</div>}
              </figure>
            </div>
          )}

          {metrics && <KpiRow m={metrics} />}

          <nav className="tabs" role="tablist">
            {([["steps", `Решения (${steps.length})`], ["trades", `Сделки (${agent.trades.length})`], ["metrics", "Метрики и затраты"], ["runs", "История запусков"]] as [Tab, string][])
              .map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
          </nav>

          {tab === "steps" && (
            <div className="split">
              <StepsTable steps={steps} selected={selected} onSelect={setSelected} />
              {selected !== null && runId && <StepInspector runId={runId} step={selected} />}
            </div>
          )}
          {tab === "trades" && <TradesTable agent={agent} />}
          {tab === "metrics" && metrics && <MetricsView m={metrics} />}
          {tab === "runs" && <RunsView agentId={cfg.id} onOpen={async (run) => {
            setPastRun({ run, steps: await api.runSteps(run.id) });
            setSelected(null);
            setTab("steps");
          }} />}
        </div>
      </div>
    </div>
  );
}

function KpiRow({ m }: { m: Metrics }) {
  const t = m.trading;
  return (
    <dl className="kpis">
      <div><dt>Капитал</dt><dd>{num(t.equity)}</dd></div>
      <div><dt>Доходность</dt><dd className={t.return_pct > 0 ? "pos" : t.return_pct < 0 ? "neg" : ""}>{pct(t.return_pct)}</dd></div>
      <div><dt>Buy & Hold</dt><dd>{pct(t.buy_hold_return_pct)}</dd></div>
      <div><dt>Макс. просадка</dt><dd>{pct(-t.max_drawdown_pct)}</dd></div>
      <div><dt>Sharpe</dt><dd>{num(t.sharpe)}</dd></div>
      <div><dt>Точность прогноза</dt><dd>{m.forecast.accuracy_pct != null ? `${m.forecast.accuracy_pct}%` : "—"}</dd></div>
      <div><dt>Потрачено</dt><dd>{usd(m.cost.total_usd)}</dd></div>
      <div><dt>PnL на $1 затрат</dt><dd>{m.cost.pnl_per_usd != null ? `$${num(m.cost.pnl_per_usd)}` : "—"}</dd></div>
    </dl>
  );
}

function StepsTable({ steps, selected, onSelect }: { steps: Step[]; selected: number | null; onSelect: (n: number) => void }) {
  const [onlyTrades, setOnlyTrades] = useState(false);
  const rows = useMemo(() => {
    const r = onlyTrades ? steps.filter((s) => s.fills.length || s.error) : steps;
    return [...r].reverse().slice(0, 1000);
  }, [steps, onlyTrades]);
  return (
    <div className="table-wrap">
      <label className="check small"><input type="checkbox" checked={onlyTrades} onChange={(e) => setOnlyTrades(e.target.checked)} />только сделки и ошибки</label>
      <table className="data">
        <thead><tr><th>#</th><th>Время</th><th className="r">Цена</th><th>Действие</th><th className="r">Размер</th><th>Прогноз</th><th className="r">Увер.</th><th className="r">Капитал</th><th className="r">Цена вызова</th><th className="r">Время</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.step} className={selected === s.step ? "sel" : ""} onClick={() => onSelect(s.step)}>
              <td>{s.step}</td>
              <td>{time(s.ts)}</td>
              <td className="r">{num(s.price, 4)}</td>
              <td><span className={`act act-${s.action}`}>{ACTION_LABEL[s.action] ?? s.action}</span>{s.fills.length > 0 && " ✓"}{s.error && <span className="err" title={s.error}> ⚠</span>}</td>
              <td className="r">{s.size_pct != null ? `${s.size_pct}%` : "—"}</td>
              <td>{s.forecast ?? "—"}</td>
              <td className="r">{conf(s)}</td>
              <td className="r">{num(s.equity)}</td>
              <td className="r">{usd(s.cost_usd, 6)}</td>
              <td className="r">{ms(s.latency_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {steps.length > 1000 && <p className="muted small">показаны последние 1000 решений</p>}
    </div>
  );
}

function conf(s: Step) {
  const c = s.answers.action?.confidence ?? Object.values(s.answers)[0]?.confidence;
  return c == null ? "—" : `${Math.round(c * 100)}%`;
}

function StepInspector({ runId, step }: { runId: string; step: number }) {
  const [data, setData] = useState<Step | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setData(null); setErr(null);
    api.step(runId, step).then(setData).catch((e) => setErr((e as Error).message));
  }, [runId, step]);
  if (err) return <aside className="inspector"><p className="err">{err}</p></aside>;
  if (!data) return <aside className="inspector"><p className="muted">загрузка…</p></aside>;
  return (
    <aside className="inspector">
      <h4>Шаг {data.step} · {time(data.ts)} · цена {num(data.price, 6)}</h4>
      {data.error && <p className="err">⚠ {data.error}</p>}
      {data.reasoning && <blockquote>{data.reasoning}</blockquote>}
      {Object.entries(data.answers).map(([qid, a]) => (
        <div key={qid} className="answer">
          <div className="answer-head"><b>{qid}</b> → <code>{a.choice}</code>{a.confidence != null && <span className="muted"> · уверенность {(a.confidence * 100).toFixed(0)}%</span>}</div>
          {a.probabilities && (
            <ul className="probs">
              {Object.entries(a.probabilities).sort((x, y) => y[1] - x[1]).map(([k, v]) => (
                <li key={k} className={k === a.choice ? "chosen" : ""}>
                  <span className="k">{k}</span>
                  <span className="bar"><span style={{ width: `${Math.max(1, v * 100)}%` }} /></span>
                  <span className="v">{(v * 100).toFixed(1)}%</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      <p className="muted small">
        Токены: {data.usage.input_tokens} вход / {data.usage.output_tokens} выход · {usd(data.cost_usd, 6)} · {ms(data.latency_ms)}
      </p>
      {data.fills.length > 0 && (
        <p className="small">Исполнено: {data.fills.map((f) => `${f.reason} ${f.side} ${num(f.qty, 6)} @ ${num(f.price, 6)} (комиссия ${num(f.fee, 4)})`).join("; ")}</p>
      )}
      <details><summary>Запрос к модели (как отправлен)</summary><pre>{pretty(data.request)}</pre></details>
      <details><summary>Ответ модели (как получен)</summary><pre>{pretty(data.response)}</pre></details>
    </aside>
  );
}

function pretty(v: unknown) {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

function TradesTable({ agent }: { agent: AgentLive }) {
  if (!agent.trades.length) return <p className="muted">Закрытых сделок пока нет.</p>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Сторона</th><th>Вход</th><th>Выход</th><th className="r">Цена входа</th><th className="r">Цена выхода</th><th className="r">Кол-во</th><th className="r">PnL, $</th><th className="r">PnL, %</th></tr></thead>
        <tbody>
          {[...agent.trades].reverse().map((t, i) => (
            <tr key={i}>
              <td>{t.side}</td><td>{time(t.entry_ts)}</td><td>{time(t.exit_ts)}</td>
              <td className="r">{num(t.entry_price, 6)}</td><td className="r">{num(t.exit_price, 6)}</td>
              <td className="r">{num(t.qty, 6)}</td>
              <td className={`r ${t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : ""}`}>{num(t.pnl)}</td>
              <td className={`r ${t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : ""}`}>{pct(t.return_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricsView({ m }: { m: Metrics }) {
  const f = m.forecast;
  const keys = ["up", "down", "flat"];
  return (
    <div className="metrics-grid">
      <section>
        <h4>Торговля</h4>
        <table className="kv"><tbody>
          <tr><th>Сделок (закрытых)</th><td>{m.trading.trades}</td></tr>
          <tr><th>Win rate</th><td>{pct(m.trading.win_rate_pct, 1, false)}</td></tr>
          <tr><th>Средняя сделка</th><td>{pct(m.trading.avg_trade_pct)}</td></tr>
          <tr><th>Время в позиции</th><td>{pct(m.trading.exposure_pct, 1, false)}</td></tr>
          <tr><th>Макс. просадка</th><td>{pct(m.trading.max_drawdown_pct, 2, false)}</td></tr>
          <tr><th>Sharpe (годовой)</th><td>{num(m.trading.sharpe)}</td></tr>
        </tbody></table>
      </section>
      <section>
        <h4>Прогноз направления</h4>
        <table className="kv"><tbody>
          <tr><th>Оценено прогнозов</th><td>{f.scored}</td></tr>
          <tr><th>Точность</th><td>{pct(f.accuracy_pct, 1, false)}</td></tr>
          <tr><th>Доля частого исхода</th><td title="сколько дала бы стратегия «всегда отвечать самым частым исходом»">{pct(f.majority_class_pct, 1, false)}</td></tr>
          <tr><th>Brier (меньше — лучше)</th><td>{f.brier ?? "—"}</td></tr>
          <tr><th>Точность входов (L/S)</th><td>{pct(f.entry_direction_accuracy_pct, 1, false)} из {f.entry_direction_scored}</td></tr>
        </tbody></table>
        {f.scored > 0 && (
          <table className="data confusion">
            <thead><tr><th>прогноз ↓ / факт →</th>{keys.map((k) => <th key={k} className="r">{k}</th>)}</tr></thead>
            <tbody>{keys.map((p) => <tr key={p}><th>{p}</th>{keys.map((r) => <td key={r} className={`r ${p === r ? "diag" : ""}`}>{f.confusion[p]?.[r] ?? 0}</td>)}</tr>)}</tbody>
          </table>
        )}
      </section>
      <section>
        <h4>Затраты</h4>
        <table className="kv"><tbody>
          <tr><th>Всего</th><td>{usd(m.cost.total_usd, 6)}</td></tr>
          <tr><th>За решение</th><td>{usd(m.cost.per_decision_usd, 6)}</td></tr>
          <tr><th>PnL на $1 затрат</th><td>{m.cost.pnl_per_usd != null ? `$${num(m.cost.pnl_per_usd)}` : "—"}</td></tr>
          <tr><th>Токены вход / выход</th><td>{num(m.cost.input_tokens, 0)} / {num(m.cost.output_tokens, 0)}</td></tr>
          <tr><th>Средняя задержка</th><td>{ms(m.cost.avg_latency_ms)}</td></tr>
          <tr><th>Ошибок</th><td>{m.cost.errors}</td></tr>
        </tbody></table>
        <table className="data">
          <thead><tr><th>Решение</th><th className="r">Раз</th><th className="r">Потрачено</th><th className="r">Ср. задержка</th></tr></thead>
          <tbody>{Object.entries(m.cost.by_action).map(([a, v]) => (
            <tr key={a}><td><span className={`act act-${a}`}>{ACTION_LABEL[a] ?? a}</span></td><td className="r">{v.count}</td><td className="r">{usd(v.cost_usd, 6)}</td><td className="r">{ms(v.avg_latency_ms)}</td></tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  );
}

function RunsView({ agentId, onOpen }: { agentId: string; onOpen: (r: RunInfo) => void }) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null);
  useEffect(() => { api.runs(agentId).then(setRuns).catch(() => setRuns([])); }, [agentId]);
  if (!runs) return <p className="muted">загрузка…</p>;
  if (!runs.length) return <p className="muted">Запусков ещё не было.</p>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Запуск</th><th>Начат</th><th>Статус</th><th>Модель</th><th>Данные</th><th className="r">Решений</th><th className="r">Доходность</th><th className="r">B&H</th><th className="r">Прогноз</th><th className="r">Затраты</th><th /></tr></thead>
        <tbody>{runs.map((r) => (
          <tr key={r.id}>
            <td><code>{r.id}</code></td>
            <td>{new Date(r.started_at * 1000).toLocaleString("ru-RU")}</td>
            <td>{r.status}{r.error && <span className="err" title={r.error}> ⚠</span>}</td>
            <td>{r.model.provider}/{r.model.model}</td>
            <td>{r.data.symbol} {r.data.interval}</td>
            <td className="r">{r.metrics?.decisions ?? "—"}</td>
            <td className="r">{pct(r.metrics?.trading.return_pct)}</td>
            <td className="r">{pct(r.metrics?.trading.buy_hold_return_pct)}</td>
            <td className="r">{pct(r.metrics?.forecast.accuracy_pct, 1, false)}</td>
            <td className="r">{usd(r.metrics?.cost.total_usd)}</td>
            <td><button className="ghost small" onClick={() => onOpen(r)}>решения</button></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
