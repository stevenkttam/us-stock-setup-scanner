from __future__ import annotations

import numpy as np
import pandas as pd


def label_trade(df: pd.DataFrame, signal_idx: int, direction: str, entry: float, stop: float, target: float, holding_period: int = 10) -> int:
    """Return 1 when target is reached before stop, else 0.

    Conservative ambiguity rule: if both stop and target are touched in one
    daily bar, classify the observation as a failure because daily OHLC cannot
    identify the intraday ordering.
    """
    end = min(signal_idx + 1 + holding_period, len(df))
    future = df.iloc[signal_idx + 1:end]
    for _, bar in future.iterrows():
        high, low = float(bar["High"]), float(bar["Low"])
        if direction == "Long":
            stop_hit = low <= stop
            target_hit = high >= target
        else:
            stop_hit = high >= stop
            target_hit = low <= target
        if stop_hit and target_hit:
            return 0
        if target_hit:
            return 1
        if stop_hit:
            return 0
    return 0


def backtest_candidates(signal_df: pd.DataFrame, price_map: dict[str, pd.DataFrame], holding_period: int = 10) -> pd.DataFrame:
    rows = []
    for ticker, g in signal_df.groupby("Ticker"):
        prices = price_map.get(ticker)
        if prices is None or prices.empty:
            continue
        prices = prices.sort_index()
        idx_map = {d: i for i, d in enumerate(prices.index)}
        for _, s in g.iterrows():
            date = pd.Timestamp(s["SignalDate"])
            if date not in idx_map:
                continue
            i = idx_map[date]
            if i + 1 >= len(prices):
                continue
            entry = float(prices.iloc[i + 1]["Open"])
            # Re-anchor stop/target to next-day entry to avoid lookahead.
            rps = abs(float(s["Entry"]) - float(s["Stop"]))
            if s["Direction"] == "Long":
                stop = entry - rps
                target = entry + 2 * rps
            else:
                stop = entry + rps
                target = entry - 2 * rps
            outcome = label_trade(prices, i, s["Direction"], entry, stop, target, holding_period)
            rows.append({
                **s.to_dict(),
                "EntryActual": entry,
                "StopActual": stop,
                "TargetActual": target,
                "Outcome": outcome,
            })
    return pd.DataFrame(rows)


def performance_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"signals": 0, "success_rate": np.nan}
    return {
        "signals": int(len(df)),
        "success_rate": float(df["Outcome"].mean()),
        "long_signals": int((df["Direction"] == "Long").sum()),
        "short_signals": int((df["Direction"] == "Short").sum()),
    }
