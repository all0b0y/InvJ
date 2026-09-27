"""Paper broker: one instrument, long/short, fees + slippage, fills at a given price.

Cash accounting: buying spends cash, short-selling adds proceeds to cash, so
equity = cash + qty * price holds for long, short and flat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import BrokerConfig, Fill, Position


@dataclass
class ClosedTrade:
    side: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float  # net of both fills' fees
    return_pct: float  # pnl / entry notional


@dataclass
class Broker:
    cfg: BrokerConfig
    cash: float = 0.0
    position: Position = field(default_factory=Position)
    fills: list[Fill] = field(default_factory=list)
    trades: list[ClosedTrade] = field(default_factory=list)
    _entry_ts: int = 0
    _entry_fee: float = 0.0

    def __post_init__(self):
        self.cash = self.cfg.initial_cash

    def equity(self, price: float) -> float:
        return self.cash + self.position.qty * price

    def execute(self, action: str, size_pct: float, price: float, ts: int) -> list[Fill]:
        """Apply an action at ``price`` (the next candle's open). Returns the fills made."""
        out: list[Fill] = []
        side = self.position.side
        if action == "close" or (action == "open_long" and side == "short") or (action == "open_short" and side == "long"):
            if side != "flat":
                out.append(self._close(price, ts, action))
        if action == "open_long" and self.position.side == "flat":
            f = self._open(+1, size_pct, price, ts)
            if f:
                out.append(f)
        elif action == "open_short" and self.position.side == "flat" and self.cfg.allow_short:
            f = self._open(-1, size_pct, price, ts)
            if f:
                out.append(f)
        self.fills.extend(out)
        return out

    def _fill_price(self, price: float, buy: bool) -> float:
        s = self.cfg.slippage_pct / 100
        return price * (1 + s) if buy else price * (1 - s)

    def _open(self, direction: int, size_pct: float, price: float, ts: int) -> Fill | None:
        equity = self.equity(price)
        if equity <= 0:
            return None
        fp = self._fill_price(price, buy=direction > 0)
        fee_rate = self.cfg.fee_pct / 100
        notional = equity * min(max(size_pct, 0.0), 100.0) / 100 / (1 + fee_rate)
        qty = notional / fp
        if qty <= 0:
            return None
        fee = notional * fee_rate
        self.cash -= direction * qty * fp + fee
        self.position = Position(qty=direction * qty, entry_price=fp)
        self._entry_ts, self._entry_fee = ts, fee
        return Fill(ts=ts, side="buy" if direction > 0 else "sell", qty=qty, price=fp, fee=fee,
                    reason="open_long" if direction > 0 else "open_short", realized_pnl=-fee)

    def _close(self, price: float, ts: int, reason: str) -> Fill:
        qty = self.position.qty
        buy = qty < 0
        fp = self._fill_price(price, buy=buy)
        fee = abs(qty) * fp * self.cfg.fee_pct / 100
        self.cash += qty * fp - fee
        gross = (fp - self.position.entry_price) * qty
        pnl = gross - fee - self._entry_fee
        entry_notional = abs(qty) * self.position.entry_price
        self.trades.append(ClosedTrade(
            side="long" if qty > 0 else "short", entry_ts=self._entry_ts, exit_ts=ts,
            entry_price=self.position.entry_price, exit_price=fp, qty=abs(qty), pnl=pnl,
            return_pct=pnl / entry_notional * 100 if entry_notional else 0.0,
        ))
        self.position = Position()
        return Fill(ts=ts, side="buy" if buy else "sell", qty=abs(qty), price=fp, fee=fee,
                    reason="close" if reason == "close" else "reverse", realized_pnl=gross - fee)
