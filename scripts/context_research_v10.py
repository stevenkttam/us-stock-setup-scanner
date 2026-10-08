from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from scanner.backtest import label_trade
from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features
from scanner.historical import build_historical_signals
from scanner.universe import fetch_us_universe

BASE_NUMERIC = [
    "SetupScore", "RVOL20", "VolumeAcceleration", "BaseDays", "BreakoutATR",
    "ATRCompression", "Trend20", "Trend50", "Trend200", "RS_SPY_20",
    "TR_ATR20", "CloseLocation", "BaseRangePct", "ATR20Pct", "RiskATR",
    "DollarVolume20Log", "GapPct",
]
BASE_CATEGORICAL = ["Setup", "MarketRegime", "Trend"]
CONTEXT_NUMERIC = [
    "BreadthAbove20Pct", "BreadthAbove50Pct", "BreadthAbove200Pct",
    "BreadthPositive5Pct", "BreadthPositive20Pct", "BreadthMedianReturn20",
    "BreadthDispersion20", "UpDownVolumeRatio",
    "SPYReturn1", "SPYReturn5", "SPYReturn20", "SPYATR20Pct", "SPYTR_ATR20",
    "QQQReturn5", "QQQReturn20", "IWMReturn20", "VIXLevel", "VIXPercentile252",
    "SectorPositive20Pct", "SectorDispersion20", "SectorMedianReturn20",
    "SectorMaxReturn20", "SectorMinReturn20", "SectorProxyCorr60",
    "SectorReturn20", "RS_Sector_20", "SectorStrengthRankPct",
]
ENRICHED_NUMERIC = BASE_NUMERIC + CONTEXT_NUMERIC
SECTOR_ETFS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLY", "XLU", "XLV", "XLC"]
BENCHMARKS = ["SPY", "QQQ", "IWM", "^VIX"] + SECTOR_ETFS
HOLDING_DAYS = 10
TARGET_R = 2.0
MIN_PRICE = 5.0
MIN_DV = 20_000_000
MIN_CORR_OBS = 40


def _f(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return np.nan


def original_v8_sample(symbols: list[str], n: int = 500) -> list[str]:
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) <= n:
        return symbols
    last = len(symbols) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [symbols[i] for i in idxs]


def fresh_v9_sample(symbols: list[str], n: int = 1000) -> list[str]:
    base = set(original_v8_sample(symbols, 500))
    remaining = [s for s in symbols if s not in base]
    if len(remaining) <= n:
        return remaining
    last = len(remaining) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [remaining[i] for i in idxs]


def fresh_v10_sample(symbols: list[str], n: int = 1000) -> list[str]:
    v8 = set(original_v8_sample(symbols, 500))
    v9 = set(fresh_v9_sample(symbols, 1000))
    remaining = [s for s in symbols if s not in v8 and s not in v9]
    if len(remaining) <= n:
        return remaining
    last = len(remaining) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [remaining[i] for i in idxs]


