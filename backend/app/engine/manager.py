"""Owns every agent window: configs, runners, the arena and the live event bus."""
from __future__ import annotations

import asyncio
from typing import Optional

from ..db import Store
from ..schemas import AgentConfig, DataConfig
from .runner import AgentRunner


class Manager:
    def __init__(self, store: Store):
        self.store = store
        self.runners: dict[str, AgentRunner] = {}
        self.subscribers: set[asyncio.Queue] = set()
        for cfg in store.load_agents():
            self.runners[cfg.id] = AgentRunner(cfg, store, self.publish)

    # ------------------------------------------------------------------ events

    def publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow client; it will resync from /state

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # ------------------------------------------------------------------ agents

    def get(self, agent_id: str) -> AgentRunner:
        try:
            return self.runners[agent_id]
        except KeyError:
            raise KeyError(f"agent {agent_id} not found") from None

    def create(self, cfg: AgentConfig) -> AgentRunner:
        if cfg.id in self.runners:
            raise ValueError(f"agent id {cfg.id} already exists")
        self.store.save_agent(cfg)
        r = AgentRunner(cfg, self.store, self.publish)
        self.runners[cfg.id] = r
        self.publish({"type": "agents"})
        return r

    def update(self, agent_id: str, cfg: AgentConfig) -> AgentRunner:
        r = self.get(agent_id)
        if r.active:
            raise RuntimeError("stop the agent before changing its config")
        cfg = cfg.model_copy(update={"id": agent_id})
        self.store.save_agent(cfg)
        r.cfg = cfg
        self.publish({"type": "agents"})
        return r

    async def delete(self, agent_id: str, purge_history: bool = False) -> None:
        r = self.get(agent_id)
        await r.stop()
        self.store.delete_agent(agent_id)
        if purge_history:
            self.store.delete_runs(agent_id)
        del self.runners[agent_id]
        self.publish({"type": "agents"})

    async def shutdown(self) -> None:
        await asyncio.gather(*(r.stop() for r in self.runners.values()), return_exceptions=True)

    # ------------------------------------------------------------------ arena

    async def arena(self, agent_ids: list[str], data: Optional[DataConfig], sync_features: bool = False) -> None:
        """Run several agents on exactly the same candles, started together."""
        runners = [self.get(a) for a in agent_ids]
        if not runners:
            raise ValueError("pick at least one agent")
        data = data or runners[0].cfg.data
        feats = runners[0].cfg.features
        for r in runners:
            await r.stop()
            update: dict = {"data": data}
            if sync_features:
                update["features"] = feats
            r.cfg = r.cfg.model_copy(update=update)
            self.store.save_agent(r.cfg)
        for r in runners:
            r.start()
        self.publish({"type": "agents"})

    def leaderboard(self) -> list[dict]:
        rows = []
        for r in self.runners.values():
            m = r.metrics or {}
            rows.append({
                "agent_id": r.cfg.id, "name": r.cfg.name, "provider": r.cfg.model.provider,
                "model": r.cfg.model.model, "status": r.status, "data_key": r.cfg.data.key(),
                "data": r.cfg.data.model_dump(), "anonymize": r.cfg.features.anonymize,
                "trading": m.get("trading"), "forecast": m.get("forecast"), "cost": m.get("cost"),
                "decisions": m.get("decisions", 0),
            })
        return rows
