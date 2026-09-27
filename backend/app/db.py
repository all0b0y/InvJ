"""SQLite persistence: agent configs, runs and every step (with raw model I/O)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .schemas import AgentConfig, StepRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    metrics TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS runs_agent ON runs(agent_id, started_at);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, step)
);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.lock = threading.Lock()

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            self.conn.commit()
            return rows

    # -- agents
    def save_agent(self, cfg: AgentConfig) -> None:
        with self.lock:
            row = self.conn.execute("SELECT sort FROM agents WHERE id=?", (cfg.id,)).fetchone()
            sort = row["sort"] if row else (self.conn.execute("SELECT COALESCE(MAX(sort),0)+1 FROM agents").fetchone()[0])
            self.conn.execute(
                "INSERT INTO agents(id, config, sort, created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (cfg.id, cfg.model_dump_json(), sort, time.time()))
            self.conn.commit()

    def load_agents(self) -> list[AgentConfig]:
        return [AgentConfig.model_validate_json(r["config"]) for r in self._exec("SELECT config FROM agents ORDER BY sort")]

    def delete_agent(self, agent_id: str) -> None:
        self._exec("DELETE FROM agents WHERE id=?", (agent_id,))

    # -- runs
    def create_run(self, run_id: str, cfg: AgentConfig) -> None:
        self._exec("INSERT INTO runs(id, agent_id, config, status, started_at) VALUES(?,?,?,?,?)",
                   (run_id, cfg.id, cfg.model_dump_json(), "running", time.time()))

    def update_run(self, run_id: str, status: str, metrics: Optional[dict] = None, error: Optional[str] = None,
                   finished: bool = False) -> None:
        self._exec("UPDATE runs SET status=?, metrics=COALESCE(?, metrics), error=?, "
                   "finished_at=CASE WHEN ? THEN ? ELSE finished_at END WHERE id=?",
                   (status, json.dumps(metrics) if metrics is not None else None, error, finished, time.time(), run_id))

    def list_runs(self, agent_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT id, agent_id, status, started_at, finished_at, metrics, error, config FROM runs"
        args: tuple = ()
        if agent_id:
            sql += " WHERE agent_id=?"
            args = (agent_id,)
        rows = self._exec(sql + " ORDER BY started_at DESC LIMIT ?", args + (limit,))
        out = []
        for r in rows:
            cfg = json.loads(r["config"])
            out.append({"id": r["id"], "agent_id": r["agent_id"], "status": r["status"],
                        "started_at": r["started_at"], "finished_at": r["finished_at"], "error": r["error"],
                        "name": cfg.get("name"), "model": cfg.get("model"), "data": cfg.get("data"),
                        "metrics": json.loads(r["metrics"]) if r["metrics"] else None})
        return out

    def get_run(self, run_id: str) -> Optional[dict]:
        rows = self._exec("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "agent_id": r["agent_id"], "status": r["status"], "started_at": r["started_at"],
                "finished_at": r["finished_at"], "error": r["error"], "config": json.loads(r["config"]),
                "metrics": json.loads(r["metrics"]) if r["metrics"] else None}

    def delete_runs(self, agent_id: str) -> None:
        self._exec("DELETE FROM steps WHERE agent_id=?", (agent_id,))
        self._exec("DELETE FROM runs WHERE agent_id=?", (agent_id,))

    # -- steps
    def add_step(self, rec: StepRecord) -> None:
        self._exec("INSERT OR REPLACE INTO steps(run_id, step, agent_id, data) VALUES(?,?,?,?)",
                   (rec.run_id, rec.step, rec.agent_id, rec.model_dump_json()))

    def get_step(self, run_id: str, step: int) -> Optional[dict[str, Any]]:
        rows = self._exec("SELECT data FROM steps WHERE run_id=? AND step=?", (run_id, step))
        return json.loads(rows[0]["data"]) if rows else None

    def list_steps(self, run_id: str) -> list[StepRecord]:
        rows = self._exec("SELECT data FROM steps WHERE run_id=? ORDER BY step", (run_id,))
        return [StepRecord.model_validate_json(r["data"]) for r in rows]
