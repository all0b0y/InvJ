"""Any chat model on OpenRouter, constrained to the same typed questions.

The model must answer every question with one option key plus a confidence in
[0, 1]; free-form reasoning is kept for the log. ``params``:
  temperature (0), max_tokens (800), structured ("json_schema" | "json_object" | "none"),
  system_prompt (override), reasoning (OpenRouter reasoning object, e.g. {"effort": "low"}).
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..features import FeatureState
from ..schemas import Answer, ModelResult, QuestionSpec, Usage
from ..settings import settings
from .base import DecideContext, DecisionModel, ModelError, openrouter_headers, post_json, register_model

SYSTEM_PROMPT = (
    "You are a disciplined trader being evaluated on historical market data. "
    "You only see information up to the latest closed candle. "
    "Answer every question by choosing exactly one of its option keys, and give your confidence "
    "(probability you are right, 0 to 1). Reply with a single JSON object and nothing else."
)


def _schema(questions: list[QuestionSpec]) -> dict:
    answers = {
        q.id: {
            "type": "object",
            "properties": {"choice": {"type": "string", "enum": list(q.options)},
                           "confidence": {"type": "number"}},
            "required": ["choice", "confidence"],
            "additionalProperties": False,
        }
        for q in questions
    }
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "brief rationale, 1-3 sentences"},
            "answers": {"type": "object", "properties": answers, "required": list(answers),
                        "additionalProperties": False},
        },
        "required": ["reasoning", "answers"],
        "additionalProperties": False,
    }


def _user_prompt(state: Any, questions: list[QuestionSpec]) -> str:
    state_txt = state if isinstance(state, str) else json.dumps(state, separators=(",", ":"))
    lines = ["MARKET STATE", state_txt, "", "QUESTIONS"]
    for q in questions:
        lines.append(f"- {q.id}: {q.instructions}")
        lines += [f"    {k}: {v}" for k, v in q.options.items()]
    example = {q.id: {"choice": next(iter(q.options)), "confidence": 0.5} for q in questions}
    lines += ["", "Reply as JSON: " + json.dumps({"reasoning": "...", "answers": example})]
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start: end + 1])
    raise ValueError("no JSON object in model output")


@register_model("llm")
class OpenRouterLLM(DecisionModel):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.client = httpx.AsyncClient(timeout=settings.http_timeout_s)

    def endpoint(self) -> str:
        return (self.cfg.base_url or settings.openrouter_base_url).rstrip("/") + "/v1/chat/completions"

    async def decide(self, state: FeatureState, questions: list[QuestionSpec], ctx: DecideContext) -> ModelResult:
        p = self.cfg.params
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": p.get("system_prompt") or SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(self.state_payload(state), questions)},
            ],
            "temperature": p.get("temperature", 0),
            "max_tokens": p.get("max_tokens", 800),
            "usage": {"include": True},  # OpenRouter usage accounting -> usage.cost
        }
        mode = p.get("structured", "json_schema")
        if mode == "json_schema":
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "decision", "strict": True, "schema": _schema(questions)}}
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if p.get("reasoning"):
            payload["reasoning"] = p["reasoning"]

        body, latency = await post_json(self.client, self.endpoint(), payload, openrouter_headers(self.api_key()))
        try:
            content = body["choices"][0]["message"].get("content") or ""
            parsed = _extract_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ModelError(f"could not parse model output: {e}", payload, body, latency) from None

        answers: dict[str, Answer] = {}
        raw_answers = parsed.get("answers") if isinstance(parsed.get("answers"), dict) else {}
        for q in questions:
            a = raw_answers.get(q.id)
            if isinstance(a, str):
                a = {"choice": a}
            if not isinstance(a, dict) or str(a.get("choice")) not in q.options:
                continue
            conf = a.get("confidence")
            try:
                conf = min(1.0, max(0.0, float(conf))) if conf is not None else None
            except (TypeError, ValueError):
                conf = None
            answers[q.id] = Answer(choice=str(a["choice"]), confidence=conf)
        if not answers:
            raise ModelError("model returned no valid answers", payload, body, latency)

        usage = body.get("usage") or {}
        return ModelResult(
            answers=answers,
            reasoning=str(parsed.get("reasoning") or "") or None,
            usage=Usage(input_tokens=int(usage.get("prompt_tokens") or 0),
                        output_tokens=int(usage.get("completion_tokens") or 0)),
            cost_usd=float(usage.get("cost") or 0.0),
            latency_ms=latency, request=payload, response=body,
        )

    async def aclose(self) -> None:
        await self.client.aclose()
