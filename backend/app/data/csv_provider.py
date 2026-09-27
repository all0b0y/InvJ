"""Candles from CSV files in ``CSV_DIR`` (default ``backend/data/csv``).

``symbol`` is the file name without ``.csv``. Expected header (case-insensitive):
``timestamp,open,high,low,close[,volume]``, where timestamp is epoch seconds/ms or
an ISO datetime. ``interval`` is informational only for CSV data.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..schemas import Candle, DataConfig
from ..settings import settings
from .base import DataProvider, parse_time_ms, register_provider

_TS_KEYS = ("timestamp", "ts", "time", "date", "datetime", "open_time")


def _ts(value: str) -> int:
    value = value.strip()
    try:
        n = float(value)
    except ValueError:
        return parse_time_ms(value)  # type: ignore[return-value]
    return int(n if n > 1e11 else n * 1000)


def read_csv(path: Path) -> list[Candle]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = {k.lower().strip(): k for k in reader.fieldnames or []}
        ts_key = next((fields[k] for k in _TS_KEYS if k in fields), None)
        if ts_key is None or not all(k in fields for k in ("open", "high", "low", "close")):
            raise ValueError(f"{path.name}: need columns timestamp,open,high,low,close[,volume]")
        vol_key = fields.get("volume")
        candles = [
            Candle(ts=_ts(row[ts_key]), open=float(row[fields["open"]]), high=float(row[fields["high"]]),
                   low=float(row[fields["low"]]), close=float(row[fields["close"]]),
                   volume=float(row[vol_key]) if vol_key and row[vol_key] else 0.0)
            for row in reader
        ]
    candles.sort(key=lambda c: c.ts)
    return candles


@register_provider("csv")
class CsvProvider(DataProvider):
    description = "Local CSV files (backend/data/csv/<symbol>.csv)"

    async def fetch(self, cfg: DataConfig) -> list[Candle]:
        path = (settings.csv_dir / f"{cfg.symbol}.csv").resolve()
        if settings.csv_dir.resolve() not in path.parents:
            raise ValueError("invalid csv symbol")
        if not path.exists():
            raise ValueError(f"CSV not found: {path.name} (put it in {settings.csv_dir})")
        candles = read_csv(path)
        start, end = parse_time_ms(cfg.start), parse_time_ms(cfg.end)
        if start is not None:
            candles = [c for c in candles if c.ts >= start]
        if end is not None:
            candles = [c for c in candles if c.ts <= end]
        return candles[: cfg.limit]

    async def symbols(self) -> list[str]:
        if not settings.csv_dir.exists():
            return []
        return sorted(p.stem for p in settings.csv_dir.glob("*.csv"))
