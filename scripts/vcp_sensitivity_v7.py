from __future__ import annotations

import json
import os
import pandas as pd
import numpy as np

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.universe import fetch_us_universe


RULES = {
    "VCP10_2_Mod":  dict(base=10, phases=2, range_decay=0.90, vol_decay=0.90, atr_ratio=0.85, breakout=0.25),
    "VCP15_2_Mod":  dict(base=15, phases=2, range_decay=0.90, vol_decay=0.90, atr_ratio=0.85, breakout=0.25),
    "VCP20_2_Mod":  dict(base=20, phases=2, range_decay=0.90, vol_decay=0.90, atr_ratio=0.85, breakout=0.25),
    "VCP15_3_Mod":  dict(base=15, phases=3, range_decay=0.90, vol_decay=0.90, atr_ratio=0.85, breakout=0.25),
    "VCP10_2_Loose":dict(base=10, phases=2, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.25),
    "VCP15_2_Loose":dict(base=15, phases=2, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.25),
    "VCP20_2_Loose":dict(base=20, phases=2, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.25),
    "VCP15_3_Loose":dict(base=15, phases=3, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.25),
    "VCP15_2_Early": dict(base=15, phases=2, range_decay=0.90, vol_decay=0.95, atr_ratio=0.90, breakout=0.10),
    "VCP20_2_Early": dict(base=20, phases=2, range_decay=0.90, vol_decay=0.95, atr_ratio=0.90, breakout=0.10),
    "VCP15_3_Early": dict(base=15, phases=3, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.10),
    "VCP20_3_Early": dict(base=20, phases=3, range_decay=0.95, vol_decay=0.95, atr_ratio=0.90, breakout=0.10),
}

TARGETS = [0.50, 0.75, 1.00, 1.50, 2.00]
HORIZONS = [5, 10]


