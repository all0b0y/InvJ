"""Local System-1 models running in this process (optional heavy deps).

  laya   - ``pip install laya``   (Apache-2.0 Jev-style decision model, Jev-compatible output)
  gliner - ``pip install gliner`` (zero-shot NER, used here as a zero-shot classifier)

Both are loaded once per process and shared by every agent that uses them; calls
run in a worker thread behind a lock so the event loop stays responsive.
Cost is the configured ``cost_per_hour_usd`` x wall time.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

from ..features import FeatureState
from ..schemas import Answer, ModelResult, QuestionSpec, Usage
from .base import DecideContext, DecisionModel, ModelError, from_jev_answers, register_model, to_jev_questions

_LOADED: dict[tuple, Any] = {}
_LOAD_LOCK = threading.Lock()
_INFER_LOCKS: dict[tuple, threading.Lock] = {}


def _shared(key: tuple, factory: Callable[[], Any]) -> tuple[Any, threading.Lock]:
    with _LOAD_LOCK:
        if key not in _LOADED:
            _LOADED[key] = factory()
            _INFER_LOCKS[key] = threading.Lock()
        return _LOADED[key], _INFER_LOCKS[key]


def _missing(pkg: str) -> ModelError:
    return ModelError(f"{pkg} is not installed. Run: pip install -e 'backend[{pkg}]' (needs torch)", fatal=True)


class _LocalModel(DecisionModel):
    async def _run(self, fn: Callable[[], Any]) -> tuple[Any, float]:
        t0 = time.perf_counter()
        out = await asyncio.to_thread(fn)
        return out, (time.perf_counter() - t0) * 1000


@register_model("laya")
class LayaModel(_LocalModel):
    """``model``: "" (auto-route), "english", "multilingual" or "typed-decisions".
    ``params``: device ("cpu"/"cuda"/"mps"), max_len."""

    def _router(self):
        try:
            from laya import Router  # type: ignore
        except ImportError:
            raise _missing("laya") from None
        device = self.cfg.params.get("device")
        return _shared(("laya", device), lambda: Router(device=device))

    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        router, lock = await asyncio.to_thread(self._router)
        payload = {"state": self.state_payload(state), "questions": to_jev_questions(questions)}
        kwargs: dict = {}
        if self.cfg.model:
            kwargs["model"] = self.cfg.model
        if self.cfg.params.get("max_len"):
            kwargs["max_len"] = int(self.cfg.params["max_len"])

        def call():
            with lock:
                return router.predict(payload["state"], payload["questions"], **kwargs)

        try:
            body, latency = await self._run(call)
        except Exception as e:  # noqa: BLE001 - surface any inference failure in the step log
            raise ModelError(f"laya inference failed: {e}", payload) from None
        usage = body.get("usage") or {}
        return ModelResult(
            answers=from_jev_answers(body.get("answers") or {}),
            usage=Usage(input_tokens=int(usage.get("input_tokens") or 0), output_tokens=0),
            cost_usd=self.compute_cost(latency), latency_ms=latency,
            request={**payload, **kwargs}, response=body,
        )


# Default GLiNER label for each option key. GLiNER scores text spans against
# entity labels, so options are phrased as "signal" entity types.
GLINER_LABELS = {
    "open_long": "bullish buy signal", "open_short": "bearish sell signal",
    "close": "exit signal", "hold": "neutral signal",
    "up": "bullish signal", "down": "bearish signal", "flat": "sideways signal",
}


def verbalize(data: dict) -> str:
    """Short natural-language description of the state; GLiNER reads text, not CSV tables."""
    rows = data.get("candles") or []
    closes = [r[4] for r in rows]
    parts = []
    if len(closes) >= 2:
        chg = (closes[-1] / closes[0] - 1) * 100
        parts.append(f"Over the last {len(closes)} candles price {'rose' if chg > 0 else 'fell'} {abs(chg):.1f}%.")
        pairs = list(zip(closes[:-1], closes[1:]))[-5:]
        recent = ["up" if b > a else "down" if b < a else "unchanged" for a, b in pairs]
        parts.append("Recent candles closed " + ", ".join(recent) + ".")
    close = closes[-1] if closes else None
    for name, v in (data.get("indicators") or {}).items():
        if v is None or close is None:
            continue
        if name.startswith(("sma", "ema")):
            parts.append(f"Price is {'above' if close > v else 'below'} the {name.upper()} moving average.")
        elif name.startswith("rsi"):
            zone = "overbought" if v > 70 else "oversold" if v < 30 else "neutral"
            parts.append(f"RSI is {v:.0f}, {zone}.")
        elif name.startswith("ret"):
            parts.append(f"Momentum over {name[3:]} candles is {'positive' if v > 0 else 'negative'} ({v:+.1f}%).")
        elif name == "vol_ratio":
            parts.append(f"Volume is {'high' if v > 1.5 else 'low' if v < 0.7 else 'normal'}.")
    pos = data.get("position") or {}
    if pos.get("side") and pos["side"] != "flat":
        parts.append(f"We hold a {pos['side']} position with unrealized P&L {pos.get('unrealized_pnl_pct', 0):+.1f}%.")
    else:
        parts.append("We have no open position.")
    return " ".join(parts)


@register_model("gliner")
class GlinerModel(_LocalModel):
    """``model``: HF id (default ``urchade/gliner_medium-v2.1``).
    ``params``: threshold (0.05), device, labels ({option_key: label} overrides).

    Classification hack: the state is verbalized, GLiNER extracts spans for each
    option's label, and an option's score is its best span score. Scores are
    normalised into probabilities; if nothing is found the last option wins
    (hold / flat). "size" questions have no text signal, so size follows the
    action's confidence (<0.4 -> smallest, >=0.6 -> largest).
    """

    def _gliner(self):
        try:
            from gliner import GLiNER  # type: ignore
        except ImportError:
            raise _missing("gliner") from None
        name = self.cfg.model or "urchade/gliner_medium-v2.1"
        device = self.cfg.params.get("device")

        def load():
            m = GLiNER.from_pretrained(name)
            return m.to(device) if device else m

        return _shared(("gliner", name, device), load)

    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        model, lock = await asyncio.to_thread(self._gliner)
        text = verbalize(state.data)
        threshold = float(self.cfg.params.get("threshold", 0.05))
        overrides = self.cfg.params.get("labels") or {}
        label_of = {k: overrides.get(k) or GLINER_LABELS.get(k) or k.replace("_", " ")
                    for q in questions if q.role != "size" for k in q.options}
        labels = sorted(set(label_of.values()))

        def call():
            with lock:
                return model.predict_entities(text, labels, threshold=threshold)

        request = {"text": text, "labels": labels, "threshold": threshold}
        try:
            entities, latency = await self._run(call)
        except Exception as e:  # noqa: BLE001
            raise ModelError(f"gliner inference failed: {e}", request) from None

        best: dict[str, float] = {}
        for e in entities:
            best[e["label"]] = max(best.get(e["label"], 0.0), float(e["score"]))

        answers: dict[str, Answer] = {}
        action_conf = None
        for q in questions:
            if q.role == "size":
                continue
            scores = {k: best.get(label_of[k], 0.0) for k in q.options}
            total = sum(scores.values())
            if total == 0:
                keys = list(q.options)
                answers[q.id] = Answer(choice=keys[-1], probabilities={k: 1 / len(keys) for k in keys},
                                       confidence=1 / len(keys))
                continue
            probs = {k: v / total for k, v in scores.items()}
            choice = max(probs, key=probs.get)
            answers[q.id] = Answer(choice=choice, probabilities=probs, confidence=probs[choice])
            if q.role == "action":
                action_conf = probs[choice]
        for q in questions:
            if q.role == "size":
                keys = sorted(q.options, key=float)
                c = action_conf or 0.0
                pick = keys[0] if c < 0.4 else keys[-1] if c >= 0.6 else keys[len(keys) // 2]
                answers[q.id] = Answer(choice=pick, confidence=c)
        return ModelResult(answers=answers, cost_usd=self.compute_cost(latency), latency_ms=latency,
                           request=request, response={"entities": entities},
                           usage=Usage(input_tokens=len(text.split())))
