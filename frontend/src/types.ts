// Mirrors backend/app/schemas.py

export type Provider = "jev" | "systemone" | "laya" | "gliner" | "llm" | "baseline";

export interface ModelConfig {
  provider: Provider;
  model: string;
  base_url: string | null;
  api_key_env: string | null;
  params: Record<string, unknown>;
  cost_per_hour_usd: number;
  state_format: "text" | "json";
}

export interface DataConfig {
  provider: string;
  symbol: string;
  interval: string;
  start: string | null;
  end: string | null;
  limit: number;
}

export interface FeatureConfig {
  name: string;
  window: number;
  indicators: string[];
  anonymize: boolean;
}

export interface QuestionSpec {
  id: string;
  type: "choice";
  instructions: string;
  options: Record<string, string>;
  role: "action" | "size" | "forecast" | null;
}

export interface AgentConfig {
  id: string;
  name: string;
  model: ModelConfig;
  data: DataConfig;
  features: FeatureConfig;
  decision: { schema_name: string; questions: QuestionSpec[]; sizing: boolean; forecast: boolean };
  broker: { initial_cash: number; fee_pct: number; slippage_pct: number; allow_short: boolean; default_size_pct: number };
  run: {
    decide_every: number; delay_ms: number; max_steps: number | null; forecast_horizon: number;
    flat_threshold_pct: number; max_consecutive_errors: number;
  };
}

export interface Candle { ts: number; open: number; high: number; low: number; close: number; volume: number }

export interface Answer { choice: string; probabilities: Record<string, number> | null; confidence: number | null }

export interface Fill { ts: number; side: "buy" | "sell"; qty: number; price: number; fee: number; reason: string; realized_pnl: number }

export interface Step {
  agent_id: string;
  run_id: string;
  step: number;
  idx: number;
  ts: number;
  price: number;
  action: string;
  size_pct: number | null;
  forecast: string | null;
  forecast_probs: Record<string, number> | null;
  answers: Record<string, Answer>;
  reasoning: string | null;
  fills: Fill[];
  position: { qty: number; entry_price: number };
  cash: number;
  equity: number;
  cost_usd: number;
  latency_ms: number;
  usage: { input_tokens: number; output_tokens: number };
  error: string | null;
  request?: unknown;
  response?: unknown;
}

export interface Trade {
  side: string; entry_ts: number; exit_ts: number; entry_price: number; exit_price: number;
  qty: number; pnl: number; return_pct: number;
}

export interface Metrics {
  trading: {
    equity: number; pnl: number; return_pct: number; buy_hold_return_pct: number | null;
    max_drawdown_pct: number; sharpe: number | null; trades: number; win_rate_pct: number | null;
    avg_trade_pct: number | null; exposure_pct: number;
  };
  forecast: {
    scored: number; accuracy_pct: number | null; majority_class_pct: number | null; brier: number | null;
    confusion: Record<string, Record<string, number>>; outcomes: Record<string, number>;
    entry_direction_scored: number; entry_direction_accuracy_pct: number | null;
  };
  cost: {
    total_usd: number; per_decision_usd: number; pnl_per_usd: number | null; input_tokens: number;
    output_tokens: number; avg_latency_ms: number; errors: number;
    by_action: Record<string, { count: number; cost_usd: number; avg_latency_ms: number }>;
  };
  decisions: number;
  progress: { idx: number; start: number; total: number };
}

export type Status = "idle" | "loading" | "running" | "paused" | "finished" | "stopped" | "error";

export interface AgentSummary {
  config: AgentConfig;
  status: Status;
  error: string | null;
  run_id: string | null;
  metrics: Metrics | null;
  last_step: Step | null;
}

export interface EquityPoint { ts: number; equity: number }

export interface AgentLive extends AgentSummary {
  candles: Candle[];
  start_idx: number;
  equity: EquityPoint[];
  fills: Fill[];
  trades: Trade[];
  steps: Step[];
}

export interface Meta {
  data_providers: { name: string; description: string }[];
  intervals: string[];
  model_providers: Provider[];
  baseline_strategies: string[];
  featurizers: string[];
  indicators: string[];
  decision_schemas: string[];
  actions: Record<string, string>;
  openrouter_key_configured: boolean;
  defaults: AgentConfig;
}

export interface PresetInfo { name: string; title?: string; description?: string; provider?: string; error?: string }

export interface RunInfo {
  id: string; agent_id: string; status: string; started_at: number; finished_at: number | null;
  error: string | null; name: string; model: ModelConfig; data: DataConfig; metrics: Metrics | null;
}

export type WsEvent =
  | { type: "agents" }
  | { type: "status"; agent_id: string; status: Status; run_id: string | null; error: string | null }
  | { type: "tick"; agent_id: string; run_id: string; candle: Candle; equity: EquityPoint; step: Step | null; metrics: Metrics };
