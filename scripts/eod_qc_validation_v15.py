from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.scan import score_candidate
from scanner.universe import fetch_us_universe


MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20_000_000.0
HORIZONS = (1, 3, 5, 10)
TOP_KS = (10, 40)
PRIMARY_HORIZON = 5
PRIMARY_TOP_K = 10
BOOTSTRAP_REPS = 2500

# Explicit assumptions, not estimates of any specific broker's actual costs.
COST_SCENARIOS = {
    "Base": {"round_trip_cost_bps": 10.0, "annual_short_borrow_rate": 0.03},
    "Stress": {"round_trip_cost_bps": 25.0, "annual_short_borrow_rate": 0.10},
}


def evenly_spaced_sample(symbols, n):
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) <= n:
        return symbols
    last = len(symbols) - 1
    return [symbols[round(i * last / (n - 1))] for i in range(n)]


def sample_v8(symbols):
    return set(evenly_spaced_sample(symbols, 500))


def sample_v9(symbols):
    a = sample_v8(symbols)
    return set(evenly_spaced_sample([x for x in symbols if x not in a], 1000))


def sample_v10(symbols):
    a = sample_v8(symbols) | sample_v9(symbols)
    return set(evenly_spaced_sample([x for x in symbols if x not in a], 1000))


def sample_v12(symbols):
    a = sample_v8(symbols) | sample_v9(symbols) | sample_v10(symbols)
    return set(evenly_spaced_sample([x for x in symbols if x not in a], 1000))


def sample_v13(symbols):
    a = sample_v8(symbols) | sample_v9(symbols) | sample_v10(symbols) | sample_v12(symbols)
    return set(evenly_spaced_sample([x for x in symbols if x not in a], 1000))


def sample_v14(symbols, n=1000):
    used = sample_v8(symbols) | sample_v9(symbols) | sample_v10(symbols) | sample_v12(symbols) | sample_v13(symbols)
    return evenly_spaced_sample([x for x in symbols if x not in used], n)


def numeric(row, key):
    try:
        value = float(row.get(key))
        return value if np.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def quality_control(row):
    """Same fixed QC thresholds as the live scan; flags do not alter SetupScore."""
    flags = []
    rvol = numeric(row, "RVOL20")
    acceleration = numeric(row, "VolumeAcceleration")
    trigger_atr = numeric(row, "BreakoutATR")
    atr_pct = numeric(row, "ATR20Pct")
    risk_atr = numeric(row, "RiskATR")
    direction = str(row.get("Direction", ""))
    regime = str(row.get("MarketRegime", ""))

    if np.isfinite(acceleration) and acceleration >= 10:
        flags.append("EXTREME VOLUME ACCELERATION (>=10x prior day)")
    elif np.isfinite(acceleration) and acceleration >= 5:
        flags.append("HIGH VOLUME ACCELERATION (>=5x prior day)")
    if np.isfinite(rvol) and rvol >= 10:
        flags.append("EXTREME RVOL (>=10x 20-day average)")
    if np.isfinite(trigger_atr) and trigger_atr >= 4:
        flags.append("EXTENDED TRIGGER (>=4 ATR)")
    if np.isfinite(atr_pct) and atr_pct >= 0.08:
        flags.append("HIGH VOLATILITY (ATR20 >=8% of price)")
    if np.isfinite(risk_atr) and risk_atr < 0.5:
        flags.append("TIGHT REFERENCE STOP (<0.5 ATR)")

    countertrend = (
        (regime.startswith("Bullish") and direction == "Short")
        or (regime.startswith("Bearish") and direction == "Long")
    )
    if countertrend:
        flags.append("COUNTERTREND VS SPY REGIME")

    unusual_volume = (
        (np.isfinite(acceleration) and acceleration >= 10)
        or (np.isfinite(rvol) and rvol >= 10)
    )
    risk_warning = (
        (np.isfinite(trigger_atr) and trigger_atr >= 4)
        or (np.isfinite(atr_pct) and atr_pct >= 0.08)
        or (np.isfinite(risk_atr) and risk_atr < 0.5)
    )

    if unusual_volume:
        status = "CHECK — unusual volume; verify raw data"
    elif risk_warning:
        status = "CHECK — extension / volatility / stop distance"
    elif countertrend:
        status = "COUNTERTREND — verify market context"
    else:
        status = "STANDARD REVIEW — still research only"

    return {
        "QualityFlags": "; ".join(flags) if flags else "No automatic QC flags",
        "QualityStatus": status,
        "MarketContext": "Countertrend vs SPY" if countertrend else "Aligned / neutral vs SPY",
        "QCFlagged": bool(flags),
        "QCUnusualVolume": bool(unusual_volume),
        "QCRiskWarning": bool(risk_warning),
        "QCCountertrend": bool(countertrend),
    }


