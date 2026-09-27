"""The questions an agent is asked each step, and how answers become orders.

Jev/Laya are classifiers: they pick among fixed options. So every model -- Jev,
Laya, GLiNER and LLMs alike -- gets the same typed questions and must return one
option per question. That keeps the comparison fair and the answers machine-usable.

Roles:
  action   - options must be a subset of ACTIONS; drives the broker
  size     - option keys are % of equity ("25", "50", "100")
  forecast - option keys are up / down / flat; scored against what really happened
Questions without a role are asked and logged but not acted on (for experiments).
"""
from __future__ import annotations

from typing import Optional

from .schemas import AgentConfig, ModelResult, QuestionSpec

ACTIONS = {
    "open_long": "Buy / go long now. If currently short, the short is closed and reversed to long.",
    "open_short": "Sell short now. If currently long, the long is closed and reversed to short.",
    "close": "Close the current position and stay out of the market.",
    "hold": "Do nothing: keep the current position (or stay flat if there is none).",
}
FORECASTS = ("up", "down", "flat")

SCHEMAS = ["default", "custom"]


def build_questions(cfg: AgentConfig) -> list[QuestionSpec]:
    d = cfg.decision
    if d.schema_name == "custom":
        qs = list(d.questions)
        _validate(qs)
        return qs
    if d.schema_name != "default":
        raise ValueError(f"unknown decision schema {d.schema_name!r}; known: {SCHEMAS}")

    actions = dict(ACTIONS)
    if not cfg.broker.allow_short:
        del actions["open_short"]
    qs = [QuestionSpec(
        id="action", role="action", options=actions,
        instructions=("You are trading this asset. Based on the market state and your current position, "
                      "what should be done now? Orders fill at the next candle's open."),
    )]
    if d.sizing:
        qs.append(QuestionSpec(
            id="size", role="size",
            instructions="If a new position is opened now, what fraction of account equity should it use?",
            options={"25": "25% of equity: low conviction", "50": "50% of equity: medium conviction",
                     "100": "100% of equity: high conviction"},
        ))
    if d.forecast:
        h, th = cfg.run.forecast_horizon, cfg.run.flat_threshold_pct
        qs.append(QuestionSpec(
            id="forecast", role="forecast",
            instructions=f"Where will the close price be {h} candles from now, relative to the latest close?",
            options={"up": f"higher by more than {th}%", "down": f"lower by more than {th}%",
                     "flat": f"within +/-{th}% of the latest close"},
        ))
    return qs


def _validate(qs: list[QuestionSpec]) -> None:
    ids = [q.id for q in qs]
    if len(set(ids)) != len(ids):
        raise ValueError("question ids must be unique")
    roles = [q.role for q in qs if q.role]
    if roles.count("action") != 1:
        raise ValueError("custom schema needs exactly one question with role 'action'")
    for q in qs:
        if len(q.options) < 2:
            raise ValueError(f"question {q.id!r} needs at least 2 options")
        if q.role == "action" and not set(q.options) <= set(ACTIONS):
            raise ValueError(f"action options must be a subset of {sorted(ACTIONS)}")
        if q.role == "forecast" and not set(q.options) <= set(FORECASTS):
            raise ValueError(f"forecast options must be a subset of {list(FORECASTS)}")
        if q.role == "size":
            for k in q.options:
                try:
                    v = float(k)
                except ValueError:
                    raise ValueError("size option keys must be numbers (% of equity)") from None
                if not 0 < v <= 100:
                    raise ValueError("size options must be in (0, 100]")


def resolve(questions: list[QuestionSpec], result: ModelResult,
            default_size_pct: float) -> tuple[str, float, Optional[str]]:
    """Map answers onto (action, size_pct, forecast). Missing/invalid answers -> hold."""
    action, size, forecast = "hold", default_size_pct, None
    for q in questions:
        a = result.answers.get(q.id)
        if a is None or a.choice not in q.options:
            continue
        if q.role == "action":
            action = a.choice
        elif q.role == "size":
            size = float(a.choice)
        elif q.role == "forecast":
            forecast = a.choice
    return action, size, forecast


def forecast_probs(questions: list[QuestionSpec], result: ModelResult) -> Optional[dict[str, float]]:
    """Forecast distribution: the model's probabilities, else its confidence spread
    evenly over the other options (LLMs only report a confidence)."""
    for q in questions:
        if q.role != "forecast":
            continue
        a = result.answers.get(q.id)
        if a is None or a.choice not in q.options:
            return None
        if a.probabilities:
            return {k: float(a.probabilities.get(k, 0.0)) for k in q.options}
        if a.confidence is not None:
            others = [k for k in q.options if k != a.choice]
            rest = (1 - a.confidence) / len(others) if others else 0.0
            return {k: a.confidence if k == a.choice else rest for k in q.options}
    return None
