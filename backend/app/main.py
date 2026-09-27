"""HTTP + WebSocket API. Run: ``uvicorn app.main:app --reload`` from ``backend/``."""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import decision, presets
from .data import get_provider, list_providers
from .data.binance import INTERVALS
from .data.csv_provider import read_csv
from .db import Store
from .engine import Manager
from .features import list_featurizers
from .features.indicators import available as available_indicators
from .models import list_model_providers
from .models.baseline import STRATEGIES
from .schemas import AgentConfig, DataConfig
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.manager = Manager(Store(settings.db_path))
    yield
    await app.state.manager.shutdown()


app = FastAPI(title="InvJ", lifespan=lifespan)


def mgr(request: Request) -> Manager:
    return request.app.state.manager


def _runner(request: Request, agent_id: str):
    try:
        return mgr(request).get(agent_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


# ---------------------------------------------------------------------- meta


@app.get("/api/meta")
async def meta():
    return {
        "data_providers": list_providers(),
        "intervals": INTERVALS,
        "model_providers": list_model_providers(),
        "baseline_strategies": STRATEGIES,
        "featurizers": list_featurizers(),
        "indicators": available_indicators(),
        "decision_schemas": decision.SCHEMAS,
        "actions": decision.ACTIONS,
        "openrouter_key_configured": bool(settings.openrouter_api_key),
        "defaults": AgentConfig(name="new agent").model_dump(),
    }


@app.get("/api/data/{provider}/symbols")
async def symbols(provider: str):
    try:
        return await get_provider(provider).symbols()
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


class CsvUpload(BaseModel):
    symbol: str
    content: str


@app.post("/api/data/csv")
async def upload_csv(body: CsvUpload):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", body.symbol):
        raise HTTPException(400, "symbol: letters, digits, _ . - only")
    settings.csv_dir.mkdir(parents=True, exist_ok=True)
    path = settings.csv_dir / f"{body.symbol}.csv"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body.content)
    try:
        n = len(read_csv(tmp))
    except (ValueError, KeyError) as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"bad CSV: {e}") from None
    tmp.replace(path)
    return {"symbol": body.symbol, "candles": n}


# ---------------------------------------------------------------------- presets / workspace


@app.get("/api/presets")
async def get_presets():
    return presets.list_presets()


@app.get("/api/presets/{name}")
async def get_preset(name: str):
    try:
        return presets.preset_config(name).model_dump()
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


@app.get("/api/export", response_class=PlainTextResponse)
async def export_workspace(request: Request):
    return presets.export_yaml([r.cfg for r in mgr(request).runners.values()])


@app.post("/api/import")
async def import_workspace(request: Request):
    text = (await request.body()).decode("utf-8")
    try:
        cfgs = presets.import_yaml(text)
    except Exception as e:  # noqa: BLE001 - yaml/pydantic errors -> 400
        raise HTTPException(400, f"invalid workspace YAML: {e}") from None
    return [mgr(request).create(c).cfg.id for c in cfgs]


# ---------------------------------------------------------------------- agents


class CreateAgent(BaseModel):
    preset: Optional[str] = None
    config: Optional[AgentConfig] = None


@app.get("/api/agents")
async def list_agents(request: Request):
    return [r.summary() for r in mgr(request).runners.values()]


@app.post("/api/agents")
async def create_agent(request: Request, body: CreateAgent):
    if body.config is not None:
        cfg = body.config.model_copy(update={"id": AgentConfig().id})
    elif body.preset:
        try:
            cfg = presets.preset_config(body.preset)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
    else:
        cfg = AgentConfig(name="new agent")
    try:
        decision.build_questions(cfg)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return mgr(request).create(cfg).summary()


@app.put("/api/agents/{agent_id}")
async def update_agent(request: Request, agent_id: str, cfg: AgentConfig):
    _runner(request, agent_id)
    try:
        decision.build_questions(cfg)
        return mgr(request).update(agent_id, cfg).summary()
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None


@app.delete("/api/agents/{agent_id}")
async def delete_agent(request: Request, agent_id: str, purge: bool = False):
    _runner(request, agent_id)
    await mgr(request).delete(agent_id, purge_history=purge)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/{action}")
async def control(request: Request, agent_id: str, action: str):
    r = _runner(request, agent_id)
    try:
        if action == "start":
            r.start()
        elif action == "pause":
            r.pause()
        elif action == "resume":
            r.resume()
        elif action == "step":
            r.step()
        elif action == "stop":
            await r.stop()
        else:
            raise HTTPException(404, f"unknown action {action}")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return r.summary()


@app.get("/api/agents/{agent_id}/state")
async def agent_state(request: Request, agent_id: str):
    return _runner(request, agent_id).snapshot()


@app.get("/api/runs")
async def runs(request: Request, agent_id: Optional[str] = None, limit: int = 100):
    return mgr(request).store.list_runs(agent_id, limit)


@app.get("/api/runs/{run_id}")
async def run_detail(request: Request, run_id: str):
    run = mgr(request).store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/runs/{run_id}/steps")
async def run_steps(request: Request, run_id: str):
    return [s.model_dump(exclude={"request", "response"}) for s in mgr(request).store.list_steps(run_id)]


@app.get("/api/runs/{run_id}/steps/{step}")
async def run_step(request: Request, run_id: str, step: int):
    s = mgr(request).store.get_step(run_id, step)
    if s is None:
        raise HTTPException(404, "step not found")
    return s


# ---------------------------------------------------------------------- arena


class ArenaRequest(BaseModel):
    agent_ids: list[str]
    data: Optional[DataConfig] = None
    sync_features: bool = False


@app.post("/api/arena")
async def arena(request: Request, body: ArenaRequest):
    try:
        await mgr(request).arena(body.agent_ids, body.data, body.sync_features)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True}


@app.get("/api/leaderboard")
async def leaderboard(request: Request):
    return mgr(request).leaderboard()


# ---------------------------------------------------------------------- live events


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    m: Manager = websocket.app.state.manager
    q = m.subscribe()
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        m.unsubscribe(q)


# ---------------------------------------------------------------------- frontend (production build)

if settings.frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=settings.frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        return FileResponse(settings.frontend_dist / "index.html")
