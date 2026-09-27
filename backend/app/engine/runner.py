"""One agent replaying history: decide on candle i's close, fill at candle i+1's open."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Optional

from .. import decision
from ..data import load_candles
from ..db import Store
from ..features import PortfolioView, get_featurizer
from ..models import DecideContext, ModelError, create_model
from ..schemas import AgentConfig, Candle, ModelResult, StepRecord
from . import metrics as metrics_mod
from .broker import Broker

log = logging.getLogger(__name__)

Publish = Callable[[dict], None]
LIGHT_EXCLUDE = {"request", "response"}


class AgentRunner:
    def __init__(self, cfg: AgentConfig, store: Store, publish: Publish):
        self.cfg = cfg
        self.store = store
        self.publish = publish
        self.status = "idle"
        self.error: Optional[str] = None
        self.run_id: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self._resume = asyncio.Event()
        self._step_budget: Optional[int] = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.candles: list[Candle] = []
        self.start_idx = 0
        self.progress_idx = 0
        self.steps: list[StepRecord] = []  # light copies (no raw request/response)
        self.equity_curve: list[tuple[int, float]] = []
        self.broker: Optional[Broker] = None
        self.metrics: Optional[dict] = None
        self.started_at: Optional[float] = None

    # ------------------------------------------------------------------ control

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, step_once: bool = False) -> None:
        if self.active:
            raise RuntimeError("already running")
        self._reset_state()
        self.error = None
        self._step_budget = 1 if step_once else None
        self._resume.set()
        self.task = asyncio.create_task(self._loop(), name=f"agent-{self.cfg.id}")

    def pause(self) -> None:
        if self.active:
            self._step_budget = None
            self._resume.clear()
            self._set_status("paused")

    def resume(self) -> None:
        if self.active:
            self._step_budget = None
            self._resume.set()

    def step(self) -> None:
        """Advance to the next decision, then pause."""
        if not self.active:
            self.start(step_once=True)
            return
        self._step_budget = 1
        self._resume.set()

    async def stop(self) -> None:
        if self.active:
            assert self.task is not None
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ views

    def summary(self) -> dict:
        return {
            "config": self.cfg.model_dump(),
            "status": self.status,
            "error": self.error,
            "run_id": self.run_id,
            "metrics": self.metrics,
            "last_step": self._light(self.steps[-1]) if self.steps else None,
        }

    def snapshot(self) -> dict:
        last = self.progress_idx if self.candles else -1
        return {
            **self.summary(),
            "candles": [c.model_dump() for c in self.candles[: last + 1]],
            "start_idx": self.start_idx,
            "equity": [{"ts": t, "equity": round(e, 4)} for t, e in self.equity_curve],
            "fills": [f.model_dump() for f in (self.broker.fills if self.broker else [])],
            "trades": [t.__dict__ for t in (self.broker.trades if self.broker else [])],
            "steps": [self._light(s) for s in self.steps],
        }

    @staticmethod
    def _light(s: StepRecord) -> dict:
        return s.model_dump(exclude=LIGHT_EXCLUDE)

    # ------------------------------------------------------------------ loop

    def _set_status(self, status: str) -> None:
        self.status = status
        self.publish({"type": "status", "agent_id": self.cfg.id, "status": status,
                      "run_id": self.run_id, "error": self.error})

    async def _gate(self) -> None:
        if self._step_budget == 0:
            self._step_budget = None
            self._resume.clear()
            self._set_status("paused")
        if not self._resume.is_set():
            await self._resume.wait()
            self._set_status("running")

    async def _loop(self) -> None:
        cfg = self.cfg
        model = None
        try:
            self._set_status("loading")
            self.candles = await load_candles(cfg.data)
            featurizer = get_featurizer(cfg.features, self.candles, cfg.data.symbol, cfg.data.interval)
            questions = decision.build_questions(cfg)
            model = create_model(cfg.model)
            start = featurizer.warmup
            if start >= len(self.candles) - 1:
                raise ValueError(f"not enough candles: have {len(self.candles)}, warm-up needs {start + 2}")

            self.run_id = uuid.uuid4().hex[:12]
            self.started_at = time.time()
            self.store.create_run(self.run_id, cfg)
            self.broker = Broker(cfg.broker)
            self.start_idx = self.progress_idx = start
            self.equity_curve = [(self.candles[start].ts, cfg.broker.initial_cash)]
            self._set_status("running")

            decisions = 0
            failed_in_row = 0
            for idx in range(start, len(self.candles) - 1):
                if cfg.run.max_steps is not None and decisions >= cfg.run.max_steps:
                    break
                await self._gate()
                rec = None
                fatal = False
                if (idx - start) % max(1, cfg.run.decide_every) == 0:
                    rec, fatal, call_failed = await self._decide(idx, decisions, featurizer, questions, model)
                    decisions += 1
                    failed_in_row = failed_in_row + 1 if call_failed else 0
                    if self._step_budget is not None:
                        self._step_budget -= 1
                nxt = self.candles[idx + 1]
                if rec is not None:
                    rec.fills = self.broker.execute(rec.action, rec.size_pct or 0, nxt.open, nxt.ts)
                equity = self.broker.equity(nxt.close)
                self.equity_curve.append((nxt.ts, equity))
                self.progress_idx = idx + 1
                if rec is not None:
                    rec.position = self.broker.position.model_copy()
                    rec.cash = self.broker.cash
                    rec.equity = equity
                    self.store.add_step(rec)
                    self.steps.append(rec.model_copy(update={"request": None, "response": None}))
                self._update_metrics()
                self.publish({
                    "type": "tick", "agent_id": cfg.id, "run_id": self.run_id,
                    "candle": nxt.model_dump(), "equity": {"ts": nxt.ts, "equity": round(equity, 4)},
                    "step": self._light(rec) if rec is not None else None,
                    "metrics": self.metrics,
                })
                if rec is not None and fatal:
                    raise RuntimeError(f"stopped on step {rec.step}: {rec.error}")
                if failed_in_row >= cfg.run.max_consecutive_errors > 0:
                    raise RuntimeError(f"stopped after {failed_in_row} failed calls in a row; last: {rec.error if rec else ''}")
                await asyncio.sleep(cfg.run.delay_ms / 1000 if cfg.run.delay_ms else 0)

            self._update_metrics()
            self.store.update_run(self.run_id, "finished", self.metrics, finished=True)
            self._set_status("finished")
        except asyncio.CancelledError:
            if self.run_id:
                self._update_metrics()
                self.store.update_run(self.run_id, "stopped", self.metrics, finished=True)
            self._set_status("stopped")
            raise
        except Exception as e:  # noqa: BLE001 - any failure ends this agent, not the server
            log.exception("agent %s failed", cfg.id)
            self.error = f"{type(e).__name__}: {e}"
            if self.run_id:
                self.store.update_run(self.run_id, "error", self.metrics, error=self.error, finished=True)
            self._set_status("error")
        finally:
            if model is not None:
                await model.aclose()

    async def _decide(self, idx, step_no, featurizer, questions, model) -> tuple[StepRecord, bool, bool]:
        """-> (record, fatal, call_failed). Model failures become a logged "hold" step."""
        assert self.broker is not None
        c = self.candles[idx]
        view = PortfolioView(position=self.broker.position, cash=self.broker.cash,
                             equity=self.broker.equity(c.close), initial_cash=self.cfg.broker.initial_cash)
        state = featurizer.build(idx, view)
        ctx = DecideContext(history=self.candles[: idx + 1], position=self.broker.position)
        error: Optional[str] = None
        req: Any = None
        resp: Any = None
        fatal = call_failed = False
        try:
            result = await model.decide(state, questions, ctx)
        except ModelError as e:
            error, req, resp, fatal, call_failed = str(e), e.request, e.response, e.fatal, True
            result = ModelResult(answers={}, latency_ms=e.latency_ms, request=req, response=resp,
                                 cost_usd=model.compute_cost(e.latency_ms))
        action, size, forecast = decision.resolve(questions, result, self.cfg.broker.default_size_pct)
        if error is None:
            missing = [q.id for q in questions if q.role and q.id not in result.answers]
            if missing:
                error = f"no valid answer for: {', '.join(missing)} (treated as hold/default)"
        rec = StepRecord(
            agent_id=self.cfg.id, run_id=self.run_id or "", step=step_no, idx=idx, ts=c.ts, price=c.close,
            action=action, size_pct=size, forecast=forecast,
            forecast_probs=decision.forecast_probs(questions, result),
            answers=result.answers, reasoning=result.reasoning,
            cost_usd=result.cost_usd, latency_ms=result.latency_ms, usage=result.usage, error=error,
            request=result.request, response=result.response,
        )
        return rec, fatal, call_failed

    def _update_metrics(self) -> None:
        if not self.broker:
            return
        self.metrics = metrics_mod.compute(
            steps=self.steps, equity_curve=self.equity_curve, trades=self.broker.trades, candles=self.candles,
            start_idx=self.start_idx, progress_idx=self.progress_idx, initial_cash=self.cfg.broker.initial_cash,
            horizon=self.cfg.run.forecast_horizon, flat_pct=self.cfg.run.flat_threshold_pct,
        )
