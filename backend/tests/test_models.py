import json
import sys
import types

import httpx
import pytest

from app import decision
from app.features import FeatureState
from app.models import DecideContext, ModelError, create_model
from app.models.local import verbalize
from app.schemas import AgentConfig, ModelConfig, Position

from .conftest import make_candles

QS = decision.build_questions(AgentConfig())
STATE = FeatureState(data={"asset": "X", "candles": [["t-1", 1, 1, 1, 100, 1], ["t-0", 1, 1, 1, 102, 1]],
                           "indicators": {"sma20": 101.0, "rsi14": 75.0}, "position": {"side": "flat"}},
                     text="Asset: X\n...")
CTX = DecideContext(history=make_candles(40), position=Position())


def _mock(model, handler):
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_jev_wire_format_and_cost():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "model": "jev-1.13",
            "answers": {
                "action": {"type": "choice", "choice": "open_long", "probabilities": {"open_long": 0.6, "open_short": 0.1, "close": 0.1, "hold": 0.2}, "confidence": 0.55},
                "size": {"type": "choice", "choice": "50", "probabilities": {"25": 0.2, "50": 0.5, "100": 0.3}},
                "forecast": {"type": "choice", "choice": "up", "probabilities": {"up": 0.5, "down": 0.2, "flat": 0.3}},
            },
            "usage": {"input_tokens": 900, "output_tokens": 0, "cost": 0.00042},
        })

    m = create_model(ModelConfig(provider="jev", model=""))
    _mock(m, handler)
    r = await m.decide(STATE, QS, CTX)
    assert seen["url"] == "https://openrouter.ai/api/v1/systemone"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "~typesafe/jev-latest" and body["state"] == STATE.text
    assert body["questions"]["action"]["type"] == "choice"
    assert set(body["questions"]["action"]["criteria"]) == {"open_long", "open_short", "close", "hold"}
    assert r.answers["action"].choice == "open_long" and r.answers["forecast"].probabilities["up"] == 0.5
    assert r.cost_usd == pytest.approx(0.00042) and r.usage.input_tokens == 900
    assert decision.resolve(QS, r, 100) == ("open_long", 50.0, "up")


async def test_systemone_local_uses_latency_cost_and_no_openrouter_key():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"answers": {"action": {"choice": "hold"}}, "usage": {"input_tokens": 5}})

    m = create_model(ModelConfig(provider="systemone", base_url="http://localhost:8001", model="english",
                                 cost_per_hour_usd=3600.0, state_format="json"))
    _mock(m, handler)
    r = await m.decide(STATE, QS, CTX)
    assert seen["url"] == "http://localhost:8001/v1/systemone" and seen["auth"] is None
    assert r.cost_usd == pytest.approx(r.latency_ms / 1000)  # $3600/h == $1/s
    assert r.request["state"] == STATE.data


async def test_http_error_raises_model_error():
    m = create_model(ModelConfig(provider="jev"))
    _mock(m, lambda req: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    with pytest.raises(ModelError, match="402: Insufficient credits"):
        await m.decide(STATE, QS, CTX)


async def test_missing_key(monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    m = create_model(ModelConfig(provider="jev"))
    with pytest.raises(ModelError, match="no API key"):
        await m.decide(STATE, QS, CTX)


async def test_llm_json_schema_and_parsing():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        content = "```json\n" + json.dumps({"reasoning": "RSI hot", "answers": {
            "action": {"choice": "open_short", "confidence": 0.8},
            "size": {"choice": "25", "confidence": 2},
            "forecast": {"choice": "sideways", "confidence": 0.4}}}) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 1200, "completion_tokens": 60, "cost": 0.0021}})

    m = create_model(ModelConfig(provider="llm", model="anthropic/claude-haiku-4.5"))
    _mock(m, handler)
    r = await m.decide(STATE, QS, CTX)
    b = seen["body"]
    assert b["model"] == "anthropic/claude-haiku-4.5" and b["usage"] == {"include": True}
    schema = b["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["answers"]["properties"]["action"]["properties"]["choice"]["enum"] == list(QS[0].options)
    assert r.answers["action"].choice == "open_short" and r.answers["size"].confidence == 1.0
    assert "forecast" not in r.answers  # invalid option dropped
    assert r.reasoning == "RSI hot" and r.cost_usd == pytest.approx(0.0021) and r.usage.output_tokens == 60


async def test_llm_garbage_output():
    m = create_model(ModelConfig(provider="llm", model="x"))
    _mock(m, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "I think buy"}}]}))
    with pytest.raises(ModelError, match="parse"):
        await m.decide(STATE, QS, CTX)


async def test_laya_in_process(monkeypatch):
    calls = {}

    class FakeRouter:
        def __init__(self, device=None):
            calls["device"] = device

        def predict(self, state, questions, **kw):
            calls["kw"] = kw
            return {"answers": {q: {"choice": list(spec["criteria"])[-1], "confidence": 0.9} for q, spec in questions.items()},
                    "usage": {"input_tokens": 321, "output_tokens": 0}, "routing": {"model": "english"}}

    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=FakeRouter))
    from app.models import local
    local._LOADED.clear()
    m = create_model(ModelConfig(provider="laya", model="typed-decisions", cost_per_hour_usd=1.0, params={"max_len": 2048}))
    r = await m.decide(STATE, QS, CTX)
    assert calls["kw"] == {"model": "typed-decisions", "max_len": 2048}
    assert r.answers["action"].choice == "hold" and r.usage.input_tokens == 321
    assert r.cost_usd >= 0


async def test_gliner_as_classifier(monkeypatch):
    class FakeGliner:
        @classmethod
        def from_pretrained(cls, name):
            return cls()

        def predict_entities(self, text, labels, threshold=0.5):
            assert "RSI is 75, overbought" in text
            return [{"text": "overbought", "label": "bearish sell signal", "score": 0.7},
                    {"text": "above", "label": "bullish buy signal", "score": 0.2},
                    {"text": "overbought", "label": "bearish signal", "score": 0.6}]

    monkeypatch.setitem(sys.modules, "gliner", types.SimpleNamespace(GLiNER=FakeGliner))
    from app.models import local
    local._LOADED.clear()
    m = create_model(ModelConfig(provider="gliner"))
    r = await m.decide(STATE, QS, CTX)
    assert r.answers["action"].choice == "open_short"
    assert r.answers["action"].probabilities["open_short"] == pytest.approx(0.7 / 0.9)
    assert r.answers["forecast"].choice == "down"
    assert r.answers["size"].choice == "100"  # confidence 0.78 -> largest size


async def test_local_model_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "laya", None)
    from app.models import local
    local._LOADED.clear()
    m = create_model(ModelConfig(provider="laya"))
    with pytest.raises(ModelError, match="not installed"):
        await m.decide(STATE, QS, CTX)


def test_verbalize():
    t = verbalize(STATE.data)
    assert "rose 2.0%" in t and "above the SMA20" in t and "no open position" in t


async def test_baselines():
    for name in ["buy_and_hold", "sma_cross", "random", "always_hold"]:
        m = create_model(ModelConfig(provider="baseline", model=name))
        r = await m.decide(STATE, QS, CTX)
        assert r.answers["action"].choice in QS[0].options
    with pytest.raises(ValueError):
        create_model(ModelConfig(provider="baseline", model="nope"))
