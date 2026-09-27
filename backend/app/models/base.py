"""Decision-model adapters.

An adapter gets the featurized state plus typed questions and returns one
``Answer`` per question. To add a model family, subclass ``DecisionModel``,
decorate with ``@register_model("provider")`` and import it in
``app/models/__init__.py``.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from ..features import FeatureState
from ..schemas import Answer, Candle, ModelConfig, ModelResult, Position, QuestionSpec
from ..settings import settings

_MODELS: dict[str, type["DecisionModel"]] = {}


@dataclass
class DecideContext:
    history: list[Candle]  # candles[0..idx], never the future
    position: Position


class ModelError(RuntimeError):
    """A model call failed; carries whatever was sent/received for the log."""

    def __init__(self, msg: str, request: Any = None, response: Any = None, latency_ms: float = 0.0):
        super().__init__(msg)
        self.request = request
        self.response = response
        self.latency_ms = latency_ms


class DecisionModel(ABC):
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg

    @abstractmethod
    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        ...

    async def aclose(self) -> None:
        pass

    # -- helpers shared by adapters -------------------------------------------------

    def state_payload(self, state: FeatureState) -> Any:
        return state.data if self.cfg.state_format == "json" else state.text

    def compute_cost(self, latency_ms: float) -> float:
        """$ equivalent for local compute: wall time x configured hourly rate."""
        return latency_ms / 3_600_000 * self.cfg.cost_per_hour_usd

    def api_key(self, required: bool = True) -> Optional[str]:
        key = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        key = key or settings.openrouter_api_key
        if required and not key:
            raise ModelError("no API key: set OPENROUTER_API_KEY in .env (or the agent's api_key_env)")
        return key or None


def register_model(provider: str) -> Callable[[type[DecisionModel]], type[DecisionModel]]:
    def deco(cls: type[DecisionModel]) -> type[DecisionModel]:
        _MODELS[provider] = cls
        return cls
    return deco


def create_model(cfg: ModelConfig) -> DecisionModel:
    try:
        return _MODELS[cfg.provider](cfg)
    except KeyError:
        raise ValueError(f"unknown model provider {cfg.provider!r}; known: {sorted(_MODELS)}") from None


def list_model_providers() -> list[str]:
    return sorted(_MODELS)


# ---------------------------------------------------------------------- Jev wire format


def to_jev_questions(questions: list[QuestionSpec]) -> dict[str, dict]:
    """Our QuestionSpec list -> Jev/Laya ``questions`` object."""
    return {q.id: {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)} for q in questions}


def from_jev_answers(raw: dict[str, Any]) -> dict[str, Answer]:
    out: dict[str, Answer] = {}
    for qid, a in (raw or {}).items():
        if not isinstance(a, dict) or a.get("choice") is None:
            continue
        probs = a.get("probabilities")
        out[qid] = Answer(
            choice=str(a["choice"]),
            probabilities={str(k): float(v) for k, v in probs.items()} if isinstance(probs, dict) else None,
            confidence=float(a["confidence"]) if a.get("confidence") is not None else None,
        )
    return out


# ---------------------------------------------------------------------- HTTP with retries

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


async def post_json(client: httpx.AsyncClient, url: str, payload: dict, headers: dict) -> tuple[dict, float]:
    """POST JSON, retrying transient failures with jittered backoff. Returns (json, latency_ms)."""
    last_err: Optional[str] = None
    body: Any = None
    total_ms = 0.0
    for attempt in range(settings.http_max_retries + 1):
        t0 = time.perf_counter()
        try:
            r = await client.post(url, json=payload, headers=headers)
            total_ms = (time.perf_counter() - t0) * 1000
            try:
                body = r.json()
            except ValueError:
                body = r.text
            if r.status_code < 400:
                if not isinstance(body, dict):
                    raise ModelError("response is not a JSON object", payload, body, total_ms)
                return body, total_ms
            last_err = f"HTTP {r.status_code}: {_err_text(body)}"
            if r.status_code not in _RETRY_STATUS:
                break
        except httpx.TransportError as e:
            total_ms = (time.perf_counter() - t0) * 1000
            last_err = f"{type(e).__name__}: {e}"
        if attempt < settings.http_max_retries:
            await asyncio.sleep(min(30.0, 2 ** attempt) * (0.5 + random.random()))
    raise ModelError(last_err or "request failed", payload, body, total_ms)


def _err_text(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error") or body.get("detail") or body
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    return str(body)[:500]


def openrouter_headers(key: Optional[str]) -> dict:
    h = {"HTTP-Referer": settings.openrouter_app_url, "X-Title": settings.openrouter_app_name}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h
