from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def add_features(df: pd.DataFrame, spy: pd.DataFrame | None = None) -> pd.DataFrame:
    d = df.copy().sort_index()
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    d["EMA20"] = _ema(d["Close"], 20)
    d["EMA50"] = _ema(d["Close"], 50)
    d["EMA200"] = _ema(d["Close"], 200)
    d["ATR10"] = _atr(d, 10)
    d["ATR20"] = _atr(d, 20)
    d["ATR40"] = _atr(d, 40)
    d["RVOL20"] = d["Volume"] / d["Volume"].rolling(20, min_periods=20).mean()
    d["VolumeAcceleration"] = d["Volume"] / d["Volume"].shift(1)
    d["DollarVolume20"] = (d["Close"] * d["Volume"]).rolling(20, min_periods=20).mean()
    d["TR"] = np.maximum.reduce([
        (d["High"] - d["Low"]).to_numpy(),
        (d["High"] - d["Close"].shift(1)).abs().to_numpy(),
        (d["Low"] - d["Close"].shift(1)).abs().to_numpy(),
    ])
    d["TR_ATR20"] = d["TR"] / d["ATR20"]
    d["CloseLocation"] = (d["Close"] - d["Low"]) / (d["High"] - d["Low"]).replace(0, np.nan)
    d["ATRCompression"] = 1 - (d["ATR10"] / d["ATR40"])
    d["Trend20"] = (d["Close"] - d["EMA20"]) / d["ATR20"]
    d["Trend50"] = (d["EMA20"] - d["EMA50"]) / d["ATR20"]
    d["Trend200"] = (d["EMA50"] - d["EMA200"]) / d["ATR20"]
    d["Return20"] = d["Close"].pct_change(20, fill_method=None)
    d["GapPct"] = d["Open"] / d["Close"].shift(1) - 1

    if spy is not None:
        s = spy.copy().sort_index()
        s["Close"] = pd.to_numeric(s["Close"], errors="coerce")
        spy_ret20 = s["Close"].pct_change(20).reindex(d.index).ffill()
        d["RS_SPY_20"] = d["Return20"] - spy_ret20
    else:
        d["RS_SPY_20"] = np.nan

    # Rolling support/resistance definitions exclude today's bar.
    for n in [4, 5, 6, 7, 8, 10, 12, 15, 20, 50]:
        d[f"HH_{n}"] = d["High"].rolling(n, min_periods=n).max().shift(1)
        d[f"LL_{n}"] = d["Low"].rolling(n, min_periods=n).min().shift(1)
        d[f"RangePct_{n}"] = (d[f"HH_{n}"] - d[f"LL_{n}"]) / d["Close"].shift(1)

    return d


def classify_regime(spy: pd.DataFrame) -> pd.Series:
    s = spy.copy().sort_index()
    close = pd.to_numeric(s["Close"], errors="coerce")
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    atr20 = _atr(s.assign(Open=s["Open"], High=s["High"], Low=s["Low"], Close=close, Volume=s["Volume"]), 20)
    tr = (s["High"] - s["Low"]).abs()
    high_vol = tr.rolling(20, min_periods=20).mean() / close > 0.025
    regime = pd.Series("Neutral", index=s.index)
    regime[(close > ema20) & (ema20 > ema50) & (ema50 > ema200)] = "Bullish"
    regime[(close < ema20) & (ema20 < ema50) & (ema50 < ema200)] = "Bearish"
    regime[high_vol] = regime[high_vol].astype(str) + " / High Vol"
    return regime
