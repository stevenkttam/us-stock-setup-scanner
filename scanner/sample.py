from __future__ import annotations

import numpy as np
import pandas as pd


def sample_rankings() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows_l = [
        ["NVDA", 78.0, 95.0, "VCP / Volatility Contraction Breakout", 2.8, 9, 1.4, "Strong", 1.42, "A", "2.8x RVOL + 9d compression + 1.4 ATR breakout"],
        ["AMD", 76.0, 92.0, "Consolidation Breakout", 2.5, 7, 1.2, "Strong", 1.31, "A", "2.5x RVOL + 7d base + strong trend"],
        ["PLTR", 73.0, 90.0, "Trend Continuation", 2.1, 8, 1.0, "Strong", 1.20, "A", "2.1x RVOL + trend alignment"],
        ["MU", 69.0, 88.0, "VCP / Volatility Contraction Breakout", 1.9, 10, 0.9, "Strong", 1.05, "B", "Tight base + volume expansion"],
        ["CRWD", 66.0, 84.0, "Consolidation Breakout", 1.8, 6, 0.8, "Mixed", 0.94, "B", "Breakout + improving volume"],
    ]
    rows_s = [
        ["TSLA", 75.0, 93.0, "Trend Breakdown", 2.9, 8, 1.3, "Strong", 1.37, "A", "2.9x RVOL + downside break + weak trend"],
        ["PYPL", 72.0, 90.0, "Consolidation Breakdown", 2.4, 9, 1.1, "Strong", 1.18, "A", "2.4x RVOL + 9d base + weak relative strength"],
        ["SMCI", 68.0, 86.0, "Range Expansion Down", 2.2, 5, 1.0, "Strong", 1.02, "B", "Range expansion + volume"],
        ["INTC", 64.0, 82.0, "Consolidation Breakdown", 1.7, 7, 0.8, "Mixed", 0.86, "B", "Breakdown near support"],
        ["SHOP", 61.0, 79.0, "Failed Breakout Reversal", 1.6, 6, 0.6, "Mixed", 0.74, "B", "Failed breakout + relative weakness"],
    ]
    cols = ["Ticker", "Probability", "SetupScore", "Setup", "RVOL20", "BaseDays", "BreakoutATR", "Trend", "ExpectedR", "Priority", "Why"]
    return pd.DataFrame(rows_l, columns=cols), pd.DataFrame(rows_s, columns=cols)
