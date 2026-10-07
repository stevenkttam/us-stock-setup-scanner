from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.historical import build_historical_signals
from scanner.universe import fetch_us_universe

BIN_SPECS = {
    "RVOL20": [-np.inf, 1.2, 1.5, 2.0, 3.0, np.inf],
    "BreakoutATR": [-np.inf, 0.25, 0.5, 1.0, 1.5, 2.0, np.inf],
    "ATRCompression": [-np.inf, 0.0, 0.15, 0.25, 0.35, np.inf],
    "Trend20": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "Trend50": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "Trend200": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "RS_SPY_20": [-np.inf, -0.05, 0.0, 0.05, 0.10, np.inf],
    "TR_ATR20": [-np.inf, 1.0, 1.5, 2.0, 3.0, np.inf],
    "CloseLocation": [-np.inf, 0.35, 0.50, 0.65, 0.80, np.inf],
    "BaseRangePct": [-np.inf, 0.04, 0.06, 0.08, 0.10, 0.12, np.inf],
    "ATR20Pct": [-np.inf, 0.01, 0.02, 0.04, 0.06, 0.10, np.inf],
    "RiskATR": [-np.inf, 0.50, 0.75, 1.00, 1.25, 1.50, np.inf],
    "GapPct": [-np.inf, -0.05, -0.02, 0.0, 0.02, 0.05, np.inf],
}

CANDIDATES = [
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 10,
    "TargetR": 1
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Range Expansion",
    "Feature": "BreakoutATR",
    "Bin": "(0.5, 1.0]",
    "HorizonDays": 10,
    "TargetR": 0.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 10,
    "TargetR": 1.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 10,
    "TargetR": 0.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 5,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Range Expansion",
    "Feature": "BreakoutATR",
    "Bin": "(0.5, 1.0]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "GapPct",
    "Bin": "(0.0, 0.02]",
    "HorizonDays": 10,
    "TargetR": 1
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RiskATR",
    "Bin": "(0.75, 1.0]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "GapPct",
    "Bin": "(0.0, 0.02]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Trend Continuation",
    "Feature": "BaseRangePct",
    "Bin": "(0.08, 0.1]",
    "HorizonDays": 10,
    "TargetR": 0.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 10,
    "TargetR": 1.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 10,
    "TargetR": 1
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 10,
    "TargetR": 2
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "BaseRangePct",
    "Bin": "(0.04, 0.06]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RiskATR",
    "Bin": "(1.0, 1.25]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RS_SPY_20",
    "Bin": "(-0.05, 0.0]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RiskATR",
    "Bin": "(0.75, 1.0]",
    "HorizonDays": 10,
    "TargetR": 1.5
  },
  {
    "Direction": "Long",
    "Setup": "Range Expansion",
    "Feature": "RVOL20",
    "Bin": "(2.0, 3.0]",
    "HorizonDays": 10,
    "TargetR": 1
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 10,
    "TargetR": 1.25
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RiskATR",
    "Bin": "(0.75, 1.0]",
    "HorizonDays": 10,
    "TargetR": 2
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "Trend50",
    "Bin": "(-inf, -1.0]",
    "HorizonDays": 5,
    "TargetR": 0.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "RiskATR",
    "Bin": "(1.0, 1.25]",
    "HorizonDays": 10,
    "TargetR": 0.5
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "GapPct",
    "Bin": "(0.0, 0.02]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Short",
    "Setup": "Failed Breakout Reversal",
    "Feature": "RS_SPY_20",
    "Bin": "(0.1, inf]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Range Expansion",
    "Feature": "RVOL20",
    "Bin": "(2.0, 3.0]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Trend Continuation",
    "Feature": "CloseLocation",
    "Bin": "(0.65, 0.8]",
    "HorizonDays": 10,
    "TargetR": 0.75
  },
  {
    "Direction": "Long",
    "Setup": "Failed Breakdown Reversal",
    "Feature": "CloseLocation",
    "Bin": "(0.5, 0.65]",
    "HorizonDays": 5,
    "TargetR": 0.75
  }
]