def f(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def split_date(d):
    d = d.sort_values("SignalDate").copy()
    dates = pd.Series(d["SignalDate"].drop_duplicates().tolist())
    a = dates.iloc[int(len(dates) * 0.60)]
    b = dates.iloc[int(len(dates) * 0.75)]
    return d[d["SignalDate"] < a], d[(d["SignalDate"] >= a) & (d["SignalDate"] < b)], d[d["SignalDate"] >= b]


def detect(feat, ticker, regime, cfg, rule_name, rule):
    rows = []
    min_price = float(cfg["universe"]["min_price"])
    min_dv = float(cfg["universe"]["min_avg_dollar_volume_20d"])
    rvol_min = float(cfg["signal"]["rvol20_min"])
    loc_long = float(cfg["signal"]["close_location_long_min"])
    loc_short = float(cfg["signal"]["close_location_short_max"])

    base = rule["base"]
    chunk = base // rule["phases"]
    if chunk < 3:
        return rows

    for i in range(max(220, base + 5), len(feat)):
        row = feat.iloc[i]
        close, atr, rvol, loc, dv = map(f, [row["Close"], row["ATR20"], row["RVOL20"], row["CloseLocation"], row["DollarVolume20"]])
        hh, ll = f(row.get(f"HH_{base}", np.nan)), f(row.get(f"LL_{base}", np.nan))
        if not all(np.isfinite(x) for x in [close, atr, rvol, loc, dv, hh, ll]):
            continue
        if close < min_price or dv < min_dv or rvol < rvol_min:
            continue

        trailing = feat.iloc[i-base:i]
        segs = [trailing.iloc[j*chunk:(j+1)*chunk] for j in range(rule["phases"])]
        ranges = [(s["High"].max() - s["Low"].min()) / s["Close"].mean() for s in segs]
        vols = [s["Volume"].mean() for s in segs]
        atr_ratio = f(row["ATR10"]) / f(row["ATR40"]) if f(row["ATR40"]) > 0 else np.nan
        if not np.isfinite(atr_ratio):
            continue

        range_ok = all(ranges[j+1] <= ranges[j] * rule["range_decay"] for j in range(len(ranges)-1))
        vol_ok = all(vols[j+1] <= vols[j] * rule["vol_decay"] for j in range(len(vols)-1))
        if not (range_ok and vol_ok and atr_ratio <= rule["atr_ratio"]):
            continue

        for direction in ["Long", "Short"]:
            if direction == "Long":
                br = (close - hh) / atr
                ok = close > hh and loc >= loc_long and br >= rule["breakout"]
            else:
                br = (ll - close) / atr
                ok = close < ll and loc <= loc_short and br >= rule["breakout"]
            if not ok:
                continue

            rows.append({
                "Ticker": ticker,
                "SignalDate": pd.Timestamp(feat.index[i]),
                "Direction": direction,
                "Rule": rule_name,
                "BreakoutATR": br,
                "RVOL20": rvol,
                "CloseLocation": loc,
                "ATRCompression": 1 - atr_ratio,
                "BaseDays": base,
                "Phases": rule["phases"],
                "RangeDecay": rule["range_decay"],
                "VolumeDecay": rule["vol_decay"],
                "MarketRegime": str(regime.iloc[i]),
            })
    return rows


def outcome(px, idx, direction, atr, horizon, target_r):
    if idx + 1 >= len(px) or not np.isfinite(atr) or atr <= 0:
        return 0, "invalid"
    entry = f(px.iloc[idx+1]["Open"])
    if not np.isfinite(entry):
        return 0, "bad_open"
    stop = entry - atr if direction == "Long" else entry + atr
    target = entry + target_r*atr if direction == "Long" else entry - target_r*atr
    future = px.iloc[idx+1:min(idx+1+horizon, len(px))]
    for _, bar in future.iterrows():
        hi, lo = f(bar["High"]), f(bar["Low"])
        if not (np.isfinite(hi) and np.isfinite(lo)):
            continue
        if direction == "Long":
            st, ta = lo <= stop, hi >= target
        else:
            st, ta = hi >= stop, lo <= target
        if st and ta:
            return 0, "ambiguous"
        if ta:
            return 1, "target"
        if st:
            return 0, "stop"
    return 0, "timeout"


def main():
    cfg = load_config()
    universe = fetch_us_universe(ROOT/"data"/"universe.csv")
    syms = universe["YahooSymbol"].dropna().tolist()
    max_symbols = int(os.getenv("SCANNER_MAX_SYMBOLS", "500"))
    if len(syms) > max_symbols:
        last = len(syms)-1
        syms = [syms[round(j*last/(max_symbols-1))] for j in range(max_symbols)]

    period = os.getenv("SCANNER_PERIOD", "3y")
    prices = download_daily(syms, period=period, batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")), sleep_seconds=.75)
    spy = download_daily(["SPY"], period=period, batch_size=1, sleep_seconds=.1).get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY download failed")
    regime = classify_regime(spy)

    all_signals = []
    for ticker, px in prices.items():
        try:
            feat = add_features(px, spy=spy)
            reg = regime.reindex(feat.index).ffill().fillna("Neutral")
            for name, rule in RULES.items():
                rows = detect(feat, ticker, reg, cfg, name, rule)
                all_signals.extend(rows)
        except Exception as exc:
            print(f"SKIP {ticker}: {exc}")

    sig = pd.DataFrame(all_signals)
    if sig.empty:
        raise RuntimeError("No VCP sensitivity signals")
    sig["SignalDate"] = pd.to_datetime(sig["SignalDate"])

    train, valid, holdout = split_date(sig)
    rows = []
    for _, s in sig.iterrows():
        px = prices.get(str(s["Ticker"]))
        if px is None or px.empty:
            continue
        px = px.sort_index()
        try:
            idx = px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError:
            continue
        if isinstance(idx, slice) or idx+1 >= len(px):
            continue
        atr = f(add_features(px, spy=spy).loc[pd.Timestamp(s["SignalDate"]), "ATR20"]) if pd.Timestamp(s["SignalDate"]) in px.index else np.nan
        for h in HORIZONS:
            for target in TARGETS:
                o, reason = outcome(px, int(idx), str(s["Direction"]), atr, h, target)
                rows.append({**s.to_dict(),"HorizonDays":h,"TargetR":target,"Outcome":o,"Reason":reason})

    out = ROOT/"artifacts"/"vcp_sensitivity_v7"
    out.mkdir(parents=True, exist_ok=True)
    r = pd.DataFrame(rows)
    if r.empty:
        raise RuntimeError("No outcomes")

    r["Split"] = np.where(r["SignalDate"] < valid["SignalDate"].min(), "Train",
                    np.where(r["SignalDate"] < holdout["SignalDate"].min(), "Validation", "Holdout"))

    by_split = (r.groupby(["Rule","Direction","Split","HorizonDays","TargetR"], dropna=False)
        .agg(N=("Outcome","size"), SuccessRate=("Outcome","mean"))
        .reset_index())
    by_split["ExpectancyR"] = by_split["SuccessRate"]*(1+by_split["TargetR"]) - 1

    candidates = by_split[(by_split["Split"]=="Validation") & (by_split["HorizonDays"]==10) & (by_split["TargetR"]==1.0) & (by_split["N"]>=30)].copy()
    candidates = candidates.sort_values(["ExpectancyR","N"], ascending=[False,False])
    top = candidates.head(15).copy()

    # Holdout confirmation for exactly the discovered validation candidates.
    confirm = top[["Rule","Direction","HorizonDays","TargetR"]].merge(by_split[by_split["Split"]=="Holdout"],
        on=["Rule","Direction","HorizonDays","TargetR"], how="left", suffixes=("","_holdout"))

    meta = {
        "symbols_requested": len(syms),
        "symbols_downloaded": len(prices),
        "total_signals": int(len(sig)),
        "rules": len(RULES),
        "minimum_validation_n_for_candidate": 30,
        "discovery_metric": "Validation 1R/10d binary expectancy",
        "note": "Predefined VCP sensitivity rules only; validation discovers candidates, holdout confirms exact candidates. No production rule is selected automatically.",
    }

    sig.to_csv(out/"signals.csv", index=False)
    by_split.to_csv(out/"by_split.csv", index=False)
    top.to_csv(out/"top_validation_candidates.csv", index=False)
    confirm.to_csv(out/"candidate_holdout_confirmation.csv", index=False)
    (out/"summary.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    print("\nTOP VALIDATION CANDIDATES")
    print(top.to_string(index=False))
    print("\nHOLDOUT CONFIRMATION")
    print(confirm.to_string(index=False))


if __name__ == "__main__":
    main()
