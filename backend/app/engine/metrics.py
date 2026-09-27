"""Scoring an agent: trading results, forecast skill and what it cost to get there."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from ..schemas import Candle, StepRecord
from .broker import ClosedTrade

_INTERVAL_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000, "M": 2_592_000_000}
_YEAR_MS = 365.25 * 86_400_000


def periods_per_year(candles: list[Candle]) -> float:
    if len(candles) < 2:
        return 365.0
    gaps = sorted(b.ts - a.ts for a, b in zip(candles, candles[1:]))
    step = gaps[len(gaps) // 2] or _INTERVAL_MS["d"]
    return _YEAR_MS / step


def outcome(candles: list[Candle], idx: int, horizon: int, flat_pct: float) -> Optional[str]:
    """What really happened ``horizon`` candles after ``idx``: up / down / flat."""
    j = idx + horizon
    if j >= len(candles):
        return None
    move = (candles[j].close / candles[idx].close - 1) * 100
    return "up" if move > flat_pct else "down" if move < -flat_pct else "flat"


def compute(
    *,
    steps: list[StepRecord],
    equity_curve: list[tuple[int, float]],
    trades: list[ClosedTrade],
    candles: list[Candle],
    start_idx: int,
    progress_idx: int,  # last candle index the replay has revealed
    initial_cash: float,
    horizon: int,
    flat_pct: float,
) -> dict:
    visible = candles[: progress_idx + 1]
    equities = [e for _, e in equity_curve] or [initial_cash]
    final = equities[-1]

    # --- trading
    peak, mdd = equities[0], 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    rets = [(b / a - 1) for a, b in zip(equities, equities[1:]) if a > 0]
    sharpe = None
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        if sd > 0:
            sharpe = mean / sd * math.sqrt(periods_per_year(candles))
    bh = None
    if progress_idx > start_idx:
        bh = (visible[-1].close / candles[start_idx].close - 1) * 100
    wins = [t for t in trades if t.pnl > 0]
    in_market = sum(1 for s in steps if s.position.qty != 0)

    # --- forecasts / directional calls, only where the outcome is already revealed
    fc_total = fc_hit = 0
    brier_sum, brier_n = 0.0, 0
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    outcomes: dict[str, int] = defaultdict(int)
    dir_total = dir_hit = 0
    for s in steps:
        real = outcome(visible, s.idx, horizon, flat_pct)
        if real is None:
            continue
        outcomes[real] += 1
        if s.forecast:
            fc_total += 1
            fc_hit += s.forecast == real
            confusion[s.forecast][real] += 1
            probs = s.forecast_probs
            if probs:
                brier_sum += sum((probs.get(k, 0.0) - (1.0 if k == real else 0.0)) ** 2 for k in ("up", "down", "flat"))
                brier_n += 1
        if s.action in ("open_long", "open_short") and any(f.reason.startswith("open") for f in s.fills):
            dir_total += 1
            dir_hit += (s.action == "open_long" and real == "up") or (s.action == "open_short" and real == "down")

    # --- cost
    total_cost = sum(s.cost_usd for s in steps)
    by_action: dict[str, dict] = defaultdict(lambda: {"count": 0, "cost_usd": 0.0, "latency_ms": 0.0})
    for s in steps:
        a = by_action[s.action]
        a["count"] += 1
        a["cost_usd"] += s.cost_usd
        a["latency_ms"] += s.latency_ms
    n = len(steps)
    pnl = final - initial_cash
    majority = max(outcomes.values()) / sum(outcomes.values()) * 100 if outcomes else None

    return {
        "trading": {
            "equity": round(final, 2),
            "pnl": round(pnl, 2),
            "return_pct": round(pnl / initial_cash * 100, 3),
            "buy_hold_return_pct": None if bh is None else round(bh, 3),
            "max_drawdown_pct": round(mdd * 100, 3),
            "sharpe": None if sharpe is None else round(sharpe, 3),
            "trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "avg_trade_pct": round(sum(t.return_pct for t in trades) / len(trades), 3) if trades else None,
            "exposure_pct": round(in_market / n * 100, 1) if n else 0.0,
        },
        "forecast": {
            "scored": fc_total,
            "accuracy_pct": round(fc_hit / fc_total * 100, 1) if fc_total else None,
            "majority_class_pct": None if majority is None else round(majority, 1),
            "brier": round(brier_sum / brier_n, 4) if brier_n else None,
            "confusion": {k: dict(v) for k, v in confusion.items()},
            "outcomes": dict(outcomes),
            "entry_direction_scored": dir_total,
            "entry_direction_accuracy_pct": round(dir_hit / dir_total * 100, 1) if dir_total else None,
        },
        "cost": {
            "total_usd": round(total_cost, 6),
            "per_decision_usd": round(total_cost / n, 6) if n else 0.0,
            "pnl_per_usd": round(pnl / total_cost, 2) if total_cost > 0 else None,
            "input_tokens": sum(s.usage.input_tokens for s in steps),
            "output_tokens": sum(s.usage.output_tokens for s in steps),
            "avg_latency_ms": round(sum(s.latency_ms for s in steps) / n, 1) if n else 0.0,
            "errors": sum(1 for s in steps if s.error),
            "by_action": {k: {"count": v["count"], "cost_usd": round(v["cost_usd"], 6),
                              "avg_latency_ms": round(v["latency_ms"] / v["count"], 1)}
                          for k, v in by_action.items()},
        },
        "decisions": n,
        "progress": {"idx": progress_idx, "start": start_idx, "total": len(candles) - 1},
    }
