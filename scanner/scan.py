from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x))) if np.isfinite(x) else 0.0


def score_volume(rvol: float) -> float:
    if not np.isfinite(rvol):
        return 0.0
    points = np.interp(rvol, [1.0, 1.5, 2.0, 2.5, 3.0], [25, 55, 75, 90, 100])
    return clamp(points)


def score_candidate(row: pd.Series, direction: str, n: int, regime: str) -> dict[str, Any] | None:
    close, atr = row.get("Close"), row.get("ATR20")
    if not all(np.isfinite([close, atr])) or atr <= 0:
        return None

    resistance = row.get(f"HH_{n}")
    support = row.get(f"LL_{n}")
    rng = row.get(f"RangePct_{n}")
    atr10 = row.get("ATR10", np.nan)
    atr40 = row.get("ATR40", np.nan)
    comp = atr10 / atr40 if np.isfinite(atr10) and np.isfinite(atr40) and atr40 > 0 else np.nan
    rvol = row.get("RVOL20", np.nan)
    close_loc = row.get("CloseLocation", np.nan)

    if direction == "Long":
        if not np.isfinite(resistance) or close <= resistance:
            return None
        breakout_atr = (close - resistance) / atr
        if breakout_atr < 0.25 or rvol < 1.20 or close_loc < 0.65:
            return None
        trend_score = np.mean([
            100 if close > row.get("EMA20", np.nan) else 0,
            100 if row.get("EMA20", np.nan) > row.get("EMA50", np.nan) else 0,
            100 if row.get("EMA50", np.nan) > row.get("EMA200", np.nan) else 0,
        ])
        rs_score = clamp(50 + row.get("RS_SPY_20", 0) * 1000)
        breakout_score = clamp(50 + breakout_atr * 35)
        trend_label = "Strong" if trend_score >= 80 else "Mixed"
        setup = "Consolidation Breakout"
        if rng <= 0.08 and comp <= 0.75:
            setup = "VCP / Volatility Contraction Breakout"
        elif row.get("TR_ATR20", 0) >= 1.50:
            setup = "Range Expansion"
        elif trend_score >= 80:
            setup = "Trend Continuation"
        why = f"{int(n)}d base + {rvol:.1f}x RVOL + {breakout_atr:.1f} ATR breakout + {trend_label.lower()} trend"
        structure_stop = row.get(f"LL_{n}", close - 2 * atr) - 0.25 * atr
        stop = max(structure_stop, close - 1.5 * atr)
        risk = close - stop
        target = close + 2 * risk
    else:
        if not np.isfinite(support) or close >= support:
            return None
        breakdown_atr = (support - close) / atr
        if breakdown_atr < 0.25 or rvol < 1.20 or close_loc > 0.35:
            return None
        trend_score = np.mean([
            100 if close < row.get("EMA20", np.nan) else 0,
            100 if row.get("EMA20", np.nan) < row.get("EMA50", np.nan) else 0,
            100 if row.get("EMA50", np.nan) < row.get("EMA200", np.nan) else 0,
        ])
        rs_score = clamp(50 - row.get("RS_SPY_20", 0) * 1000)
        breakout_score = clamp(50 + breakdown_atr * 35)
        trend_label = "Strong" if trend_score >= 80 else "Mixed"
        setup = "Consolidation Breakdown"
        if rng <= 0.08 and comp <= 0.75:
            setup = "VCP / Volatility Contraction Breakdown"
        elif row.get("TR_ATR20", 0) >= 1.50:
            setup = "Range Expansion Down"
        elif trend_score >= 80:
            setup = "Trend Breakdown"
        why = f"{int(n)}d base + {rvol:.1f}x RVOL + {breakdown_atr:.1f} ATR breakdown + {trend_label.lower()} trend"
        structure_stop = row.get(f"HH_{n}", close + 2 * atr) + 0.25 * atr
        stop = min(structure_stop, close + 1.5 * atr)
        risk = stop - close
        target = close - 2 * risk

    consolidation_score = clamp(100 - (rng / 0.12) * 100) if np.isfinite(rng) else 0
    volatility_score = clamp((1 - comp) * 300) if np.isfinite(comp) else 0
    market_score = 80 if ("Bullish" in str(regime) and direction == "Long") or ("Bearish" in str(regime) and direction == "Short") else 45
    score = (
        0.20 * breakout_score
        + 0.20 * score_volume(rvol)
        + 0.15 * consolidation_score
        + 0.15 * trend_score
        + 0.10 * volatility_score
        + 0.10 * rs_score
        + 0.10 * market_score
    )
    return {
        "Direction": direction,
        "Setup": setup,
        "SetupScore": round(score, 1),
        "RVOL20": round(float(rvol), 2),
        "BaseDays": int(n),
        "BreakoutATR": round(float((close - resistance) / atr if direction == "Long" else (support - close) / atr), 2),
        "Trend": trend_label,
        "ATRCompression": round(float(1 - comp), 3) if np.isfinite(comp) else np.nan,
        "Trend20": float(row.get("Trend20", np.nan)),
        "Trend50": float(row.get("Trend50", np.nan)),
        "Trend200": float(row.get("Trend200", np.nan)),
        "RS_SPY_20": float(row.get("RS_SPY_20", np.nan)),
        "TR_ATR20": float(row.get("TR_ATR20", np.nan)),
        "Entry": float(close),
        "Stop": float(stop),
        "Target2R": float(target),
        "RiskPerShare": float(risk),
        "MarketRegime": str(regime),
        "Why": why,
    }


def scan_symbol(df: pd.DataFrame, ticker: str, regime: str, config: dict) -> tuple[list[dict], list[dict]]:
    latest = df.iloc[-1]
    longs, shorts = [], []
    for n in config["signal"]["consolidation_lengths"]:
        for direction, store in [("Long", longs), ("Short", shorts)]:
            result = score_candidate(latest, direction, n, regime)
            if result:
                result["Ticker"] = ticker
                store.append(result)
    # Keep best detected setup per direction for the ticker.
    def best(items):
        if not items:
            return None
        return sorted(items, key=lambda x: x["SetupScore"], reverse=True)[0]
    return ([best(longs)] if best(longs) else [], [best(shorts)] if best(shorts) else [])
