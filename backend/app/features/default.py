"""Default state: last N candles + indicators + current position.

With ``anonymize`` on, the ticker and all timestamps are hidden and every price
(OHLC, price-level indicators, entry price) is rebased so the first close in the
window is 100. The model then sees shape, not a recognisable piece of history.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import indicators as ind
from .base import FeatureState, Featurizer, PortfolioView, register_featurizer


def _fmt(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _price_digits(p: float) -> int:
    return 2 if p >= 100 else 4 if p >= 1 else 6


@register_featurizer("default")
class DefaultFeaturizer(Featurizer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.series = {name: ind.compute(name, self.candles) for name in self.cfg.indicators}

    @property
    def warmup(self) -> int:
        # Start once the window is full and every indicator has a value.
        first_valid = [next((i for i, v in enumerate(s) if v is not None), 0) for s in self.series.values()]
        return max([self.cfg.window - 1, *first_valid])

    def build(self, idx: int, portfolio: PortfolioView) -> FeatureState:
        lo = max(0, idx - self.cfg.window + 1)
        window = self.candles[lo: idx + 1]
        anon = self.cfg.anonymize
        scale = 100.0 / window[0].close if anon else 1.0
        nd = 2 if anon else _price_digits(window[-1].close)
        avg_vol = sum(c.volume for c in window) / len(window) or 1.0

        rows = []
        for k, c in enumerate(window):
            t = f"t-{len(window) - 1 - k}" if anon else _iso(c.ts)
            vol = c.volume / avg_vol if anon else c.volume
            rows.append([t, round(c.open * scale, nd), round(c.high * scale, nd), round(c.low * scale, nd),
                         round(c.close * scale, nd), round(vol, 3 if anon else 2)])

        inds = {}
        for name, s in self.series.items():
            v = s[idx]
            if v is not None and ind.is_price_level(name):
                v *= scale
            inds[name] = None if v is None else round(v, nd if ind.is_price_level(name) else 2)

        pos = portfolio.position
        price = window[-1].close
        upnl_pct = 0.0
        if pos.qty:
            upnl_pct = (price / pos.entry_price - 1) * 100 * (1 if pos.qty > 0 else -1)
        position = {
            "side": pos.side,
            "size_pct_of_equity": round(abs(pos.qty) * price / portfolio.equity * 100, 1) if pos.qty else 0.0,
            "entry_price": round(pos.entry_price * scale, nd) if pos.qty else None,
            "unrealized_pnl_pct": round(upnl_pct, 2),
            "equity_return_pct": round((portfolio.equity / portfolio.initial_cash - 1) * 100, 2),
        }
        asset = "ASSET" if anon else self.symbol
        data = {
            "asset": asset,
            "interval": self.interval,
            "now": None if anon else _iso(window[-1].ts),
            "price_note": "prices rebased: first close in window = 100; volume = ratio to window average" if anon else None,
            "candles_columns": ["time", "open", "high", "low", "close", "volume"],
            "candles": rows,
            "indicators": inds,
            "position": position,
        }
        return FeatureState(data={k: v for k, v in data.items() if v is not None}, text=self._text(data, nd))

    def _text(self, d: dict, nd: int) -> str:
        p = d["position"]
        lines = [f"Asset: {d['asset']} | candle interval: {d['interval']}"]
        if d.get("now"):
            lines.append(f"Now (last closed candle): {d['now']} UTC")
        if d.get("price_note"):
            lines.append(f"Note: {d['price_note']}")
        lines.append(f"Last {len(d['candles'])} candles, oldest first:")
        lines.append(",".join(d["candles_columns"]))
        lines += [",".join(str(x) for x in r) for r in d["candles"]]
        if d["indicators"]:
            lines.append("Indicators (latest): " + ", ".join(f"{k.upper()}={_fmt(v, nd)}" for k, v in d["indicators"].items()))
        if p["side"] == "flat":
            lines.append("Position: flat (no open position)")
        else:
            lines.append(f"Position: {p['side']} {p['size_pct_of_equity']}% of equity, entry {p['entry_price']}, "
                         f"unrealized {p['unrealized_pnl_pct']:+.2f}%")
        lines.append(f"Account return so far: {p['equity_return_pct']:+.2f}%")
        return "\n".join(lines)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