MIN_N = 100


def f(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def original_v8_sample(symbols, n=500):
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) <= n:
        return symbols
    last = len(symbols) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [symbols[i] for i in idxs]


def fresh_sample(symbols, n=1000):
    base = set(original_v8_sample(symbols, 500))
    remaining = [s for s in symbols if s not in base]
    if len(remaining) <= n:
        return remaining
    last = len(remaining) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [remaining[i] for i in idxs]


def label_trade(px, idx, direction, risk, horizon, target_r):
    if idx + 1 >= len(px) or not np.isfinite(risk) or risk <= 0:
        return 0
    entry = f(px.iloc[idx + 1]["Open"])
    if not np.isfinite(entry):
        return 0
    stop = entry - risk if direction == "Long" else entry + risk
    target = entry + target_r * risk if direction == "Long" else entry - target_r * risk
    future = px.iloc[idx + 1:min(idx + 1 + horizon, len(px))]
    for _, bar in future.iterrows():
        hi, lo = f(bar["High"]), f(bar["Low"])
        if not (np.isfinite(hi) and np.isfinite(lo)):
            continue
        if direction == "Long":
            stop_hit, target_hit = lo <= stop, hi >= target
        else:
            stop_hit, target_hit = hi >= stop, lo <= target
        if stop_hit and target_hit:
            return 0
        if target_hit:
            return 1
        if stop_hit:
            return 0
    return 0


def holm_adjust(pvals):
    pvals = np.asarray(pvals, dtype=float)
    m = len(pvals)
    order = np.argsort(pvals)
    adjusted = np.empty(m, dtype=float)
    running = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, (m - rank) * pvals[idx])
        running = max(running, val)
        adjusted[idx] = running
    return adjusted


