"""Causal technical indicators: value at index i only uses candles[0..i].

Names are ``<kind><period>`` (``sma20``, ``ema50``, ``rsi14``, ``atr14``, ``ret5``)
plus ``vol_ratio`` (volume / 20-candle average volume).
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from ..schemas import Candle

Series = list[Optional[float]]

# kind -> (compute(candles, period), is_price_level)
# Price-level indicators are rebased together with prices when anonymizing.
_KINDS: dict[str, tuple[Callable[[list[Candle], int], Series], bool]] = {}


def _kind(name: str, price_level: bool):
    def deco(fn):
        _KINDS[name] = (fn, price_level)
        return fn
    return deco


@_kind("sma", True)
def sma(candles: list[Candle], n: int) -> Series:
    out: Series = []
    s = 0.0
    for i, c in enumerate(candles):
        s += c.close
        if i >= n:
            s -= candles[i - n].close
        out.append(s / n if i >= n - 1 else None)
    return out


@_kind("ema", True)
def ema(candles: list[Candle], n: int) -> Series:
    out: Series = []
    k = 2 / (n + 1)
    e: Optional[float] = None
    for i, c in enumerate(candles):
        if i == n - 1:
            e = sum(x.close for x in candles[:n]) / n
        elif e is not None:
            e = c.close * k + e * (1 - k)
        out.append(e)
    return out


@_kind("rsi", False)
def rsi(candles: list[Candle], n: int) -> Series:
    """Wilder's RSI."""
    out: Series = [None]
    gain = loss = 0.0
    for i in range(1, len(candles)):
        d = candles[i].close - candles[i - 1].close
        g, l_ = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            gain += g
            loss += l_
            if i < n:
                out.append(None)
                continue
            gain /= n
            loss /= n
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l_) / n
        out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return out[: len(candles)]


@_kind("atr", True)
def atr(candles: list[Candle], n: int) -> Series:
    out: Series = []
    a: Optional[float] = None
    trs: list[float] = []
    for i, c in enumerate(candles):
        prev = candles[i - 1].close if i else c.close
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        trs.append(tr)
        if i == n - 1:
            a = sum(trs) / n
        elif a is not None:
            a = (a * (n - 1) + tr) / n
        out.append(a)
    return out


@_kind("ret", False)
def ret(candles: list[Candle], n: int) -> Series:
    """% return over the last n candles."""
    return [None if i < n else (c.close / candles[i - n].close - 1) * 100 for i, c in enumerate(candles)]


def vol_ratio(candles: list[Candle], n: int = 20) -> Series:
    out: Series = []
    s = 0.0
    for i, c in enumerate(candles):
        s += c.volume
        if i >= n:
            s -= candles[i - n].volume
        avg = s / n if i >= n - 1 else None
        out.append(c.volume / avg if avg else None)
    return out


_NAME = re.compile(r"^([a-z]+)(\d+)$")


def is_price_level(name: str) -> bool:
    m = _NAME.match(name)
    return bool(m and _KINDS.get(m.group(1), (None, False))[1])


def compute(name: str, candles: list[Candle]) -> Series:
    if name == "vol_ratio":
        return vol_ratio(candles)
    m = _NAME.match(name)
    if not m or m.group(1) not in _KINDS:
        raise ValueError(f"unknown indicator {name!r}; use {sorted(_KINDS)}<period> or vol_ratio")
    fn, _ = _KINDS[m.group(1)]
    return fn(candles, int(m.group(2)))


def available() -> list[str]:
    return [f"{k}<N>" for k in sorted(_KINDS)] + ["vol_ratio"]
