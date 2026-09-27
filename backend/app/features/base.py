"""Featurizers turn (candles so far, portfolio) into the state a model sees.

To change what agents see (add news, order book, multi-timeframe...): write a
``Featurizer`` subclass, register it with ``@register_featurizer("name")``,
import it in ``app/features/__init__.py`` and set ``features.name`` in the agent config.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from ..schemas import Candle, FeatureConfig, Position

_FEATURIZERS: dict[str, type["Featurizer"]] = {}


@dataclass
class PortfolioView:
    position: Position
    cash: float
    equity: float
    initial_cash: float


@dataclass
class FeatureState:
    data: dict[str, Any]  # structured form (sent when state_format == "json")
    text: str  # rendered form (sent when state_format == "text")


class Featurizer(ABC):
    def __init__(self, cfg: FeatureConfig, candles: list[Candle], symbol: str, interval: str):
        self.cfg = cfg
        self.candles = candles
        self.symbol = symbol
        self.interval = interval

    @property
    def warmup(self) -> int:
        """First candle index at which a decision can be made."""
        return self.cfg.window - 1

    @abstractmethod
    def build(self, idx: int, portfolio: PortfolioView) -> FeatureState:
        """State after candle ``idx`` closed. Must not read candles[idx+1:]."""


def register_featurizer(name: str) -> Callable[[type[Featurizer]], type[Featurizer]]:
    def deco(cls: type[Featurizer]) -> type[Featurizer]:
        _FEATURIZERS[name] = cls
        return cls
    return deco


def get_featurizer(cfg: FeatureConfig, candles: list[Candle], symbol: str, interval: str) -> Featurizer:
    try:
        cls = _FEATURIZERS[cfg.name]
    except KeyError:
        raise ValueError(f"unknown featurizer {cfg.name!r}; known: {sorted(_FEATURIZERS)}") from None
    return cls(cfg, candles, symbol, interval)


def list_featurizers() -> list[str]:
    return sorted(_FEATURIZERS)
