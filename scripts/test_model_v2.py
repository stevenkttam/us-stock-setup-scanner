from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.model_v2 import train_and_validate_v2


def main() -> None:
    rng = np.random.default_rng(42)
    n = 1800
    dates = pd.date_range("2024-01-02", periods=n, freq="B")

    setup = rng.choice(
        [
            "Trend Continuation",
            "Range Expansion",
            "Consolidation Breakout",
            "VCP / Volatility Contraction Breakout",
            "Failed Breakdown Reversal",
        ],
        size=n,
    )
    regime = rng.choice(["Bullish", "Neutral", "Bearish"], size=n)
    trend = rng.choice(["Strong", "Mixed"], size=n)

    d = pd.DataFrame({
        "SignalDate": dates,
        "Direction": "Long",
        "Ticker": rng.choice(["AAA", "BBB", "CCC", "DDD"], size=n),
        "Setup": setup,
        "MarketRegime": regime,
        "Trend": trend,
        "Outcome": rng.binomial(1, 0.35, size=n),
        "SetupScore": rng.uniform(45, 85, size=n),
        "RVOL20": rng.uniform(1.2, 3.0, size=n),
        "VolumeAcceleration": rng.uniform(0.6, 2.5, size=n),
        "BaseDays": rng.choice([4, 5, 6, 7, 8, 10, 12, 15, 20], size=n),
        "BreakoutATR": rng.uniform(0.25, 2.5, size=n),
        "ATRCompression": rng.uniform(0.0, 0.5, size=n),
        "Trend20": rng.uniform(-2, 4, size=n),
        "Trend50": rng.uniform(-2, 4, size=n),
        "Trend200": rng.uniform(-2, 4, size=n),
        "RS_SPY_20": rng.uniform(-0.10, 0.10, size=n),
        "TR_ATR20": rng.uniform(0.8, 3.0, size=n),
        "CloseLocation": rng.uniform(0.0, 1.0, size=n),
        "BaseRangePct": rng.uniform(0.02, 0.12, size=n),
        "ATR20Pct": rng.uniform(0.005, 0.08, size=n),
        "RiskATR": rng.uniform(0.2, 1.5, size=n),
        "DollarVolume20": rng.uniform(20e6, 500e6, size=n),
        "GapPct": rng.uniform(-0.10, 0.10, size=n),
    })

    model, report = train_and_validate_v2(d, "Long")
    assert report["metadata"]["selected_model"] in {
        "Logistic V1 Baseline",
        "Logistic V2",
        "HistGradientBoosting V2",
    }
    assert report["metadata"]["holdout"]["n"] >= 100
    print("V2 synthetic model test: OK")


if __name__ == "__main__":
    main()