def make_feature_frame(px, spy, regime_series, ticker):
    f = add_features(px, spy=spy).copy().sort_index()
    close = pd.to_numeric(f["Close"], errors="coerce")
    open_ = pd.to_numeric(f["Open"], errors="coerce")
    f["Ticker"] = ticker
    f["SignalDate"] = pd.to_datetime(f.index)
    f["HistoricalRegime"] = regime_series.reindex(f.index).ffill()

    spy_close = pd.to_numeric(spy["Close"], errors="coerce").sort_index()
    spy_open = pd.to_numeric(spy["Open"], errors="coerce").sort_index()

    for horizon in HORIZONS:
        # EOD signal date; hypothetical entry at next session open; exit at close
        # of the h-th session after the signal. Matched SPY window for relative return.
        f[f"ForwardRet{horizon}"] = close.shift(-horizon) / open_.shift(-1) - 1.0
        spy_forward = (spy_close.shift(-horizon) / spy_open.shift(-1) - 1.0).reindex(f.index)
        f[f"ForwardExcess{horizon}"] = f[f"ForwardRet{horizon}"] - spy_forward
        f[f"ForwardExitDate{horizon}"] = pd.Series(f.index, index=f.index).shift(-horizon)

    return f.replace([np.inf, -np.inf], np.nan)


def generate_historical_candidates(prices, spy, cfg):
    regime_series = classify_regime(spy).sort_index()
    lengths = cfg["signal"]["consolidation_lengths"]
    frames = []
    total = len(prices)

    for ticker_i, (ticker, px) in enumerate(prices.items(), 1):
        try:
            f = make_feature_frame(px, spy, regime_series, ticker)
            # Eligibility and data-quality prefilters reduce work, without using future outcomes.
            eligible = (
                f["Close"].ge(MIN_PRICE)
                & f["DollarVolume20"].ge(MIN_DOLLAR_VOLUME)
                & f["RVOL20"].ge(float(cfg["signal"].get("rvol20_min", 1.20)))
                & f["CloseLocation"].notna()
                & f["ATR20"].gt(0)
                & f["HistoricalRegime"].notna()
            )
            candidate_days = f.loc[eligible]
            ticker_rows = []

            for _, row in candidate_days.iterrows():
                date = pd.Timestamp(row["SignalDate"])
                for direction in ("Long", "Short"):
                    possible = []
                    regime = str(row["HistoricalRegime"])
                    for n in lengths:
                        result = score_candidate(row, direction, int(n), regime, cfg["signal"])
                        if result is not None:
                            possible.append(result)
                    if not possible:
                        continue

                    best = max(possible, key=lambda x: x["SetupScore"])
                    record = dict(best)
                    record["SignalDate"] = date
                    record["Ticker"] = ticker
                    record["SignalClose"] = record.pop("Entry", np.nan)
                    for horizon in HORIZONS:
                        record[f"ForwardRet{horizon}"] = numeric(row, f"ForwardRet{horizon}")
                        record[f"ForwardExcess{horizon}"] = numeric(row, f"ForwardExcess{horizon}")
                        record[f"ForwardExitDate{horizon}"] = row.get(f"ForwardExitDate{horizon}")
                    record.update(quality_control(record))
                    ticker_rows.append(record)

            if ticker_rows:
                frames.append(pd.DataFrame(ticker_rows))
        except Exception as exc:
            print(f"FEATURE/CANDIDATE SKIP {ticker}: {type(exc).__name__}: {exc}", flush=True)

        if ticker_i % 50 == 0 or ticker_i == total:
            print(f"HISTORICAL CANDIDATES: processed {ticker_i}/{total} tickers; signals={sum(len(x) for x in frames):,}", flush=True)

    if not frames:
        raise RuntimeError("No historical candidates generated.")
    candidates = pd.concat(frames, ignore_index=True)
    candidates = candidates.sort_values(["SignalDate", "Direction", "SetupScore"], ascending=[True, True, False])
    return candidates.reset_index(drop=True)


