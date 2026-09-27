"""Pluggable market-data sources.

To add a source (Yahoo, Alpha Vantage, CoinGecko, ...): subclass ``DataProvider``,
decorate it with ``@register_provider("name")`` and import the module in
``app/data/__init__.py``. Agents pick it by name in ``DataConfig.provider``.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional

from ..schemas import Candle, DataConfig

_PROVIDERS: dict[str, "DataProvider"] = {}
_CACHE: dict[str, list[Candle]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


class DataProvider(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    async def fetch(self, cfg: DataConfig) -> list[Candle]:
        """Return candles sorted by ``ts`` ascending."""

    async def symbols(self) -> list[str]:
        return []


def register_provider(name: str) -> Callable[[type[DataProvider]], type[DataProvider]]:
    def deco(cls: type[DataProvider]) -> type[DataProvider]:
        inst = cls()
        inst.name = name
        _PROVIDERS[name] = inst
        return cls

    return deco


def get_provider(name: str) -> DataProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown data provider {name!r}; known: {sorted(_PROVIDERS)}") from None


def list_providers() -> list[dict]:
    return [{"name": n, "description": p.description} for n, p in sorted(_PROVIDERS.items())]


async def load_candles(cfg: DataConfig) -> list[Candle]:
    """Fetch once per distinct config; agents on the same data share the result."""
    key = cfg.key()
    if key in _CACHE:
        return _CACHE[key]
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if key not in _CACHE:
            candles = await get_provider(cfg.provider).fetch(cfg)
            if not candles:
                raise ValueError(f"no candles for {cfg.symbol} {cfg.interval} ({cfg.provider})")
            _CACHE[key] = candles
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()


def parse_time_ms(value: Optional[str]) -> Optional[int]:
    """ISO date or datetime (assumed UTC when naive) -> epoch ms."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
