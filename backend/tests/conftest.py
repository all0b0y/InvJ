import math
import shutil

import pytest

from app.schemas import Candle
from app.settings import settings


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    shutil.copy(settings.csv_dir / "SAMPLE-SYNTH.csv", csv_dir)
    monkeypatch.setattr(settings, "csv_dir", csv_dir)
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(settings, "http_max_retries", 0)
    from app.data import base
    base.clear_cache()
    yield
    base.clear_cache()


def make_candles(n: int = 120, start: float = 100.0, step_ms: int = 3_600_000) -> list[Candle]:
    out = []
    p = start
    for i in range(n):
        o = p
        c = o * (1 + 0.01 * math.sin(i / 6))
        out.append(Candle(ts=1_704_067_200_000 + i * step_ms, open=o, high=max(o, c) * 1.002,
                          low=min(o, c) * 0.998, close=c, volume=1000 + i))
        p = c
    return out
