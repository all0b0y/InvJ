import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.engine import Manager
from app.schemas import AgentConfig, DataConfig, ModelConfig, RunConfig


def _cfg(**kw) -> AgentConfig:
    base = dict(name="t", model=ModelConfig(provider="baseline", model="sma_cross"),
                data=DataConfig(provider="csv", symbol="SAMPLE-SYNTH", limit=200))
    base.update(kw)
    return AgentConfig(**base)


async def _wait(runner, timeout=10):
    t0 = time.time()
    while runner.active and time.time() - t0 < timeout:
        await asyncio.sleep(0.01)


async def test_full_replay_fills_at_next_open(tmp_path):
    m = Manager(Store(tmp_path / "e.db"))
    r = m.create(_cfg())
    r.start()
    await _wait(r)
    assert r.status == "finished", r.error
    candles = r.candles
    assert r.steps and r.steps[0].idx == r.start_idx
    by_ts = {c.ts: c for c in candles}
    for s in r.steps:
        for f in s.fills:
            assert f.ts == candles[s.idx + 1].ts  # decided at close of idx, filled at next candle
            assert f.price == pytest.approx(by_ts[f.ts].open * (1 + 0.0005 if f.side == "buy" else 1 - 0.0005))
    met = r.metrics
    assert met["decisions"] == len(candles) - 1 - r.start_idx
    assert met["trading"]["trades"] > 0 and met["forecast"]["scored"] > 0
    assert met["cost"]["total_usd"] == 0
    runs = m.store.list_runs(r.cfg.id)
    assert runs[0]["status"] == "finished" and runs[0]["metrics"]["decisions"] == met["decisions"]
    full = m.store.get_step(r.run_id, 0)
    assert full["request"]["strategy"] == "sma_cross"


async def test_step_pause_resume_stop(tmp_path):
    m = Manager(Store(tmp_path / "e.db"))
    r = m.create(_cfg(run=RunConfig(delay_ms=5)))
    r.step()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if r.status == "paused":
            break
    assert r.status == "paused" and len(r.steps) == 1
    r.step()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(r.steps) == 2 and r.status == "paused":
            break
    assert len(r.steps) == 2
    r.resume()
    await asyncio.sleep(0.05)
    assert len(r.steps) > 2
    await r.stop()
    assert r.status == "stopped" and not r.active


async def test_model_errors_become_hold_and_are_logged(tmp_path, monkeypatch):
    from app.models import systemone

    orig = systemone._SystemOneBase.__init__

    def init(self, cfg):
        orig(self, cfg)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom")))

    monkeypatch.setattr(systemone._SystemOneBase, "__init__", init)
    m = Manager(Store(tmp_path / "e.db"))
    r = m.create(_cfg(model=ModelConfig(provider="jev"), run=RunConfig(max_steps=3)))
    r.start()
    await _wait(r)
    assert r.status == "finished"
    assert len(r.steps) == 3 and all(s.error and "500" in s.error and s.action == "hold" for s in r.steps)
    assert r.metrics["cost"]["errors"] == 3


def _patch_transport(monkeypatch, handler):
    from app.models import systemone
    orig = systemone._SystemOneBase.__init__

    def init(self, cfg):
        orig(self, cfg)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(systemone._SystemOneBase, "__init__", init)


async def test_fatal_error_stops_run(tmp_path, monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    m = Manager(Store(tmp_path / "e.db"))
    r = m.create(_cfg(model=ModelConfig(provider="jev")))
    r.start()
    await _wait(r)
    assert r.status == "error" and "Insufficient credits" in r.error
    assert len(r.steps) == 1  # the failed call is still logged with its request/response
    assert m.store.get_step(r.run_id, 0)["request"]["model"] == "~typesafe/jev-latest"


async def test_consecutive_errors_stop_run(tmp_path, monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(503, text="down"))
    m = Manager(Store(tmp_path / "e.db"))
    r = m.create(_cfg(model=ModelConfig(provider="jev"), run=RunConfig(max_consecutive_errors=4)))
    r.start()
    await _wait(r)
    assert r.status == "error" and "4 failed calls in a row" in r.error and len(r.steps) == 4


async def test_arena_shares_data(tmp_path):
    m = Manager(Store(tmp_path / "e.db"))
    a = m.create(_cfg(name="a"))
    b = m.create(_cfg(name="b", model=ModelConfig(provider="baseline", model="buy_and_hold"),
                      data=DataConfig(provider="csv", symbol="SAMPLE-SYNTH", limit=120)))
    await m.arena([a.cfg.id, b.cfg.id], DataConfig(provider="csv", symbol="SAMPLE-SYNTH", limit=150))
    await _wait(a)
    await _wait(b)
    assert a.cfg.data.limit == b.cfg.data.limit == 150
    assert a.candles is b.candles  # fetched once, shared
    rows = m.leaderboard()
    assert len({row["data_key"] for row in rows}) == 1


def test_api_flow():
    from app.main import app

    with TestClient(app) as client:
        meta = client.get("/api/meta").json()
        assert "jev" in meta["model_providers"] and meta["openrouter_key_configured"] is True
        presets = {p["name"] for p in client.get("/api/presets").json()}
        assert {"jev", "laya-local", "gliner", "llm-claude", "demo-offline"} <= presets
        for p in presets:  # every shipped preset must be a valid config
            assert client.get(f"/api/presets/{p}").status_code == 200

        agent = client.post("/api/agents", json={"preset": "demo-offline"}).json()
        aid = agent["config"]["id"]
        cfg = agent["config"]
        cfg["run"]["delay_ms"] = 0
        cfg["data"]["limit"] = 120
        assert client.put(f"/api/agents/{aid}", json=cfg).status_code == 200

        with client.websocket_connect("/ws") as ws:
            assert client.post(f"/api/agents/{aid}/start").status_code == 200
            kinds = set()
            for _ in range(500):
                ev = ws.receive_json()
                kinds.add(ev["type"])
                if ev["type"] == "status" and ev["status"] == "finished":
                    break
            assert {"status", "tick"} <= kinds

        st = client.get(f"/api/agents/{aid}/state").json()
        assert st["status"] == "finished" and st["steps"] and "request" not in st["steps"][0]
        step = client.get(f"/api/runs/{st['run_id']}/steps/0").json()
        assert "request" in step and "response" in step
        assert client.get(f"/api/runs?agent_id={aid}").json()[0]["status"] == "finished"
        assert client.get("/api/leaderboard").json()[0]["trading"]["trades"] >= 0

        y = client.get("/api/export").text
        assert "SAMPLE-SYNTH" in y
        ids = client.post("/api/import", content=y).json()
        assert len(ids) == 1 and ids[0] != aid

        bad = dict(cfg, decision={"schema_name": "custom", "questions": []})
        assert client.put(f"/api/agents/{aid}", json=bad).status_code == 400

        up = client.post("/api/data/csv", json={"symbol": "MYDATA", "content": "timestamp,open,high,low,close\n1,1,1,1,1\n2,2,2,2,2\n"})
        assert up.json() == {"symbol": "MYDATA", "candles": 2}
        assert client.post("/api/data/csv", json={"symbol": "../x", "content": ""}).status_code == 400
        assert client.delete(f"/api/agents/{aid}").status_code == 200
