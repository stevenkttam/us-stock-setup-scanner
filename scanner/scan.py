from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd

def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x))) if np.isfinite(x) else 0.0

def score_volume(rvol: float) -> float:
    if not np.isfinite(rvol): return 0.0
    return clamp(np.interp(rvol,[1.0,1.5,2.0,2.5,3.0],[25,55,75,90,100]))

def score_candidate(row: pd.Series, direction: str, n: int, regime: str, signal_cfg: dict | None = None) -> dict[str, Any] | None:
    cfg = signal_cfg or {}
    range_max = float(cfg.get("consolidation_range_max", 0.12))
    atr_ratio_max = float(cfg.get("atr10_atr40_max", 0.85))
    breakout_min = float(cfg.get("breakout_atr_min", 0.25))
    rvol_min = float(cfg.get("rvol20_min", 1.20))
    loc_long = float(cfg.get("close_location_long_min", 0.65))
    loc_short = float(cfg.get("close_location_short_max", 0.35))
    range_exp = float(cfg.get("range_expansion_tr_min", 1.50))

    close = row.get("Close", np.nan)
    atr = row.get("ATR20", np.nan)
    prev_close = row.get("Close", np.nan)
    if not np.isfinite(close) or not np.isfinite(atr) or atr <= 0:
        return None

    resistance = row.get(f"HH_{n}", np.nan)
    support = row.get(f"LL_{n}", np.nan)
    rng = row.get(f"RangePct_{n}", np.nan)
    a10, a40 = row.get("ATR10", np.nan), row.get("ATR40", np.nan)
    comp = a10 / a40 if np.isfinite(a10) and np.isfinite(a40) and a40 > 0 else np.nan
    rvol = row.get("RVOL20", np.nan)
    loc = row.get("CloseLocation", np.nan)
    tr_atr = row.get("TR_ATR20", np.nan)
    high = row.get("High", np.nan)
    low = row.get("Low", np.nan)

    if not np.isfinite(rvol) or rvol < rvol_min or not np.isfinite(loc):
        return None

    trend_long = np.mean([
        100 if close > row.get("EMA20", np.nan) else 0,
        100 if row.get("EMA20", np.nan) > row.get("EMA50", np.nan) else 0,
        100 if row.get("EMA50", np.nan) > row.get("EMA200", np.nan) else 0,
    ])
    trend_short = np.mean([
        100 if close < row.get("EMA20", np.nan) else 0,
        100 if row.get("EMA20", np.nan) < row.get("EMA50", np.nan) else 0,
        100 if row.get("EMA50", np.nan) < row.get("EMA200", np.nan) else 0,
    ])
    rs_long = clamp(50 + row.get("RS_SPY_20", 0) * 1000)
    rs_short = clamp(50 - row.get("RS_SPY_20", 0) * 1000)
    trend = trend_long if direction == "Long" else trend_short
    rs = rs_long if direction == "Long" else rs_short

    # Separate the setup families so that range-expansion and reversal
    # candidates are not incorrectly forced through the consolidation filter.
    setup = None
    trigger = np.nan
    structure_level = np.nan

    if direction == "Long":
        if np.isfinite(support) and np.isfinite(low) and low < support and close > support and loc >= 0.55:
            rebound = (close - support) / atr
            if rebound >= 0.10:
                setup = "Failed Breakdown Reversal"
                trigger = rebound
                structure_level = low - 0.25 * atr
        if setup is None and np.isfinite(resistance) and close > resistance and loc >= loc_long:
            br = (close - resistance) / atr
            if br >= breakout_min:
                if np.isfinite(rng) and np.isfinite(comp) and rng <= range_max and comp <= atr_ratio_max and rng <= 0.08 and comp <= 0.75:
                    setup = "VCP / Volatility Contraction Breakout"
                elif np.isfinite(tr_atr) and tr_atr >= range_exp:
                    setup = "Range Expansion"
                elif trend >= 80:
                    setup = "Trend Continuation"
                elif np.isfinite(rng) and np.isfinite(comp) and rng <= range_max and comp <= atr_ratio_max:
                    setup = "Consolidation Breakout"
                if setup:
                    trigger = br
                    structure_level = (support - 0.25 * atr) if np.isfinite(support) else (close - 1.5 * atr)
        if setup is None and trend >= 80 and close > row.get("EMA20", np.nan) and np.isfinite(tr_atr) and tr_atr >= 1.0:
            continuation = (close - row.get("EMA20", close)) / atr
            if continuation >= 0.10:
                setup = "Trend Continuation"
                trigger = continuation
                structure_level = (support - 0.25 * atr) if np.isfinite(support) else (close - 1.5 * atr)
        if setup is None and np.isfinite(tr_atr) and tr_atr >= range_exp and loc >= loc_long:
            setup = "Range Expansion"
            trigger = tr_atr
            structure_level = (support - 0.25 * atr) if np.isfinite(support) else (close - 1.5 * atr)
        if setup is None:
            return None

        stop = max(structure_level, close - 1.5 * atr)
        risk = close - stop
        target = close + 2 * risk
        label = "Strong" if trend >= 80 else "Mixed"
        breakout_score = clamp(50 + float(trigger) * 35)

    else:
        if np.isfinite(resistance) and np.isfinite(high) and high > resistance and close < resistance and loc <= 0.45:
            rejection = (resistance - close) / atr
            if rejection >= 0.10:
                setup = "Failed Breakout Reversal"
                trigger = rejection
                structure_level = high + 0.25 * atr
        if setup is None and np.isfinite(support) and close < support and loc <= loc_short:
            br = (support - close) / atr
            if br >= breakout_min:
                if np.isfinite(rng) and np.isfinite(comp) and rng <= range_max and comp <= atr_ratio_max and rng <= 0.08 and comp <= 0.75:
                    setup = "VCP / Volatility Contraction Breakdown"
                elif np.isfinite(tr_atr) and tr_atr >= range_exp:
                    setup = "Range Expansion Down"
                elif trend >= 80:
                    setup = "Trend Breakdown"
                elif np.isfinite(rng) and np.isfinite(comp) and rng <= range_max and comp <= atr_ratio_max:
                    setup = "Consolidation Breakdown"
                if setup:
                    trigger = br
                    structure_level = (resistance + 0.25 * atr) if np.isfinite(resistance) else (close + 1.5 * atr)
        if setup is None and trend >= 80 and close < row.get("EMA20", np.inf) and np.isfinite(tr_atr) and tr_atr >= 1.0:
            continuation = (row.get("EMA20", close) - close) / atr
            if continuation >= 0.10:
                setup = "Trend Breakdown"
                trigger = continuation
                structure_level = (resistance + 0.25 * atr) if np.isfinite(resistance) else (close + 1.5 * atr)
        if setup is None and np.isfinite(tr_atr) and tr_atr >= range_exp and loc <= loc_short:
            setup = "Range Expansion Down"
            trigger = tr_atr
            structure_level = (resistance + 0.25 * atr) if np.isfinite(resistance) else (close + 1.5 * atr)
        if setup is None:
            return None

        stop = min(structure_level, close + 1.5 * atr)
        risk = stop - close
        target = close - 2 * risk
        label = "Strong" if trend >= 80 else "Mixed"
        breakout_score = clamp(50 + float(trigger) * 35)

    if not np.isfinite(risk) or risk <= 0:
        return None

    consolidation = clamp(100 - (float(rng) / range_max) * 100) if np.isfinite(rng) and range_max > 0 else 50.0
    volatility = clamp((1 - float(comp)) * 300) if np.isfinite(comp) else 50.0
    market = 80 if (
        ("Bullish" in str(regime) and direction == "Long")
        or ("Bearish" in str(regime) and direction == "Short")
    ) else 45
    score = (
        0.20 * breakout_score
        + 0.20 * score_volume(rvol)
        + 0.15 * consolidation
        + 0.15 * trend
        + 0.10 * volatility
        + 0.10 * rs
        + 0.10 * market
    )

    why = (
        f"{n}d setup + {rvol:.1f}x RVOL + trigger {float(trigger):.1f} ATR + "
        f"{label.lower()} trend"
    )
    return {
        "Direction": direction,
        "Setup": setup,
        "SetupScore": round(score, 1),
        "RVOL20": round(float(rvol), 2),
        "BaseDays": int(n),
        "BreakoutATR": round(float(trigger), 2),
        "Trend": label,
        "ATRCompression": round(float(1 - comp), 3) if np.isfinite(comp) else 0.0,
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

def scan_symbol(df:pd.DataFrame,ticker:str,regime:str,config:dict)->tuple[list[dict],list[dict]]:
    latest=df.iloc[-1]; longs=[]; shorts=[]
    for n in config["signal"]["consolidation_lengths"]:
        for direction,store in [("Long",longs),("Short",shorts)]:
            result=score_candidate(latest,direction,n,regime,config["signal"])
            if result: result["Ticker"]=ticker; store.append(result)
    def best(items): return sorted(items,key=lambda x:x["SetupScore"],reverse=True)[0] if items else None
    return ([best(longs)] if best(longs) else [],[best(shorts)] if best(shorts) else [])