def main():
    cfg = load_config()
    u = fetch_us_universe(ROOT / "data" / "universe.csv")
    all_symbols = u["YahooSymbol"].dropna().astype(str).tolist()
    symbols = fresh_sample(all_symbols, 1000)

    period = os.getenv("SCANNER_PERIOD", "10y")
    prices = download_daily(
        symbols,
        period=period,
        batch_size=int(os.getenv("SCANNER_BATCH_SIZE", "50")),
        sleep_seconds=0.75,
    )
    spy = download_daily(["SPY"], period=period, batch_size=1, sleep_seconds=0.1).get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY download failed")

    signal_parts = []
    for ticker, px in prices.items():
        try:
            s = build_historical_signals({ticker: px}, spy, cfg)
            if not s.empty:
                signal_parts.append(s)
        except Exception as exc:
            print(f"SKIP {ticker}: {exc}")

    signals = pd.concat(signal_parts, ignore_index=True) if signal_parts else pd.DataFrame()
    if signals.empty:
        raise RuntimeError("No signals generated in confirmatory sample")

    rows = []
    for _, s in signals.iterrows():
        feature = CANDIDATES[0]["Feature"]
        px = prices.get(str(s["Ticker"]))
        if px is None or px.empty:
            continue
        px = px.sort_index()
        try:
            idx = px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError:
            continue
        if isinstance(idx, slice) or idx + 1 >= len(px):
            continue
        risk = f(s["RiskPerShare"])
        if not np.isfinite(risk) or risk <= 0:
            continue

        for j, c in enumerate(CANDIDATES):
            if s["Direction"] != c["Direction"] or s["Setup"] != c["Setup"]:
                continue
            if c["Feature"] not in s.index:
                continue
            val = f(s[c["Feature"]])
            if not np.isfinite(val):
                continue
            label = str(pd.cut(pd.Series([val]), bins=BIN_SPECS[c["Feature"]], include_lowest=True).iloc[0])
            if label != c["Bin"]:
                continue
            o = label_trade(px, int(idx), c["Direction"], risk, int(c["HorizonDays"]), float(c["TargetR"]))
            rows.append({
                "CandidateID": j + 1,
                **c,
                "Ticker": s["Ticker"],
                "SignalDate": pd.Timestamp(s["SignalDate"]),
                "Outcome": o,
                "Half": f"{pd.Timestamp(s['SignalDate']).year}-H{1 if pd.Timestamp(s['SignalDate']).month <= 6 else 2}",
            })

    r = pd.DataFrame(rows)
    if r.empty:
        raise RuntimeError("No candidate matches in confirmatory sample")

    agg = (r.groupby(["CandidateID", "Direction", "Setup", "Feature", "Bin", "HorizonDays", "TargetR"], dropna=False)
        .agg(N=("Outcome","size"), Wins=("Outcome","sum"), SuccessRate=("Outcome","mean"))
        .reset_index())
    agg["ExpectancyR"] = agg["SuccessRate"] * (1.0 + agg["TargetR"]) - 1.0
    agg["BreakevenRate"] = 1.0 / (1.0 + agg["TargetR"])
    agg["PValueOneSided"] = [
        binomtest(int(w), int(n), p=float(p0), alternative="greater").pvalue
        for w, n, p0 in zip(agg["Wins"], agg["N"], agg["BreakevenRate"])
    ]
    agg["HolmAdjustedP"] = holm_adjust(agg["PValueOneSided"].to_numpy())

    half = (r.groupby(["CandidateID","Direction","Setup","Feature","Bin","HorizonDays","TargetR","Half"], dropna=False)
        .agg(N=("Outcome","size"), SuccessRate=("Outcome","mean"))
        .reset_index())
    half["ExpectancyR"] = half["SuccessRate"] * (1.0 + half["TargetR"]) - 1.0

    persistence = (half.groupby("CandidateID")
        .agg(HalvesObserved=("Half","nunique"),
             PositiveHalfRate=("ExpectancyR", lambda x: float((x > 0).mean())),
             PositiveHalves=("ExpectancyR", lambda x: int((x > 0).sum())))
        .reset_index())
    agg = agg.merge(persistence, on="CandidateID", how="left")

    agg["Confirmed"] = (
        (agg["N"] >= MIN_N)
        & (agg["ExpectancyR"] > 0)
        & (agg["HolmAdjustedP"] < 0.05)
        & (agg["PositiveHalfRate"] >= 0.60)
    )

    out = ROOT / "artifacts" / "confirmatory_v9"
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "universe_requested": int(len(symbols)),
        "symbols_downloaded": int(len(prices)),
        "signals_generated": int(len(signals)),
        "fresh_sample_excludes_v8_500": True,
        "candidate_count": len(CANDIDATES),
        "candidate_source": "V8 top30 validation candidates, frozen before V9 test",
        "minimum_N": MIN_N,
        "significance": "one-sided exact binomial test against target-specific breakeven win rate; Holm adjusted across 30 frozen candidates",
        "persistence": "positive expectancy in >=60% of observed half-years",
        "note": "Cross-sectional confirmatory test; no candidate retuning. Current-universe survivorship bias remains.",
    }
    pd.DataFrame(CANDIDATES).assign(CandidateID=np.arange(1, len(CANDIDATES)+1)).to_csv(out/"frozen_candidates.csv", index=False)
    agg.sort_values(["Confirmed","HolmAdjustedP","ExpectancyR"], ascending=[False,True,False]).to_csv(out/"candidate_results.csv", index=False)
    half.to_csv(out/"by_half.csv", index=False)
    r.to_parquet(out/"matched_outcomes.parquet", index=False)
    (out/"summary.json").write_text(json.dumps(meta, indent=2))

    print(json.dumps(meta, indent=2))
    print("\nCANDIDATE RESULTS")
    print(agg.sort_values(["Confirmed","HolmAdjustedP","ExpectancyR"], ascending=[False,True,False]).to_string(index=False))


if __name__ == "__main__":
    main()
