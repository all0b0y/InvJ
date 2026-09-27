"""Free, deterministic reference strategies. A model that cannot beat these is not adding value.

``model``: buy_and_hold | sma_cross | random | always_hold
``params``: fast (10), slow (30) for sma_cross; seed (0) for random.
"""
from __future__ import annotations

import random

from ..features import FeatureState
from ..schemas import Answer, ModelResult, QuestionSpec
from .base import DecideContext, DecisionModel, register_model

STRATEGIES = ["buy_and_hold", "sma_cross", "random", "always_hold"]


def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


@register_model("baseline")
class BaselineModel(DecisionModel):
    def __init__(self, cfg):
        super().__init__(cfg)
        if cfg.model not in STRATEGIES:
            raise ValueError(f"baseline model must be one of {STRATEGIES}")
        self.rng = random.Random(cfg.params.get("seed", 0))

    def _pick(self, ctx: DecideContext, options: set[str]) -> tuple[str, str]:
        """-> (action, forecast)"""
        side = ctx.position.side
        m = self.cfg.model
        if m == "always_hold":
            return "hold", "flat"
        if m == "buy_and_hold":
            return ("open_long" if side != "long" else "hold"), "up"
        if m == "random":
            return self.rng.choice(sorted(options)), self.rng.choice(["up", "down", "flat"])
        closes = [c.close for c in ctx.history]
        fast = _sma(closes, int(self.cfg.params.get("fast", 10)))
        slow = _sma(closes, int(self.cfg.params.get("slow", 30)))
        if fast is None or slow is None:
            return "hold", "flat"
        if fast > slow:
            return ("open_long" if side != "long" else "hold"), "up"
        want = "open_short" if "open_short" in options else "close"
        return (want if side != ("short" if want == "open_short" else "flat") else "hold"), "down"

    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        action_q = next(q for q in questions if q.role == "action")
        action, forecast = self._pick(ctx, set(action_q.options))
        if action not in action_q.options:
            action = "hold" if "hold" in action_q.options else next(iter(action_q.options))
        answers = {}
        for q in questions:
            if q.role == "action":
                answers[q.id] = Answer(choice=action, confidence=1.0)
            elif q.role == "size":
                answers[q.id] = Answer(choice=max(q.options, key=float), confidence=1.0)
            elif q.role == "forecast" and forecast in q.options:
                answers[q.id] = Answer(choice=forecast, confidence=1.0)
        return ModelResult(answers=answers, request={"strategy": self.cfg.model, "params": self.cfg.params},
                           response={k: a.choice for k, a in answers.items()})
