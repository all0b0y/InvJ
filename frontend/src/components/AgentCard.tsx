import { useState } from "react";
import { api } from "../api";
import { ACTION_LABEL, ms, pct, PROVIDER_LABEL, STATUS_LABEL, usd } from "../format";
import type { AgentLive } from "../types";
import { PriceChart } from "./Charts";

interface Props {
  agent: AgentLive;
  color: string;
  onOpen: () => void;
  onEdit: () => void;
  onError: (msg: string) => void;
}

export function AgentCard({ agent, color, onOpen, onEdit, onError }: Props) {
  const [busy, setBusy] = useState(false);
  const { config: cfg, status, metrics: m } = agent;
  const active = status === "running" || status === "paused" || status === "loading";
  const last = agent.last_step;
  const prog = m?.progress;
  const progress = prog && prog.total > prog.start ? ((prog.idx - prog.start) / (prog.total - prog.start)) * 100 : 0;

  const run = async (action: "start" | "pause" | "resume" | "step" | "stop") => {
    setBusy(true);
    try { await api.control(cfg.id, action); } catch (e) { onError(String((e as Error).message)); }
    setBusy(false);
  };
  const remove = async () => {
    if (!confirm(`Удалить агента «${cfg.name}»? История запусков сохранится.`)) return;
    try { await api.remove(cfg.id); } catch (e) { onError(String((e as Error).message)); }
  };

  return (
    <article className="card agent-card">
      <header className="card-head">
        <span className="swatch" style={{ background: color }} aria-hidden />
        <div className="title-block">
          <h3 title={cfg.name}>{cfg.name}</h3>
          <div className="sub">
            {PROVIDER_LABEL[cfg.model.provider]} · <code>{cfg.model.model || "auto"}</code>
          </div>
        </div>
        <span className={`pill status-${status}`}>{STATUS_LABEL[status] ?? status}</span>
      </header>

      <div className="sub data-line">
        {cfg.data.symbol} · {cfg.data.interval} · {cfg.data.provider}
        {cfg.data.start ? ` · с ${cfg.data.start}` : ""}
        {cfg.features.anonymize && <span className="tag">anon</span>}
      </div>

      <div className="chart-box" onClick={onOpen} role="button" tabIndex={0} title="Открыть подробности"
        onKeyDown={(e) => e.key === "Enter" && onOpen()}>
        {agent.candles.length ? <PriceChart candles={agent.candles} fills={agent.fills} height={170} />
          : <div className="empty-chart">{status === "loading" ? "Загрузка данных…" : "Нажмите ▶, чтобы начать прогон"}</div>}
      </div>
      <div className="progress" aria-label="прогресс"><div style={{ width: `${progress}%` }} /></div>

      <dl className="stats">
        <div><dt>Доходность</dt><dd className={sign(m?.trading.return_pct)}>{pct(m?.trading.return_pct)}</dd></div>
        <div><dt>Buy&Hold</dt><dd>{pct(m?.trading.buy_hold_return_pct)}</dd></div>
        <div><dt>Сделки / win</dt><dd>{m ? `${m.trading.trades} / ${m.trading.win_rate_pct ?? "—"}%` : "—"}</dd></div>
        <div><dt>Прогноз</dt><dd title="точность прогноза направления (в скобках — доля самого частого исхода)">
          {m?.forecast.accuracy_pct != null ? `${m.forecast.accuracy_pct}% (${m.forecast.majority_class_pct}%)` : "—"}</dd></div>
        <div><dt>Потрачено</dt><dd>{usd(m?.cost.total_usd)}</dd></div>
        <div><dt>Задержка</dt><dd>{ms(m?.cost.avg_latency_ms)}</dd></div>
      </dl>

      <div className="last-step">
        {last ? (
          <>
            <span className={`act act-${last.action}`}>{ACTION_LABEL[last.action] ?? last.action}</span>
            {last.forecast && <span className="tag">прогноз: {last.forecast}</span>}
            {last.error ? <span className="err" title={last.error}>⚠ {last.error}</span>
              : <span className="reason" title={last.reasoning ?? ""}>{last.reasoning ?? confText(last.answers.action?.confidence)}</span>}
          </>
        ) : agent.error ? <span className="err" title={agent.error}>⚠ {agent.error}</span> : <span className="muted">решений пока нет</span>}
      </div>

      <footer className="controls">
        {!active && <button onClick={() => run("start")} disabled={busy} title="Запустить прогон заново">▶ Старт</button>}
        {status === "running" && <button onClick={() => run("pause")} disabled={busy}>⏸ Пауза</button>}
        {status === "paused" && <button onClick={() => run("resume")} disabled={busy}>▶ Дальше</button>}
        {(status === "paused" || !active) && <button onClick={() => run("step")} disabled={busy} title="Один шаг решения">⏭ Шаг</button>}
        {active && <button onClick={() => run("stop")} disabled={busy}>■ Стоп</button>}
        <span className="spacer" />
        <button className="ghost" onClick={onOpen} title="Подробности">Детали</button>
        <button className="ghost" onClick={onEdit} disabled={active} title={active ? "Остановите агента, чтобы менять конфиг" : "Настроить"}>⚙</button>
        <button className="ghost danger" onClick={remove} title="Удалить" aria-label="Удалить">✕</button>
      </footer>
    </article>
  );
}

function sign(v: number | null | undefined) {
  return v == null || v === 0 ? "" : v > 0 ? "pos" : "neg";
}

function confText(c: number | null | undefined) {
  return c == null ? "" : `уверенность ${(c * 100).toFixed(0)}%`;
}

export function AddCard({ onClick }: { onClick: () => void }) {
  return (
    <button className="card add-card" onClick={onClick}>
      <span className="plus" aria-hidden>＋</span>
      <span>Добавить агента</span>
      <span className="muted">Jev, Laya, GLiNER, LLM или бейзлайн</span>
    </button>
  );
}
