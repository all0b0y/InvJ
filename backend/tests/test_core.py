import pytest

from app import decision
from app.engine import metrics
from app.engine.broker import Broker
from app.features import PortfolioView, get_featurizer
from app.features import indicators as ind
from app.schemas import AgentConfig, Answer, BrokerConfig, Candle, FeatureConfig, ModelResult, Position, QuestionSpec

from .conftest import make_candles


def c(close, ts=0):
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=1)


# ------------------------------------------------------------------ indicators


def test_sma_ema_rsi_basic():
    cs = [c(x, i) for i, x in enumerate([1, 2, 3, 4, 5, 6])]
    assert ind.compute("sma3", cs) == [None, None, 2.0, 3.0, 4.0, 5.0]
    ema = ind.compute("ema3", cs)
    assert ema[:2] == [None, None] and ema[2] == 2.0 and ema[3] == pytest.approx(3.0)
    rsi = ind.compute("rsi3", cs)
    assert rsi[:3] == [None, None, None] and rsi[3] == 100.0  # only gains
    assert len(rsi) == len(cs)


def test_indicators_are_causal():
    cs = make_candles(80)
    for name in ["sma20", "ema10", "rsi14", "atr14", "ret5", "vol_ratio"]:
        full = ind.compute(name, cs)
        partial = ind.compute(name, cs[:50])
        assert full[:50] == partial, name


def test_unknown_indicator():
    with pytest.raises(ValueError):
        ind.compute("foo7", make_candles(10))


# ------------------------------------------------------------------ broker


def test_long_roundtrip_fees_and_slippage():
    b = Broker(BrokerConfig(initial_cash=1000, fee_pct=0.1, slippage_pct=0.0))
    fills = b.execute("open_long", 100, price=100, ts=1)
    assert len(fills) == 1 and b.position.side == "long"
    assert b.equity(100) == pytest.approx(1000 - fills[0].fee)
    assert b.cash == pytest.approx(0, abs=1e-9)
    b.execute("close", 100, price=110, ts=2)
    assert b.position.side == "flat"
    t = b.trades[0]
    assert t.side == "long" and t.pnl == pytest.approx(b.cash - 1000)
    assert t.pnl > 0


def test_short_and_reverse():
    b = Broker(BrokerConfig(initial_cash=1000, fee_pct=0.0, slippage_pct=0.0))
    b.execute("open_short", 50, price=100, ts=1)
    assert b.position.qty == pytest.approx(-5)
    assert b.equity(90) == pytest.approx(1050)
    fills = b.execute("open_long", 100, price=90, ts=2)  # reverse
    assert [f.reason for f in fills] == ["reverse", "open_long"]
    assert b.position.side == "long" and b.trades[0].pnl == pytest.approx(50)
    assert b.equity(90) == pytest.approx(1050)


def test_no_short_when_disabled_and_idempotent_actions():
    b = Broker(BrokerConfig(allow_short=False, fee_pct=0, slippage_pct=0))
    assert b.execute("open_short", 100, 100, 1) == []
    assert b.execute("close", 100, 100, 1) == []
    b.execute("open_long", 50, 100, 1)
    assert b.execute("open_long", 50, 100, 2) == []  # no pyramiding
    assert b.execute("hold", 50, 100, 2) == []


def test_slippage_is_adverse():
    b = Broker(BrokerConfig(fee_pct=0, slippage_pct=1.0))
    assert b.execute("open_long", 100, 100, 1)[0].price == pytest.approx(101)
    assert b.execute("close", 100, 100, 2)[0].price == pytest.approx(99)


# ------------------------------------------------------------------ featurizer


def _view(cash=10_000):
    return PortfolioView(position=Position(), cash=cash, equity=cash, initial_cash=cash)


def test_featurizer_no_lookahead_and_anonymize():
    cs = make_candles(100)
    f = get_featurizer(FeatureConfig(window=10, indicators=["sma20", "rsi14"]), cs, "BTCUSDT", "1h")
    assert f.warmup == 19  # sma20 needs 20 candles
    s = f.build(40, _view())
    assert len(s.data["candles"]) == 10 and s.data["candles"][-1][4] == round(cs[40].close, 2)
    assert "BTCUSDT" in s.text and "2024-" in s.text

    fa = get_featurizer(FeatureConfig(window=10, indicators=["sma20"], anonymize=True), cs, "BTCUSDT", "1h")
    sa = fa.build(40, _view())
    assert "BTCUSDT" not in sa.text and "2024" not in sa.text
    assert sa.data["candles"][0][4] == 100.0
    assert sa.data["candles"][-1][0] == "t-0"


# ------------------------------------------------------------------ decision schema


def test_default_schema_and_resolve():
    cfg = AgentConfig()
    qs = decision.build_questions(cfg)
    assert [q.id for q in qs] == ["action", "size", "forecast"]
    r = ModelResult(answers={"action": Answer(choice="open_short"), "size": Answer(choice="50"),
                             "forecast": Answer(choice="down", confidence=0.7)})
    assert decision.resolve(qs, r, 100) == ("open_short", 50.0, "down")
    assert decision.forecast_probs(qs, r) == pytest.approx({"up": 0.15, "down": 0.7, "flat": 0.15})
    bad = ModelResult(answers={"action": Answer(choice="yolo")})
    assert decision.resolve(qs, bad, 100)[0] == "hold"


def test_long_only_schema_drops_short():
    cfg = AgentConfig(broker=BrokerConfig(allow_short=False))
    assert "open_short" not in decision.build_questions(cfg)[0].options


def test_custom_schema_validation():
    cfg = AgentConfig()
    cfg.decision.schema_name = "custom"
    cfg.decision.questions = [QuestionSpec(id="a", instructions="x", options={"open_long": "", "hold": ""}, role="action"),
                              QuestionSpec(id="risk", instructions="y", options={"low": "", "high": ""})]
    assert len(decision.build_questions(cfg)) == 2
    cfg.decision.questions[0].options = {"buy": "", "hold": ""}
    with pytest.raises(ValueError):
        decision.build_questions(cfg)


# ------------------------------------------------------------------ metrics


def test_outcome_and_drawdown():
    cs = [c(100, 0), c(101, 1), c(99, 2), c(99.05, 3)]
    assert metrics.outcome(cs, 0, 1, 0.5) == "up"
    assert metrics.outcome(cs, 1, 1, 0.5) == "down"
    assert metrics.outcome(cs, 2, 1, 0.5) == "flat"
    assert metrics.outcome(cs, 3, 1, 0.5) is None
    m = metrics.compute(steps=[], equity_curve=[(0, 100), (1, 120), (2, 90), (3, 100)], trades=[], candles=cs,
                        start_idx=0, progress_idx=3, initial_cash=100, horizon=1, flat_pct=0.5)
    assert m["trading"]["max_drawdown_pct"] == pytest.approx(25.0)
    assert m["trading"]["return_pct"] == 0
