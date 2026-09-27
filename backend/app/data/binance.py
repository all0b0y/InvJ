"""Binance spot klines. Public endpoint, no key.

Defaults to ``data-api.binance.vision`` (market-data-only mirror that is not
geo-blocked like ``api.binance.com``); override with BINANCE_BASE_URL.
"""
from __future__ import annotations

import httpx

from ..schemas import Candle, DataConfig
from ..settings import settings
from .base import DataProvider, parse_time_ms, register_provider

INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]
_PAGE = 1000


@register_provider("binance")
class BinanceProvider(DataProvider):
    description = "Binance spot klines (crypto, no API key)"

    async def fetch(self, cfg: DataConfig) -> list[Candle]:
        if cfg.interval not in INTERVALS:
            raise ValueError(f"binance interval must be one of {INTERVALS}")
        start = parse_time_ms(cfg.start)
        end = parse_time_ms(cfg.end)
        out: list[Candle] = []
        async with httpx.AsyncClient(base_url=settings.binance_base_url, timeout=settings.http_timeout_s) as client:
            while len(out) < cfg.limit:
                params: dict = {"symbol": cfg.symbol.upper(), "interval": cfg.interval,
                                "limit": min(_PAGE, cfg.limit - len(out))}
                if start is not None:
                    params["startTime"] = start
                if end is not None:
                    params["endTime"] = end
                if start is None and out:
                    # No start given: page backwards from the newest candles.
                    params["endTime"] = out[0].ts - 1
                r = await client.get("/api/v3/klines", params=params)
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    break
                page = [Candle(ts=int(k[0]), open=float(k[1]), high=float(k[2]), low=float(k[3]),
                               close=float(k[4]), volume=float(k[5])) for k in rows]
                if start is None:
                    out = page + out
                else:
                    out.extend(page)
                    start = page[-1].ts + 1
                if len(rows) < params["limit"]:
                    break
        return out[: cfg.limit] if cfg.start else out[-cfg.limit:]

    async def symbols(self) -> list[str]:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
