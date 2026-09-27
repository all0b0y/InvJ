"""Shared data types.

Everything that describes *what an agent is* lives in ``AgentConfig`` and is plain
JSON/YAML, so presets, exports and the UI form all speak the same structure.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- market data


class Candle(BaseModel):
    ts: int  # open time, epoch milliseconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


# --------------------------------------------------------------------------- agent config


class ModelConfig(BaseModel):
    """Which decision maker drives the agent.

    provider:
      jev       - TypeSafe Jev through OpenRouter's /v1/systemone
      systemone - any server speaking the Jev wire protocol (e.g. ``laya-serve``)
      laya      - Laya in-process (``pip install laya``)
      gliner    - GLiNER in-process, used as a zero-shot classifier
      llm       - any chat model on OpenRouter, answers forced into JSON
      baseline  - deterministic reference strategies (free, no network)
    """

    provider: Literal["jev", "systemone", "laya", "gliner", "llm", "baseline"] = "baseline"
    model: str = "sma_cross"
    base_url: Optional[str] = None
    # Name of an env var holding a per-agent key; falls back to OPENROUTER_API_KEY.
    api_key_env: Optional[str] = None
    # Provider-specific knobs (temperature, max_tokens, device, threshold, ...).
    params: dict[str, Any] = Field(default_factory=dict)
    # For local models: $ equivalent of one hour of compute, cost = latency * rate.
    cost_per_hour_usd: float = 0.0
    # How the featurized state is sent: rendered text or the raw JSON object.
    state_format: Literal["text", "json"] = "text"


class DataConfig(BaseModel):
    provider: Literal["binance", "csv"] = "binance"
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    start: Optional[str] = None  # ISO date/datetime, UTC
    end: Optional[str] = None
    limit: int = 500  # max candles to load (after start)

    def key(self) -> str:
        return f"{self.provider}:{self.symbol}:{self.interval}:{self.start}:{self.end}:{self.limit}"


class FeatureConfig(BaseModel):
    name: str = "default"
    window: int = 30  # candles shown to the model
    indicators: list[str] = Field(default_factory=lambda: ["sma20", "ema50", "rsi14", "atr14", "vol_ratio"])
    # Hide ticker + dates and rebase prices to 100, so the model cannot recall history.
    anonymize: bool = False


class QuestionSpec(BaseModel):
    """One typed question, Jev/Laya style. ``role`` tells the engine how to use the answer."""

    id: str
    type: Literal["choice"] = "choice"
    instructions: str
    options: dict[str, str]  # option key -> description (Jev "criteria")
    role: Optional[Literal["action", "size", "forecast"]] = None


class DecisionConfig(BaseModel):
    schema_name: str = "default"  # a registered schema, or "custom"
    questions: list[QuestionSpec] = Field(default_factory=list)  # used when schema_name == "custom"
    sizing: bool = True  # ask for position size
    forecast: bool = True  # ask for a direction forecast (scored separately from PnL)


class BrokerConfig(BaseModel):
    initial_cash: float = 10_000.0
    fee_pct: float = 0.1  # per fill, % of notional
    slippage_pct: float = 0.05  # adverse price move per fill, %
    allow_short: bool = True
    default_size_pct: float = 100.0  # used when sizing is off


class RunConfig(BaseModel):
    decide_every: int = 1  # ask the model every N candles
    delay_ms: int = 0  # pause between steps, for watching live
    max_steps: Optional[int] = None
    forecast_horizon: int = 5  # candles ahead used to score forecasts
    flat_threshold_pct: float = 0.1  # |move| below this counts as "flat"


class AgentConfig(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = "agent"
    model: ModelConfig = Field(default_factory=ModelConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    run: RunConfig = Field(default_factory=RunConfig)


# --------------------------------------------------------------------------- model io


class Answer(BaseModel):
    choice: str
    probabilities: Optional[dict[str, float]] = None
    confidence: Optional[float] = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelResult(BaseModel):
    answers: dict[str, Answer]
    reasoning: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    request: Any = None  # exact payload sent (secrets stripped)
    response: Any = None  # exact payload received


# --------------------------------------------------------------------------- engine records


class Fill(BaseModel):
    ts: int
    side: Literal["buy", "sell"]
    qty: float
    price: float
    fee: float
    reason: str
    realized_pnl: float = 0.0


class Position(BaseModel):
    qty: float = 0.0  # >0 long, <0 short
    entry_price: float = 0.0

    @property
    def side(self) -> str:
        return "long" if self.qty > 0 else "short" if self.qty < 0 else "flat"


class StepRecord(BaseModel):
    agent_id: str
    run_id: str
    step: int  # sequential decision number
    idx: int  # candle index the decision was made on (after its close)
    ts: int
    price: float
    action: str
    size_pct: Optional[float] = None
    forecast: Optional[str] = None
    forecast_probs: Optional[dict[str, float]] = None
    answers: dict[str, Answer] = Field(default_factory=dict)
    reasoning: Optional[str] = None
    fills: list[Fill] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)
    cash: float = 0.0
    equity: float = 0.0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    error: Optional[str] = None
    request: Any = None
    response: Any = None


AgentStatus = Literal["idle", "loading", "running", "paused", "finished", "error"]