def build_breadth_context(feature_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for ticker, d in feature_map.items():
        close = pd.to_numeric(d["Close"], errors="coerce")
        volume = pd.to_numeric(d["Volume"], errors="coerce")
        r1 = close.pct_change(1, fill_method=None)
        r5 = close.pct_change(5, fill_method=None)
        r20 = close.pct_change(20, fill_method=None)
        active = close.ge(MIN_PRICE) & pd.to_numeric(d["DollarVolume20"], errors="coerce").ge(MIN_DV)
        x = pd.DataFrame({
            "Active": active.astype(float),
            "Above20": (active & (close > d["EMA20"])).astype(float),
            "Above50": (active & (close > d["EMA50"])).astype(float),
            "Above200": (active & (close > d["EMA200"])).astype(float),
            "Positive5": (active & (r5 > 0)).astype(float),
            "Positive20": (active & (r20 > 0)).astype(float),
            "Return20": r20.where(active),
            "UpVol": volume.where(active & (r1 > 0), 0.0),
            "DownVol": volume.where(active & (r1 < 0), 0.0),
        }, index=d.index)
        frames.append(x.reset_index(names="SignalDate"))
    if not frames:
        return pd.DataFrame()
    x = pd.concat(frames, ignore_index=True)
    g = x.groupby("SignalDate", observed=True)
    out = g.agg(
        ActiveCount=("Active", "sum"),
        BreadthAbove20=("Above20", "sum"),
        BreadthAbove50=("Above50", "sum"),
        BreadthAbove200=("Above200", "sum"),
        BreadthPositive5=("Positive5", "sum"),
        BreadthPositive20=("Positive20", "sum"),
        BreadthMedianReturn20=("Return20", "median"),
        BreadthDispersion20=("Return20", "std"),
        UpVolume=("UpVol", "sum"),
        DownVolume=("DownVol", "sum"),
    ).reset_index()
    denom = out["ActiveCount"].replace(0, np.nan)
    for dst, src in [
        ("BreadthAbove20Pct", "BreadthAbove20"),
        ("BreadthAbove50Pct", "BreadthAbove50"),
        ("BreadthAbove200Pct", "BreadthAbove200"),
        ("BreadthPositive5Pct", "BreadthPositive5"),
        ("BreadthPositive20Pct", "BreadthPositive20"),
    ]:
        out[dst] = out[src] / denom
    out["UpDownVolumeRatio"] = out["UpVolume"] / out["DownVolume"].replace(0, np.nan)
    return out.set_index("SignalDate")[
        CONTEXT_NUMERIC[:8]
    ].sort_index()


def build_benchmark_context(benchmark_map: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    def close_series(ticker: str) -> pd.Series:
        d = benchmark_map.get(ticker)
        if d is None or d.empty:
            return pd.Series(dtype=float)
        return pd.to_numeric(d["Close"], errors="coerce").sort_index()

    spy = close_series("SPY")
    qqq = close_series("QQQ")
    iwm = close_series("IWM")
    if spy.empty or qqq.empty or iwm.empty:
        raise RuntimeError("SPY/QQQ/IWM benchmark data missing")

    spyf = add_features(benchmark_map["SPY"])
    ctx = pd.DataFrame(index=spy.index.union(qqq.index).union(iwm.index).sort_values())
    ctx["SPYReturn1"] = spy.pct_change(1, fill_method=None)
    ctx["SPYReturn5"] = spy.pct_change(5, fill_method=None)
    ctx["SPYReturn20"] = spy.pct_change(20, fill_method=None)
    ctx["SPYATR20Pct"] = pd.to_numeric(spyf["ATR20"], errors="coerce") / spy.replace(0, np.nan)
    ctx["SPYTR_ATR20"] = pd.to_numeric(spyf["TR_ATR20"], errors="coerce")
    ctx["QQQReturn5"] = qqq.pct_change(5, fill_method=None)
    ctx["QQQReturn20"] = qqq.pct_change(20, fill_method=None)
    ctx["IWMReturn20"] = iwm.pct_change(20, fill_method=None)

    vix = close_series("^VIX")
    ctx["VIXLevel"] = vix
    ctx["VIXPercentile252"] = vix.rolling(252, min_periods=60).apply(
        lambda a: float((a <= a[-1]).mean()), raw=True
    ) if not vix.empty else np.nan

    sectors = {etf: close_series(etf) for etf in SECTOR_ETFS}
    sectors = {k: v for k, v in sectors.items() if not v.empty}
    sector_close = pd.DataFrame(sectors).sort_index()
    sec20 = sector_close.pct_change(20, fill_method=None)
    ctx["SectorPositive20Pct"] = (sec20 > 0).mean(axis=1, skipna=True)
    ctx["SectorDispersion20"] = sec20.std(axis=1, skipna=True)
    ctx["SectorMedianReturn20"] = sec20.median(axis=1, skipna=True)
    ctx["SectorMaxReturn20"] = sec20.max(axis=1, skipna=True)
    ctx["SectorMinReturn20"] = sec20.min(axis=1, skipna=True)
    return ctx.sort_index(), sector_close.sort_index()


def infer_sector_context(px: pd.DataFrame, dates: list[pd.Timestamp], sector_close: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(px["Close"], errors="coerce").sort_index()
    stock_ret = close.pct_change(1, fill_method=None)
    stock_ret20 = close.pct_change(20, fill_method=None)
    sector_ret = sector_close.pct_change(1, fill_method=None)
    sector_ret20 = sector_close.pct_change(20, fill_method=None)
    rows = []
    for date in dates:
        date = pd.Timestamp(date)
        if date not in stock_ret.index:
            continue
        window = stock_ret.loc[:date].dropna().tail(60)
        if len(window) < MIN_CORR_OBS:
            continue
        secw = sector_ret.reindex(window.index).dropna(axis=0, how="any")
        common = secw.index.intersection(window.index)
        if len(common) < MIN_CORR_OBS:
            continue
        x = window.reindex(common).to_numpy(dtype=float)
        y = secw.reindex(common).to_numpy(dtype=float)
        xs = x.std(ddof=1)
        ys = y.std(axis=0, ddof=1)
        valid = np.isfinite(ys) & (ys > 0)
        if not (np.isfinite(xs) and xs > 0 and np.any(valid)):
            continue
        xz = (x - x.mean()) / xs
        yz = (y - y.mean(axis=0)) / ys
        corr = (yz * xz[:, None]).sum(axis=0) / (len(common) - 1)
        corr = np.where(valid & np.isfinite(corr), corr, -np.inf)
        j = int(np.argmax(corr))
        proxy = sector_close.columns[j]
        best_corr = float(corr[j])
        sec20_val = _f(sector_ret20.get(proxy, pd.Series(dtype=float)).get(date, np.nan))
        stock20_val = _f(stock_ret20.get(date, np.nan))
        row = {
            "SignalDate": date,
            "SectorProxy": proxy,
            "SectorProxyCorr60": best_corr,
            "SectorReturn20": sec20_val,
            "RS_Sector_20": stock20_val - sec20_val if np.isfinite(stock20_val) and np.isfinite(sec20_val) else np.nan,
        }
        snap = sector_ret20.loc[date] if date in sector_ret20.index else pd.Series(dtype=float)
        vals = snap.dropna().sort_values()
        row["SectorStrengthRankPct"] = float((vals <= sec20_val).mean()) if np.isfinite(sec20_val) and len(vals) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def add_outcomes(signals: pd.DataFrame, prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for _, s in signals.iterrows():
        px = prices.get(str(s["Ticker"]))
        if px is None or px.empty:
            continue
        px = px.sort_index()
        try:
            idx = px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError:
            continue
        if isinstance(idx, slice) or int(idx) + 1 >= len(px):
            continue
        idx = int(idx)
        signal_close, risk = _f(s["Entry"]), _f(s["RiskPerShare"])
        next_open = _f(px.iloc[idx + 1]["Open"])
        if not (np.isfinite(signal_close) and np.isfinite(risk) and risk > 0 and np.isfinite(next_open)):
            continue
        if s["Direction"] == "Long":
            stop, target = next_open - risk, next_open + TARGET_R * risk
        else:
            stop, target = next_open + risk, next_open - TARGET_R * risk
        outcome = label_trade(px, idx, s["Direction"], next_open, stop, target, HOLDING_DAYS)
        gap_pct = next_open / signal_close - 1.0
        signed_gap_r = (next_open - signal_close) / risk if s["Direction"] == "Long" else (signal_close - next_open) / risk
        rows.append({
            **s.to_dict(),
            "EntryActual": next_open,
            "StopActual": stop,
            "TargetActual": target,
            "Outcome": int(outcome),
            "NextOpenGapPct": gap_pct,
            "NextOpenSignedGapR": signed_gap_r,
            "NextOpenAdverseGapR": max(0.0, -signed_gap_r),
        })
    return pd.DataFrame(rows)


def split_by_date(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    d = d.sort_values("SignalDate")
    dates = pd.Series(d["SignalDate"].drop_duplicates().sort_values().tolist())
    if len(dates) < 80:
        raise ValueError("Not enough unique dates for time split")
    train_end = pd.Timestamp(dates.iloc[int(len(dates) * 0.60)])
    valid_end = pd.Timestamp(dates.iloc[int(len(dates) * 0.75)])
    return (
        d[d["SignalDate"] < train_end].copy(),
        d[(d["SignalDate"] >= train_end) & (d["SignalDate"] < valid_end)].copy(),
        d[d["SignalDate"] >= valid_end].copy(),
    )


def metrics(y: pd.Series, p: np.ndarray) -> dict[str, float]:
    yy, pp = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    order = np.argsort(-pp)
    def top(frac: float) -> tuple[float, float]:
        n = max(1, int(len(pp) * frac))
        sr = float(yy[order[:n]].mean())
        return sr, 3.0 * sr - 1.0
    t5, e5 = top(0.05)
    t10, e10 = top(0.10)
    t20, e20 = top(0.20)
    t30, e30 = top(0.30)
    return {
        "n": int(len(yy)),
        "success_rate": float(yy.mean()),
        "brier": float(brier_score_loss(yy, pp)),
        "auc": float(roc_auc_score(yy, pp)) if np.unique(yy).size == 2 else np.nan,
        "top5_success_rate": t5, "top5_expectancy_R": e5,
        "top10_success_rate": t10, "top10_expectancy_R": e10,
        "top20_success_rate": t20, "top20_expectancy_R": e20,
        "top30_success_rate": t30, "top30_expectancy_R": e30,
    }


def calibration(y: pd.Series, p: np.ndarray) -> pd.DataFrame:
    x = pd.DataFrame({"Outcome": np.asarray(y, dtype=int), "Probability": np.asarray(p, dtype=float)})
    x["Decile"] = pd.qcut(x["Probability"].rank(method="first"), q=10, labels=False)
    g = x.groupby("Decile", observed=True).agg(
        N=("Outcome", "size"),
        PredictedProbability=("Probability", "mean"),
        ObservedSuccessRate=("Outcome", "mean"),
    ).reset_index()
    g["CalibrationError"] = g["ObservedSuccessRate"] - g["PredictedProbability"]
    return g


def build_model(numeric: list[str], categorical: list[str], kind: str):
    prep = ColumnTransformer([
        ("num", StandardScaler() if kind == "Logistic" else "passthrough", numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical),
    ], remainder="drop", sparse_threshold=0.0)
    if kind == "Logistic":
        base = Pipeline([
            ("prep", prep),
            ("clf", LogisticRegression(max_iter=3000, class_weight="balanced")),
        ])
    else:
        base = Pipeline([
            ("prep", prep),
            ("clf", HistGradientBoostingClassifier(
                learning_rate=0.05, max_iter=250, max_leaf_nodes=15,
                min_samples_leaf=60, l2_regularization=1.0,
                class_weight="balanced", random_state=42,
            )),
        ])
    return CalibratedClassifierCV(base, method="isotonic", cv=TimeSeriesSplit(n_splits=5))


def evaluate_direction(d: pd.DataFrame, direction: str, out_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    d = d[d["Direction"] == direction].copy().sort_values("SignalDate")
    if len(d) < 1000:
        raise ValueError(f"{direction} has too few clean rows: {len(d)}")
    train, valid, holdout = split_by_date(d)
    records = []
    for feature_set, numeric in [
        ("Baseline V2", BASE_NUMERIC),
        ("Context V2", ENRICHED_NUMERIC),
    ]:
        for kind in ["Logistic", "HistGradientBoosting"]:
            m = build_model(numeric, BASE_CATEGORICAL, kind)
            m.fit(train[numeric + BASE_CATEGORICAL], train["Outcome"])
            pv = m.predict_proba(valid[numeric + BASE_CATEGORICAL])[:, 1]
            records.append({
                "Direction": direction, "FeatureSet": feature_set, "Model": kind,
                "Partition": "Validation", **metrics(valid["Outcome"], pv)
            })

    context_valid = [r for r in records if r["FeatureSet"] == "Context V2"]
    selected = max(context_valid, key=lambda r: (r["top10_expectancy_R"], -r["brier"]))
    selected_kind = selected["Model"]

    holdout_records = []
    for feature_set, numeric in [
        ("Baseline V2", BASE_NUMERIC),
        ("Context V2", ENRICHED_NUMERIC),
    ]:
        for kind in ["Logistic", "HistGradientBoosting"]:
            m = build_model(numeric, BASE_CATEGORICAL, kind)
            dev = pd.concat([train, valid], ignore_index=True)
            m.fit(dev[numeric + BASE_CATEGORICAL], dev["Outcome"])
            ph = m.predict_proba(holdout[numeric + BASE_CATEGORICAL])[:, 1]
            hm = metrics(holdout["Outcome"], ph)
            holdout_records.append({
                "Direction": direction, "FeatureSet": feature_set, "Model": kind,
                "Partition": "Holdout", **hm
            })
            if feature_set == "Context V2" and kind == selected_kind:
                calibration(holdout["Outcome"], ph).to_csv(
                    out_dir / f"{direction.lower()}_selected_holdout_calibration.csv", index=False
                )

    all_records = pd.DataFrame(records + holdout_records)
    base_hold = all_records[(all_records.Partition == "Holdout") & (all_records.FeatureSet == "Baseline V2")]
    base_best = base_hold.sort_values(["top10_expectancy_R", "brier"], ascending=[False, True]).iloc[0]
    sel_hold = all_records[
        (all_records.Partition == "Holdout")
        & (all_records.FeatureSet == "Context V2")
        & (all_records.Model == selected_kind)
    ].iloc[0]
    meta = {
        "selected_context_model_on_validation": selected_kind,
        "train_n": int(len(train)), "validation_n": int(len(valid)), "holdout_n": int(len(holdout)),
        "train_start": str(train["SignalDate"].min().date()), "train_end": str(train["SignalDate"].max().date()),
        "validation_start": str(valid["SignalDate"].min().date()), "validation_end": str(valid["SignalDate"].max().date()),
        "holdout_start": str(holdout["SignalDate"].min().date()), "holdout_end": str(holdout["SignalDate"].max().date()),
        "holdout_selected_context": sel_hold.to_dict(),
        "holdout_best_baseline": base_best.to_dict(),
        "holdout_delta": {
            "auc": float(sel_hold["auc"] - base_best["auc"]),
            "brier": float(sel_hold["brier"] - base_best["brier"]),
            "top10_expectancy_R": float(sel_hold["top10_expectancy_R"] - base_best["top10_expectancy_R"]),
            "top20_expectancy_R": float(sel_hold["top20_expectancy_R"] - base_best["top20_expectancy_R"]),
        },
    }
    return all_records, meta


def gap_diagnostics(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for direction in ["Long", "Short"]:
        x = d[d["Direction"] == direction]
        base_sr = float(x["Outcome"].mean())
        rows.append({
            "Direction": direction, "Rule": "No guard", "N": int(len(x)),
            "SuccessRate": base_sr, "ExpectancyR": 3.0 * base_sr - 1.0,
        })
        for guard in [0.25, 0.50, 0.75, 1.00, 1.50]:
            kept = x[x["NextOpenAdverseGapR"] <= guard]
            if kept.empty:
                continue
            sr = float(kept["Outcome"].mean())
            rows.append({
                "Direction": direction, "Rule": f"Exclude adverse gap > {guard:.2f}R",
                "N": int(len(kept)), "SuccessRate": sr, "ExpectancyR": 3.0 * sr - 1.0,
                "ExcludedN": int(len(x) - len(kept)),
            })
    return pd.DataFrame(rows)


def main() -> None:
    cfg = load_config()
    data_dir = ROOT / "data"
    out_dir = ROOT / "artifacts" / "context_v10"
    out_dir.mkdir(parents=True, exist_ok=True)

    universe = fetch_us_universe(data_dir / "universe.csv")
    all_symbols = universe["YahooSymbol"].dropna().astype(str).tolist()
    n = int(os.getenv("SCANNER_MAX_SYMBOLS", "1000"))
    symbols = fresh_v10_sample(all_symbols, n)
    period = os.getenv("SCANNER_PERIOD", "10y")

    prices = download_daily(
        symbols, period=period,
        batch_size=int(os.getenv("SCANNER_BATCH_SIZE", "50")),
        sleep_seconds=0.75,
    )
    benchmark_map = download_daily(
        BENCHMARKS, period=period, batch_size=25, sleep_seconds=0.2
    )
    spy = benchmark_map.get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY download failed")

    feature_map = {}
    for ticker, px in prices.items():
        try:
            feature_map[ticker] = add_features(px, spy=spy)
        except Exception as exc:
            print(f"FEATURE SKIP {ticker}: {exc}", flush=True)
    if not feature_map:
        raise RuntimeError("No stock features built")

    signals = build_historical_signals({k: prices[k] for k in feature_map}, spy, cfg)
    if signals.empty:
        raise RuntimeError("No signals generated")
    signals["SignalDate"] = pd.to_datetime(signals["SignalDate"], errors="coerce")

    breadth = build_breadth_context(feature_map)
    benchmark_ctx, sector_close = build_benchmark_context(benchmark_map)
    signals = signals.merge(breadth, left_on="SignalDate", right_index=True, how="left")
    signals = signals.merge(benchmark_ctx, left_on="SignalDate", right_index=True, how="left")

    sector_rows = []
    for ticker, g in signals.groupby("Ticker", sort=False):
        if sector_close.empty:
            continue
        x = infer_sector_context(prices[str(ticker)], g["SignalDate"].drop_duplicates().tolist(), sector_close)
        if not x.empty:
            x["Ticker"] = ticker
            sector_rows.append(x)
        print(f"SECTOR {ticker}: {len(g)} signals", flush=True)
    for c in ["SectorProxy", "SectorProxyCorr60", "SectorReturn20", "RS_Sector_20", "SectorStrengthRankPct"]:
        if c not in signals.columns:
            signals[c] = np.nan
    if sector_rows:
        sector_ctx = pd.concat(sector_rows, ignore_index=True)
        signals = signals.merge(
            sector_ctx,
            on=["Ticker", "SignalDate"],
            how="left",
            suffixes=("", "_sector"),
        )
        # The base signal frame already contains placeholder sector columns.
        # Coalesce the enriched values into the canonical feature names rather
        # than leaving the originals as all-NaN after the merge.
        for c in ["SectorProxy", "SectorProxyCorr60", "SectorReturn20", "RS_Sector_20", "SectorStrengthRankPct"]:
            suffixed = f"{c}_sector"
            if suffixed in signals.columns:
                signals[c] = signals[c].combine_first(signals[suffixed])
                signals = signals.drop(columns=[suffixed])

    data = add_outcomes(signals, {k: prices[k] for k in feature_map})
    if data.empty:
        raise RuntimeError("No outcome rows")
    data["DollarVolume20Log"] = np.log1p(pd.to_numeric(data["DollarVolume20"], errors="coerce").clip(lower=0))
    data = data.replace([np.inf, -np.inf], np.nan)

    required = ENRICHED_NUMERIC + BASE_CATEGORICAL + ["Outcome", "SignalDate"]
    clean = data.dropna(subset=required).copy()
    clean["Outcome"] = clean["Outcome"].astype(int)
    if clean.empty:
        raise RuntimeError("No clean enriched rows")

    comps, metas = [], {}
    for direction in ["Long", "Short"]:
        comp, meta = evaluate_direction(clean, direction, out_dir)
        comps.append(comp)
        metas[direction] = meta

    comparison = pd.concat(comps, ignore_index=True)
    comparison.to_csv(out_dir / "model_comparison.csv", index=False)
    gap_diagnostics(clean).to_csv(out_dir / "next_open_gap_diagnostics.csv", index=False)
    clean.to_parquet(out_dir / "context_training_rows.parquet", index=False)

    summary = {
        "research_version": "V2 Context Enrichment / V10",
        "universe_requested": int(len(symbols)),
        "symbols_downloaded": int(len(feature_map)),
        "signals_generated": int(len(signals)),
        "clean_enriched_observations": int(len(clean)),
        "primary_trade_definition": "+2R before -1R within 10 sessions; next-session open entry; same-bar target/stop ambiguity = failure",
        "context_groups": [
            "Research-sample breadth: above 20/50/200-day averages, positive 5/20-day breadth, return dispersion, up/down volume",
            "Market context: SPY/QQQ/IWM returns and SPY volatility",
            "VIX level and 252-day percentile",
            "Sector ETF breadth/dispersion plus inferred stock sector proxy from 60-day return correlation",
            "Next-open gap is execution diagnostics only and is not used in the daily pre-close model",
        ],
        "sample_partition": "Fresh 1000-stock partition after the deterministic V8 500 and V9 1000 samples",
        "model_selection": "Within Context V2, select Logistic vs HistGradientBoosting on chronological validation by top-10 expectancy then Brier; untouched holdout is used once for final comparison",
        "note": "Research only. Current-universe survivorship bias remains. Breadth uses the fresh research sample rather than the full US market. Sector proxy is inferred, not a fundamental classification.",
        "directions": metas,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print("\nMODEL COMPARISON")
    print(comparison.to_string(index=False))
    print("\nNEXT-OPEN GAP DIAGNOSTICS")
    print(gap_diagnostics(clean).to_string(index=False))


if __name__ == "__main__":
    main()