def assign_partition(candidates, train_cut, holdout_cut):
    out = candidates.copy()
    out["Partition"] = np.select(
        [
            out["SignalDate"] < train_cut,
            out["SignalDate"] < holdout_cut,
        ],
        ["Train", "Validation"],
        default="Holdout",
    )
    return out


def block_bootstrap_ci(values, reps=BOOTSTRAP_REPS, block=5, seed=42):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return (float(x.mean()) if n else np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    estimates = np.empty(reps)
    starts = np.arange(n)
    blocks_needed = int(np.ceil(n / block))
    for i in range(reps):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([x[(start + np.arange(block)) % n] for start in chosen])[:n]
        estimates[i] = sample.mean()
    return float(x.mean()), float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def build_daily_portfolios(candidates, train_cut, holdout_cut, first_date, last_date):
    rows = []

    for horizon in HORIZONS:
        ret_col = f"ForwardRet{horizon}"
        excess_col = f"ForwardExcess{horizon}"
        exit_col = f"ForwardExitDate{horizon}"
        valid = candidates.dropna(subset=[ret_col, excess_col, exit_col]).copy()

        for (partition, date, direction), g in valid.groupby(["Partition", "SignalDate", "Direction"], sort=True):
            # Exclude any trade whose exit date crosses a chronological partition.
            exit_dates = pd.to_datetime(g[exit_col])
            if partition == "Train":
                g = g[exit_dates < train_cut]
            elif partition == "Validation":
                g = g[(exit_dates >= train_cut) & (exit_dates < holdout_cut)]
            else:
                g = g[(exit_dates >= holdout_cut) & (exit_dates <= last_date)]
            if g.empty:
                continue
            g = g.sort_values("SetupScore", ascending=False)

            group_frames = {
                "All": g,
                "QC Clear": g[~g["QCFlagged"]],
                "QC Flagged": g[g["QCFlagged"]],
                "Unusual Volume": g[g["QCUnusualVolume"]],
                "No Unusual Volume": g[~g["QCUnusualVolume"]],
            }

            for group_name, subset in group_frames.items():
                if subset.empty:
                    continue
                for top_k in TOP_KS:
                    selected = subset.head(top_k)
                    n_trades = len(selected)
                    sign = 1.0 if direction == "Long" else -1.0
                    stock_return = selected[ret_col].astype(float).to_numpy()
                    excess_return = selected[excess_col].astype(float).to_numpy()
                    gross = sign * stock_return
                    directional_excess = sign * excess_return

                    for scenario, costs in COST_SCENARIOS.items():
                        round_trip_cost = costs["round_trip_cost_bps"] / 10_000.0
                        borrow_cost = (costs["annual_short_borrow_rate"] * horizon / 252.0) if direction == "Short" else 0.0
                        net = gross - round_trip_cost - borrow_cost
                        rows.append({
                            "SignalDate": pd.Timestamp(date),
                            "Partition": partition,
                            "Direction": direction,
                            "QCGroup": group_name,
                            "TopK": int(top_k),
                            "Horizon": int(horizon),
                            "CostScenario": scenario,
                            "NTrades": int(n_trades),
                            "MeanGrossDirectionalPnL": float(np.mean(gross)),
                            "MeanDirectionalExcessVsSPY": float(np.mean(directional_excess)),
                            "MeanNetPnL": float(np.mean(net)),
                            "GrossHitRate": float(np.mean(gross > 0)),
                            "NetHitRate": float(np.mean(net > 0)),
                        })

        print(f"PORTFOLIO METRICS: finished {horizon}-session horizon", flush=True)

    return pd.DataFrame(rows)


def summarize_portfolios(daily):
    keys = ["Partition", "Direction", "QCGroup", "TopK", "Horizon", "CostScenario"]
    rows = []
    for key, g in daily.groupby(keys, sort=True):
        gross_mean, gross_lo, gross_hi = block_bootstrap_ci(g["MeanGrossDirectionalPnL"], seed=100 + int(key[4]))
        net_mean, net_lo, net_hi = block_bootstrap_ci(g["MeanNetPnL"], seed=200 + int(key[4]))
        rows.append({
            **dict(zip(keys, key)),
            "ActiveDates": int(g["SignalDate"].nunique()),
            "TotalSelectedTrades": int(g["NTrades"].sum()),
            "MeanDailyGrossDirectionalPnL": gross_mean,
            "GrossCI95Low": gross_lo,
            "GrossCI95High": gross_hi,
            "MeanDailyNetPnL": net_mean,
            "NetCI95Low": net_lo,
            "NetCI95High": net_hi,
            "GrossHitRate": float(g["GrossHitRate"].mean()),
            "NetHitRate": float(g["NetHitRate"].mean()),
        })
    return pd.DataFrame(rows)


def compare_qc_to_all(daily):
    primary = daily[
        daily["Partition"].eq("Holdout")
        & daily["TopK"].eq(PRIMARY_TOP_K)
        & daily["Horizon"].eq(PRIMARY_HORIZON)
    ].copy()
    rows = []
    for direction in ("Long", "Short"):
        for scenario in COST_SCENARIOS:
            base = primary[
                primary["Direction"].eq(direction)
                & primary["CostScenario"].eq(scenario)
                & primary["QCGroup"].eq("All")
            ][["SignalDate", "MeanNetPnL"]].rename(columns={"MeanNetPnL": "AllNetPnL"})
            for group_name in ("QC Clear", "QC Flagged"):
                grp = primary[
                    primary["Direction"].eq(direction)
                    & primary["CostScenario"].eq(scenario)
                    & primary["QCGroup"].eq(group_name)
                ][["SignalDate", "MeanNetPnL"]].rename(columns={"MeanNetPnL": "GroupNetPnL"})
                paired = base.merge(grp, on="SignalDate", how="inner")
                diff, low, high = block_bootstrap_ci(
                    paired["GroupNetPnL"] - paired["AllNetPnL"],
                    seed=900 + (0 if direction == "Long" else 10) + (0 if scenario == "Base" else 100) + (0 if group_name == "QC Clear" else 1000),
                )
                rows.append({
                    "Direction": direction,
                    "CostScenario": scenario,
                    "Comparison": f"{group_name} minus All",
                    "TopK": PRIMARY_TOP_K,
                    "Horizon": PRIMARY_HORIZON,
                    "PairedActiveDates": int(len(paired)),
                    "AllHoldoutActiveDates": int(len(base)),
                    "GroupHoldoutActiveDates": int(len(grp)),
                    "CoverageOfAllDates": float(len(paired) / len(base)) if len(base) else np.nan,
                    "MeanDailyNetPnLDifference": diff,
                    "DiffCI95Low": low,
                    "DiffCI95High": high,
                    "ImprovementSupported": bool(np.isfinite(low) and low > 0),
                })
    return pd.DataFrame(rows)


def main():
    data_dir = ROOT / "data"
    out_dir = ROOT / "artifacts" / "eod_qc_validation_v15"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    universe = fetch_us_universe(data_dir / "universe.csv", refresh=True)
    symbols_all = universe["YahooSymbol"].dropna().astype(str).tolist()
    requested = int(os.getenv("SCANNER_MAX_SYMBOLS", "1000"))
    symbols = sample_v14(symbols_all, requested)
    period = os.getenv("SCANNER_PERIOD", "10y")
    batch_size = int(os.getenv("SCANNER_BATCH_SIZE", "50"))

    print(f"Selected V14 fresh ticker partition for QC validation: {len(symbols)} symbols", flush=True)
    print("Fixed QC thresholds are taken from the live scanner; no threshold optimization will be performed.", flush=True)
    prices = download_daily(symbols, period=period, batch_size=batch_size, sleep_seconds=0.75)
    print(f"Downloaded {len(prices)} / {len(symbols)} symbols", flush=True)

    spy_map = download_daily(["SPY"], period=period, batch_size=1, sleep_seconds=0.25)
    spy = spy_map.get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY benchmark download failed; refusing to report QC performance.")

    candidates = generate_historical_candidates(prices, spy, cfg)
    candidates = candidates.sort_values(["SignalDate", "Direction", "SetupScore"], ascending=[True, True, False])
    market_dates = pd.DatetimeIndex(pd.to_datetime(spy.index)).sort_values().unique()
    if len(market_dates) < 500:
        raise RuntimeError(f"Not enough market dates for chronological split: {len(market_dates)}")
    train_cut = pd.Timestamp(market_dates[int(len(market_dates) * 0.60)])
    holdout_cut = pd.Timestamp(market_dates[int(len(market_dates) * 0.80)])
    candidates = assign_partition(candidates, train_cut, holdout_cut)

    # Outcomes are checked against the partition's date range later, so no forward return
    # is allowed to cross into another partition.
    candidate_path = out_dir / "historical_candidate_events.parquet"
    candidates.to_parquet(candidate_path, index=False)

    daily = build_daily_portfolios(candidates, train_cut, holdout_cut, pd.Timestamp(market_dates[0]), pd.Timestamp(market_dates[-1]))
    if daily.empty:
        raise RuntimeError("No portfolios produced after applying chronology and outcome completeness checks.")
    summary_df = summarize_portfolios(daily)
    comparisons_df = compare_qc_to_all(daily)

    daily.to_csv(out_dir / "daily_qc_portfolio_returns.csv", index=False)
    summary_df.to_csv(out_dir / "qc_group_performance_summary.csv", index=False)
    comparisons_df.to_csv(out_dir / "qc_incremental_holdout_comparisons.csv", index=False)

    primary = summary_df[
        summary_df["Partition"].eq("Holdout")
        & summary_df["TopK"].eq(PRIMARY_TOP_K)
        & summary_df["Horizon"].eq(PRIMARY_HORIZON)
        & summary_df["CostScenario"].eq("Base")
    ]
    split_meta = {
        "train_signal_dates": [str(market_dates[0].date()), str((train_cut - pd.Timedelta(days=1)).date())],
        "validation_signal_dates": [str(train_cut.date()), str((holdout_cut - pd.Timedelta(days=1)).date())],
        "holdout_signal_dates": [str(holdout_cut.date()), str(market_dates[-1].date())],
        "train_cut": str(train_cut.date()),
        "holdout_cut": str(holdout_cut.date()),
    }
    metadata = {
        "research_version": "V15 Point-in-Time QC Group Validation",
        "status": "research_only",
        "symbols_requested": len(symbols),
        "symbols_downloaded": len(prices),
        "candidate_events": int(len(candidates)),
        "candidate_tickers": int(candidates["Ticker"].nunique()),
        "candidate_dates": int(candidates["SignalDate"].nunique()),
        "long_events": int(candidates["Direction"].eq("Long").sum()),
        "short_events": int(candidates["Direction"].eq("Short").sum()),
        "qc_flagged_events": int(candidates["QCFlagged"].sum()),
        "qc_clear_events": int((~candidates["QCFlagged"]).sum()),
        "qc_unusual_volume_events": int(candidates["QCUnusualVolume"].sum()),
        "qc_countertrend_events": int(candidates["QCCountertrend"].sum()),
        "primary_test": "Holdout Top 10 by heuristic SetupScore; next-session open entry; 5-session exit; QC Clear/Flagged compared with All.",
        "qcs": {
            "volume_acceleration_high": ">=5x previous day's volume; extreme >=10x",
            "relative_volume_extreme": "RVOL20 >=10x 20-day average",
            "extended_trigger": "BreakoutATR >=4 ATR",
            "high_volatility": "ATR20Pct >=8%",
            "tight_reference_stop": "RiskATR <0.5",
            "countertrend": "Long while SPY regime Bearish or Short while SPY regime Bullish",
        },
        "cost_assumptions": COST_SCENARIOS,
        "cost_method": "Fixed round-trip transaction/slippage cost deducted from each trade; short borrow annualized rate multiplied by holding sessions / 252 for shorts.",
        "split": split_meta,
        "execution_convention": "Signal is generated using the close of SignalDate; return starts at next session open and exits at close h sessions after signal. Matched SPY window used for directional excess return.",
        "bias_limitations": "Uses current listed ticker universe and yfinance history; survivorship bias remains because point-in-time membership and delisted stocks are unavailable. Results are exploratory and costs are assumed, not broker-specific.",
        "primary_holdout_base_cost_top10_5d": primary.to_dict(orient="records"),
        "incremental_holdout_comparisons": comparisons_df.to_dict(orient="records"),
        "interpretation_gate": "QC filter is not recommended for trading unless holdout net results are economically meaningful, comparison confidence intervals support improvement, coverage is sufficient, and an independent replication agrees.",
    }
    (out_dir / "summary.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

    print(json.dumps(metadata, indent=2, default=str), flush=True)
    print("\nHOLDOUT BASE-COST TOP 10 / 5-SESSION SUMMARY\n", flush=True)
    print(primary.to_string(index=False), flush=True)
    print("\nQC FILTER INCREMENTAL HOLDOUT COMPARISONS\n", flush=True)
    print(comparisons_df.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
