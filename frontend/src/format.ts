export const usd = (v: number | null | undefined, digits = 4) =>
  v == null ? "—" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(digits)}` : `$${v.toFixed(2)}`;

export const pct = (v: number | null | undefined, digits = 2, sign = true) =>
  v == null ? "—" : `${sign && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits });

export const ms = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} с` : `${Math.round(v)} мс`;

export const time = (ts: number) =>
  new Date(ts).toISOString().slice(0, 16).replace("T", " ");

export const ACTION_LABEL: Record<string, string> = {
  open_long: "LONG",
  open_short: "SHORT",
  close: "CLOSE",
  hold: "HOLD",
};

export const STATUS_LABEL: Record<string, string> = {
  idle: "ожидает",
  loading: "загрузка",
  running: "идёт",
  paused: "пауза",
  finished: "готово",
  stopped: "остановлен",
  error: "ошибка",
};

export const PROVIDER_LABEL: Record<string, string> = {
  jev: "Jev · OpenRouter",
  systemone: "SystemOne HTTP",
  laya: "Laya · local",
  gliner: "GLiNER · local",
  llm: "LLM · OpenRouter",
  baseline: "Baseline",
};

/** Categorical slots in fixed order (reference palette, validated). Colour follows the agent, not its rank. */
export const SERIES_VARS = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
