"""Jev (via OpenRouter) and any other server speaking the Jev ``/v1/systemone`` protocol.

Wire format (TypeSafe Jev; ``laya-serve`` implements the same):
  request : {"model", "state", "questions": {id: {"type": "choice", "instructions", "criteria": {opt: desc}}}}
  response: {"answers": {id: {"choice", "probabilities", "confidence"}}, "usage": {"input_tokens", "output_tokens", "cost"?}}

provider "jev"       -> {OPENROUTER_BASE_URL}/v1/systemone, model "~typesafe/jev-latest", OpenRouter key
provider "systemone" -> {base_url}/v1/systemone, e.g. a local ``laya-serve`` (key optional)
"""
from __future__ import annotations

import os

import httpx

from ..features import FeatureState
from ..schemas import ModelResult, QuestionSpec, Usage
from ..settings import settings
from .base import (DecideContext, DecisionModel, ModelError, from_jev_answers, openrouter_headers,
                   post_json, register_model, to_jev_questions)

DEFAULT_JEV_MODEL = "~typesafe/jev-latest"


class _SystemOneBase(DecisionModel):
    needs_key = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.client = httpx.AsyncClient(timeout=settings.http_timeout_s)

    def endpoint(self) -> str:
        raise NotImplementedError

    def headers(self) -> dict:
        return openrouter_headers(self.api_key(required=self.needs_key))

    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        payload: dict = {"state": self.state_payload(state), "questions": to_jev_questions(questions)}
        if self.cfg.model:
            payload["model"] = self.cfg.model
        payload.update(self.cfg.params.get("extra_body", {}))
        body, latency = await post_json(self.client, self.endpoint(), payload, self.headers())
        answers = from_jev_answers(body.get("answers") or {})
        if not answers:
            raise ModelError("response has no answers", payload, body, latency)
        usage = body.get("usage") or {}
        cost = usage.get("cost")
        return ModelResult(
            answers=answers,
            usage=Usage(input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0)),
            cost_usd=float(cost) if cost is not None else self.compute_cost(latency),
            latency_ms=latency, request=payload, response=body,
        )

    async def aclose(self) -> None:
        await self.client.aclose()


@register_model("jev")
class JevModel(_SystemOneBase):
    def __init__(self, cfg):
        if not cfg.model:
            cfg = cfg.model_copy(update={"model": DEFAULT_JEV_MODEL})
        super().__init__(cfg)

    def endpoint(self) -> str:
        return (self.cfg.base_url or settings.openrouter_base_url).rstrip("/") + "/v1/systemone"


@register_model("systemone")
class SystemOneModel(_SystemOneBase):
    needs_key = False

    def endpoint(self) -> str:
        if not self.cfg.base_url:
            raise ModelError("systemone provider needs base_url, e.g. http://localhost:8001 (laya-serve)")
        url = self.cfg.base_url.rstrip("/")
        return url if url.endswith("/v1/systemone") else url + "/v1/systemone"

    def headers(self) -> dict:
        # Never forward the OpenRouter key to an arbitrary server; only an explicit per-agent key.
        key = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        return {"Authorization": f"Bearer {key}"} if key else {}
