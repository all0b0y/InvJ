"""Generate data/csv/SAMPLE-SYNTH.csv: synthetic hourly OHLCV (NOT real market data).

Regime-switching random walk, fixed seed, so offline demos and tests are reproducible.
Usage: python scripts/make_sample_csv.py [rows]
"""
import csv
import math
import random
import sys
from pathlib import Path

rows = int(sys.argv[1]) if len(sys.argv) > 1 else 600
rng = random.Random(7)
out = Path(__file__).resolve().parent.parent / "data" / "csv" / "SAMPLE-SYNTH.csv"
out.parent.mkdir(parents=True, exist_ok=True)

ts = 1_704_067_200  # 2024-01-01T00:00:00Z
price = 100.0
drift = 0.0
with out.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for i in range(rows):
        if i % 80 == 0:
            drift = rng.choice([-0.0015, 0.0, 0.0015, 0.002])
        o = price
        c = o * math.exp(drift + rng.gauss(0, 0.008))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.003)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.003)))
        v = 1000 * math.exp(rng.gauss(0, 0.4))
        w.writerow([ts, f"{o:.4f}", f"{h:.4f}", f"{lo:.4f}", f"{c:.4f}", f"{v:.2f}"])
        price = c
        ts += 3600
print(f"wrote {rows} rows to {out}")
