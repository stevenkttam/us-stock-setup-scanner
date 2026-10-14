from __future__ import annotations
"""V17 historical replay of the live EOD heuristic scanner.

Research-only. Current Yahoo Finance market cap is applied as a static filter,
not point-in-time historical market capitalization; look-ahead and survivorship
limitations therefore remain. No parameters are optimized on the holdout.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.market_cap import fetch_market_caps, market_cap_meets_threshold
from scanner.scan import score_candidate
from scanner.universe import fetch_us_universe
from scripts.run_live_scan import add_quality_control_flags

HORIZONS = (1, 3, 5, 10)
TOP_KS = (10, 40)
COSTS = {
    "Base": {"round_trip_cost_bps": 10.0, "annual_short_borrow_rate": 0.03},
    "Stress": {"round_trip_cost_bps": 25.0, "annual_short_borrow_rate": 0.10},
}
BOOTSTRAP_REPS = 2500
BOOTSTRAP_BLOCK = 10


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return normalized daily OHLCV with a timezone-naive date index."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(out.columns):
        return pd.DataFrame()
    out = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return out[(out[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (out["Volume"] >= 0)]


def deterministic_sample(symbols, n: int = 1000) -> list[str]:
    """Stable pseudo-random ticker sample, independent of source ordering."""
    unique = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))
    return sorted(
        unique,
        key=lambda s: hashlib.sha256(("V17-EOD-SETUP|" + s).encode("utf-8")).hexdigest(),
    )[: max(0, int(n))]


def partition_boundaries(calendar: pd.DatetimeIndex) -> tuple[int, int]:
    if len(calendar) < 250:
        raise RuntimeError(f"Need at least 250 SPY sessions; got {len(calendar)}")
    return int(len(calendar) * 0.60), int(len(calendar) * 0.80)


def partition_for_position(position: int, train_end: int, holdout_start: int) -> str:
    if position < train_end:
        return "Train"
    if position < holdout_start:
        return "Validation"
    return "Holdout"


def block_bootstrap_ci(values, reps: int = BOOTSTRAP_REPS, block: int = BOOTSTRAP_BLOCK, seed: int = 42):
    """Circular moving-block bootstrap over ordered scored signal-date observations."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n == 0:
        return np.nan, np.nan, np.nan
    mean = float(arr.mean())
    if n < 2 * block:
        return mean, np.nan, np.nan
    rng = np.random.default_rng(seed)
    estimates = np.empty(reps)
    n_blocks = int(np.ceil(n / block))
    offsets = np.arange(block)
    for i in range(reps):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate([arr[(start + offsets) % n] for start in starts])[:n]
        estimates[i] = sample.mean()
    return mean, float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def simulate_trade(candidate: dict, prices: pd.DataFrame, spy: pd.DataFrame,
                   calendar: pd.DatetimeIndex, horizon: int,
                   signal_position: int | None = None) -> dict:
    """Next-open entry, recalculated 1R stop / 2R target, time exit at horizon.

    If both barriers are touched in one daily bar, assume stop first. A stop
    crossed on an opening gap fills at the open; target gaps are capped at target.
    """
    signal_date = pd.Timestamp(candidate["SignalDate"]).normalize()
    position = calendar.get_indexer([signal_date])[0] if signal_position is None else int(signal_position)
    base = {
        "CandidateID": str(candidate["CandidateID"]),
        "SignalDate": signal_date.strftime("%Y-%m-%d"),
        "Ticker": candidate["Ticker"], "Direction": candidate["Direction"],
        "Setup": candidate["Setup"], "SetupScore": float(candidate["SetupScore"]),
        "Horizon": int(horizon), "OutcomeStatus": "pending",
        "EntryDate": None, "ExitDate": None, "PlannedExitDate": None,
        "EntryPrice": np.nan, "ExitPrice": np.nan, "HoldingSessions": np.nan,
        "ExitReason": None, "StockReturn": np.nan, "SPYReturn": np.nan,
        "DirectionalGrossReturn": np.nan, "DirectionalExcessVsSPY": np.nan,
        "RMultiple": np.nan, "NetBase": np.nan, "NetStress": np.nan,
    }
    if position < 0:
        base["OutcomeStatus"] = "invalid_signal_date_not_in_SPY_calendar"
        return base
    if position + horizon >= len(calendar):
        base["OutcomeStatus"] = "pending_exit_beyond_download"
        return base

    entry_date = pd.Timestamp(calendar[position + 1]).normalize()
    planned_exit_date = pd.Timestamp(calendar[position + horizon]).normalize()
    base["EntryDate"] = entry_date.strftime("%Y-%m-%d")
    base["PlannedExitDate"] = planned_exit_date.strftime("%Y-%m-%d")
    if entry_date not in prices.index:
        base["OutcomeStatus"] = "invalid_missing_stock_entry_bar"
        return base
    entry = float(prices.loc[entry_date]["Open"])
    if not np.isfinite(entry) or entry <= 0:
        base["OutcomeStatus"] = "invalid_bad_stock_entry_price"
        return base
    if entry_date not in spy.index or not np.isfinite(spy.loc[entry_date].get("Open", np.nan)):
        base["OutcomeStatus"] = "invalid_missing_SPY_entry_bar"
        return base

    direction = str(candidate["Direction"])
    sign = 1.0 if direction == "Long" else -1.0
    signal_close = float(candidate["SignalClose"])
    stop = entry + (float(candidate["Stop"]) - signal_close)
    target = entry + (float(candidate["Target2R"]) - signal_close)
    risk = (entry - stop) if direction == "Long" else (stop - entry)
    if not np.isfinite(risk) or risk <= 0:
        base["OutcomeStatus"] = "invalid_bad_reference_risk"
        return base

    exit_price = exit_date = exit_reason = None
    holding = 0
    for date in calendar[position + 1: position + horizon + 1]:
        date = pd.Timestamp(date).normalize()
        if date not in prices.index:
            base["OutcomeStatus"] = "invalid_missing_stock_bar_before_exit"
            return base
        bar = prices.loc[date]
        o, hi, lo, close = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
        if not all(np.isfinite(v) and v > 0 for v in (o, hi, lo, close)):
            base["OutcomeStatus"] = "invalid_bad_stock_bar"
            return base
        holding += 1
        if direction == "Long":
            if o <= stop:
                exit_price, exit_reason = o, "stop_gap"
            elif o >= target:
                exit_price, exit_reason = target, "target_gap_capped"
            elif lo <= stop and hi >= target:
                exit_price, exit_reason = stop, "stop_and_target_same_bar_stop_first"
            elif lo <= stop:
                exit_price, exit_reason = stop, "stop"
            elif hi >= target:
                exit_price, exit_reason = target, "target"
        else:
            if o >= stop:
                exit_price, exit_reason = o, "stop_gap"
            elif o <= target:
                exit_price, exit_reason = target, "target_gap_capped"
            elif hi >= stop and lo <= target:
                exit_price, exit_reason = stop, "stop_and_target_same_bar_stop_first"
            elif hi >= stop:
                exit_price, exit_reason = stop, "stop"
            elif lo <= target:
                exit_price, exit_reason = target, "target"
        if exit_price is not None:
            exit_date = date
            break

    if exit_price is None:
        exit_date = planned_exit_date
        if exit_date not in prices.index:
            base["OutcomeStatus"] = "invalid_missing_stock_planned_exit_bar"
            return base
        exit_price, exit_reason = float(prices.loc[exit_date]["Close"]), "time_exit"
    if exit_date not in spy.index:
        base["OutcomeStatus"] = "invalid_missing_SPY_exit_bar"
        return base

    spy_open, spy_close = float(spy.loc[entry_date]["Open"]), float(spy.loc[exit_date]["Close"])
    if not all(np.isfinite(v) and v > 0 for v in (spy_open, spy_close)):
        base["OutcomeStatus"] = "invalid_bad_SPY_price"
        return base
    stock_return = exit_price / entry - 1.0
    spy_return = spy_close / spy_open - 1.0
    gross = sign * stock_return
    excess = gross - sign * spy_return
    r_multiple = gross * entry / risk
    base.update({
        "OutcomeStatus": "complete", "ExitDate": exit_date.strftime("%Y-%m-%d"),
        "EntryPrice": entry, "ExitPrice": exit_price, "HoldingSessions": int(holding),
        "ExitReason": exit_reason, "StockReturn": stock_return, "SPYReturn": spy_return,
        "DirectionalGrossReturn": gross, "DirectionalExcessVsSPY": excess,
        "RMultiple": r_multiple,
        "NetBase": gross - COSTS["Base"]["round_trip_cost_bps"] / 10000.0
                  - (COSTS["Base"]["annual_short_borrow_rate"] * holding / 252.0 if direction == "Short" else 0.0),
        "NetStress": gross - COSTS["Stress"]["round_trip_cost_bps"] / 10000.0
                     - (COSTS["Stress"]["annual_short_borrow_rate"] * holding / 252.0 if direction == "Short" else 0.0),
    })
    return base


def build_historical_candidates(price_map, spy, cap_map, symbol_map, cfg) -> tuple[pd.DataFrame, dict]:
    """Apply the live score_candidate() logic on each eligible historical daily bar."""
    regime = classify_regime(spy).copy()
    regime.index = pd.DatetimeIndex(pd.to_datetime(regime.index)).tz_localize(None).normalize()
    frames = []
    diagnostics = {
        "downloaded_symbols": int(len(price_map)), "symbols_with_valid_ohlcv": 0,
        "latest_price_pass": 0, "latest_dollar_volume_pass": 0,
        "latest_price_and_dollar_volume_pass": 0,
        "historical_price_eligible_rows": 0, "historical_price_and_dollar_volume_eligible_rows": 0,
        "historical_signal_events": 0, "historical_signal_dates": 0,
    }
    for ticker_i, (yahoo_symbol, raw_prices) in enumerate(price_map.items(), 1):
        prices = normalize_frame(raw_prices)
        if prices.empty or len(prices) < 220:
            continue
        if not market_cap_meets_threshold(cap_map.get(yahoo_symbol), float(cfg["universe"].get("min_market_cap_usd", 500_000_000))):
            continue
        diagnostics["symbols_with_valid_ohlcv"] += 1
        try:
            features = add_features(prices, spy=spy).copy().sort_index()
            features.index = pd.DatetimeIndex(pd.to_datetime(features.index)).tz_localize(None).normalize()
            latest = features.iloc[-1]
            min_price = float(cfg["universe"]["min_price"])
            min_dv = float(cfg["universe"]["min_avg_dollar_volume_20d"])
            price_ok = pd.notna(latest.get("Close")) and float(latest["Close"]) >= min_price
            dv_ok = pd.notna(latest.get("DollarVolume20")) and float(latest["DollarVolume20"]) >= min_dv
            diagnostics["latest_price_pass"] += int(price_ok)
            diagnostics["latest_dollar_volume_pass"] += int(dv_ok)
            diagnostics["latest_price_and_dollar_volume_pass"] += int(price_ok and dv_ok)
            eligible_mask = (
                features["Close"].ge(min_price)
                & features["DollarVolume20"].ge(min_dv)
                & features["RVOL20"].notna()
                & features["ATR20"].notna()
            )
            eligible = features.loc[eligible_mask]
            diagnostics["historical_price_eligible_rows"] += int(features["Close"].ge(min_price).sum())
            diagnostics["historical_price_and_dollar_volume_eligible_rows"] += int(eligible_mask.sum())
            if eligible.empty:
                continue
            ticker = symbol_map.get(yahoo_symbol, yahoo_symbol)
            cap = float(cap_map[yahoo_symbol])
            ticker_signals = []
            for date, row in eligible.iterrows():
                if date not in regime.index or pd.isna(regime.loc[date]):
                    continue
                date_regime = str(regime.loc[date])
                for direction in ("Long", "Short"):
                    possible = [
                        result for n in cfg["signal"]["consolidation_lengths"]
                        if (result := score_candidate(row, direction, int(n), date_regime, cfg["signal"])) is not None
                    ]
                    if not possible:
                        continue
                    best = max(possible, key=lambda x: x["SetupScore"])
                    best.update({
                        "Ticker": ticker, "YahooSymbol": yahoo_symbol,
                        "SignalDate": pd.Timestamp(date).normalize(),
                        "SignalClose": float(row["Close"]), "MarketRegime": date_regime,
                        "MarketCapUSD": cap, "AvgDollarVolume20D": float(row["DollarVolume20"]),
                    })
                    ticker_signals.append(best)
            if ticker_signals:
                frames.append(pd.DataFrame(ticker_signals))
        except Exception as exc:
            print(f"FEATURE/SIGNAL SKIP {yahoo_symbol}: {type(exc).__name__}: {exc}", flush=True)
        if ticker_i % 100 == 0:
            print(f"Historical candidate generation: processed {ticker_i} symbols; events={sum(len(x) for x in frames):,}", flush=True)

    if not frames:
        raise RuntimeError("No historical candidates generated. Diagnostics: " + json.dumps(diagnostics, default=str))
    candidates = pd.concat(frames, ignore_index=True)
    candidates = add_quality_control_flags(candidates)
    # The live helper emits human-readable flags; materialize the group labels
    # required by historical portfolio comparisons without changing live scores.
    candidates["QCFlagged"] = (
        candidates["QualityFlags"].fillna("No automatic QC flags").astype(str)
        != "No automatic QC flags"
    )
    volume_acceleration = pd.to_numeric(candidates.get("VolumeAcceleration"), errors="coerce")
    rvol20 = pd.to_numeric(candidates.get("RVOL20"), errors="coerce")
    candidates["QCUnusualVolume"] = volume_acceleration.ge(10) | rvol20.ge(10)
    candidates = candidates.sort_values(
        ["SignalDate", "Direction", "SetupScore", "Ticker"],
        ascending=[True, True, False, True], kind="mergesort",
    ).reset_index(drop=True)
    candidates["CandidateID"] = (
        candidates["SignalDate"].dt.strftime("%Y-%m-%d") + "|"
        + candidates["Ticker"].astype(str) + "|" + candidates["Direction"].astype(str)
    )
    candidates = candidates.drop_duplicates("CandidateID", keep="first").reset_index(drop=True)
    diagnostics["historical_signal_events"] = int(len(candidates))
    diagnostics["historical_signal_dates"] = int(candidates["SignalDate"].nunique())
    return candidates, diagnostics


def make_trade_outcomes(candidates: pd.DataFrame, price_map, spy, calendar: pd.DatetimeIndex,
                        train_end: int, holdout_start: int) -> pd.DataFrame:
    """Build exact-date outcomes; don't let holding horizons cross split boundaries."""
    stock_frames = {symbol: normalize_frame(frame) for symbol, frame in price_map.items()}
    positions = {pd.Timestamp(date).normalize(): i for i, date in enumerate(calendar)}
    rows = []
    for _, series in candidates.iterrows():
        candidate = series.to_dict()
        date = pd.Timestamp(candidate["SignalDate"]).normalize()
        pos = positions.get(date, -1)
        if pos < 0:
            continue
        partition = partition_for_position(pos, train_end, holdout_start)
        candidate["Partition"] = partition
        candidate["CandidateID"] = str(candidate["CandidateID"])
        stock = stock_frames.get(str(candidate["YahooSymbol"]), pd.DataFrame())
        for horizon in HORIZONS:
            planned_pos = pos + horizon
            if planned_pos >= len(calendar):
                outcome = simulate_trade(candidate, stock, spy, calendar, horizon, pos)
            elif partition == "Train" and planned_pos >= train_end:
                continue
            elif partition == "Validation" and planned_pos >= holdout_start:
                continue
            else:
                outcome = simulate_trade(candidate, stock, spy, calendar, horizon, pos)
            outcome["Partition"] = partition
            rows.append(outcome)
    return pd.DataFrame(rows)


def make_daily_portfolios(candidates: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Score top-K portfolios; selected invalid names are not silently replaced."""
    rows = []
    if candidates.empty or outcomes.empty:
        return pd.DataFrame()
    keys = ["CandidateID", "Horizon"]
    lookup = outcomes.drop_duplicates(keys, keep="first").set_index(keys).to_dict(orient="index")
    for (date, direction), all_for_date in candidates.groupby(["SignalDate", "Direction"], sort=True):
        date = pd.Timestamp(date).normalize()
        groups = {
            "All": all_for_date,
            "QC Clear": all_for_date[~all_for_date["QCFlagged"]],
            "QC Flagged": all_for_date[all_for_date["QCFlagged"]],
            "No Unusual Volume": all_for_date[~all_for_date["QCUnusualVolume"]],
            "Unusual Volume": all_for_date[all_for_date["QCUnusualVolume"]],
        }
        partition = str(all_for_date["Partition"].iloc[0]) if "Partition" in all_for_date else "Unknown"
        # Split-boundary signals have no outcome rows for that horizon for any
        # ticker on the date. Omit the whole date/horizon, not mark it unscorable.
        date_ids = all_for_date["CandidateID"].astype(str).tolist()
        available_horizons = [
            h for h in HORIZONS
            if any((candidate_id, int(h)) in lookup for candidate_id in date_ids)
        ]
        for group_name, subset in groups.items():
            ranked = subset.sort_values(["SetupScore", "Ticker"], ascending=[False, True], kind="mergesort")
            for top_k in TOP_KS:
                selected = ranked.head(top_k)
                for horizon in available_horizons:
                    selected_outcomes = [lookup.get((str(cid), int(horizon))) for cid in selected["CandidateID"]]
                    if selected.empty:
                        status, selected_trades = "no_candidates", []
                    elif any(x is None for x in selected_outcomes):
                        status, selected_trades = "unscorable_missing_outcome", []
                    elif any(x.get("OutcomeStatus") != "complete" for x in selected_outcomes):
                        statuses = [x.get("OutcomeStatus", "") for x in selected_outcomes if x is not None]
                        status = "pending_outcomes" if any(str(s).startswith("pending") for s in statuses) else "unscorable_missing_outcome"
                        selected_trades = []
                    else:
                        status, selected_trades = "complete", selected_outcomes
                    for scenario in COSTS:
                        if status == "complete" and selected_trades:
                            net_col = "NetBase" if scenario == "Base" else "NetStress"
                            gross = np.array([float(x["DirectionalGrossReturn"]) for x in selected_trades])
                            excess = np.array([float(x["DirectionalExcessVsSPY"]) for x in selected_trades])
                            net = np.array([float(x[net_col]) for x in selected_trades])
                            pnl_gross, pnl_excess, pnl_net = float(gross.sum()/top_k), float(excess.sum()/top_k), float(net.sum()/top_k)
                            mean_trade, hit_rate = float(net.mean()), float((net > 0).mean())
                            avg_r = float(np.mean([float(x["RMultiple"]) for x in selected_trades]))
                            stops = sum(str(x["ExitReason"]).startswith("stop") or "stop_and_target" in str(x["ExitReason"]) for x in selected_trades)
                            targets = sum(str(x["ExitReason"]).startswith("target") for x in selected_trades)
                            mean_holding = float(np.mean([float(x["HoldingSessions"]) for x in selected_trades]))
                        elif status == "no_candidates":
                            pnl_gross = pnl_excess = pnl_net = 0.0
                            mean_trade = hit_rate = avg_r = mean_holding = np.nan
                            stops = targets = 0
                        else:
                            pnl_gross = pnl_excess = pnl_net = mean_trade = hit_rate = avg_r = mean_holding = np.nan
                            stops = targets = 0
                        rows.append({
                            "SignalDate": date.strftime("%Y-%m-%d"), "Partition": partition,
                            "Direction": direction, "QCGroup": group_name, "TopK": int(top_k),
                            "Horizon": int(horizon), "CostScenario": scenario,
                            "SelectedSignals": int(len(selected)),
                            "NTrades": int(len(selected_trades)) if status == "complete" else 0,
                            "TargetSlots": int(top_k),
                            "SlotCoverage": float(len(selected)/top_k) if not selected.empty else 0.0,
                            "PortfolioStatus": status,
                            "MeanDirectionalGrossPnL": pnl_gross,
                            "MeanDirectionalExcessVsSPY": pnl_excess,
                            "MeanNetPnL": pnl_net, "MeanNetPnLPerTrade": mean_trade,
                            "NetHitRate": hit_rate, "MeanRMultiple": avg_r,
                            "StopExitCount": int(stops), "TargetExitCount": int(targets),
                            "MeanHoldingSessions": mean_holding,
                        })
    return pd.DataFrame(rows)


def summarize_portfolios(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if daily.empty:
        return pd.DataFrame()
    keys = ["Partition", "Direction", "QCGroup", "TopK", "Horizon", "CostScenario"]
    for key, group in daily.groupby(keys, sort=True):
        complete = group[group["PortfolioStatus"].isin(["complete", "no_candidates"])].copy()
        score = pd.to_numeric(complete["MeanNetPnL"], errors="coerce")
        alpha = pd.to_numeric(complete["MeanDirectionalExcessVsSPY"], errors="coerce")
        mean, low, high = block_bootstrap_ci(score, seed=100 + int(key[4]) + int(key[3]))
        alpha_mean, alpha_low, alpha_high = block_bootstrap_ci(alpha, seed=200 + int(key[4]) + int(key[3]))
        rows.append({
            **dict(zip(keys, key)),
            "SignalDates": int(group["SignalDate"].nunique()),
            "ScorableDates": int(complete["SignalDate"].nunique()),
            "PendingDates": int(group.loc[group["PortfolioStatus"] == "pending_outcomes", "SignalDate"].nunique()),
            "UnscorableDates": int(group.loc[group["PortfolioStatus"] == "unscorable_missing_outcome", "SignalDate"].nunique()),
            "TotalTrades": int(complete["NTrades"].sum()),
            "MeanDailyGrossPnL": float(pd.to_numeric(complete["MeanDirectionalGrossPnL"], errors="coerce").mean()) if not complete.empty else np.nan,
            "MeanDailyExcessVsSPY": alpha_mean, "MeanDailyNetPnL": mean,
            "MeanNetPnLPerTrade": float(pd.to_numeric(complete["MeanNetPnLPerTrade"], errors="coerce").mean()) if complete["MeanNetPnLPerTrade"].notna().any() else np.nan,
            "NetCI95Low": low, "NetCI95High": high,
            "AlphaCI95Low": alpha_low, "AlphaCI95High": alpha_high,
            "NetPositiveDatePct": float((score > 0).mean()) if score.notna().any() else np.nan,
            "MeanSlotCoverage": float(pd.to_numeric(complete["SlotCoverage"], errors="coerce").mean()) if not complete.empty else np.nan,
            "MeanRMultiple": float(pd.to_numeric(complete["MeanRMultiple"], errors="coerce").mean()) if complete["MeanRMultiple"].notna().any() else np.nan,
            "StopExitCount": int(complete["StopExitCount"].sum()),
            "TargetExitCount": int(complete["TargetExitCount"].sum()),
            "CIStatus": "available" if np.isfinite(low) else f"need at least {2 * BOOTSTRAP_BLOCK} scorable dates",
        })
    return pd.DataFrame(rows)


def compare_qc_groups(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if daily.empty:
        return pd.DataFrame()
    primary = daily[(daily["TopK"] == 10) & daily["QCGroup"].isin(["All", "QC Clear", "QC Flagged"])]
    for partition in ("Validation", "Holdout"):
        for direction in ("Long", "Short"):
            for horizon in HORIZONS:
                for scenario in COSTS:
                    base = primary[
                        (primary["Partition"] == partition) & (primary["Direction"] == direction)
                        & (primary["Horizon"] == horizon) & (primary["CostScenario"] == scenario)
                        & (primary["QCGroup"] == "All")
                    ][["SignalDate", "MeanNetPnL", "PortfolioStatus"]].rename(columns={"MeanNetPnL": "AllNet", "PortfolioStatus": "AllStatus"})
                    for group_name in ("QC Clear", "QC Flagged"):
                        part = primary[
                            (primary["Partition"] == partition) & (primary["Direction"] == direction)
                            & (primary["Horizon"] == horizon) & (primary["CostScenario"] == scenario)
                            & (primary["QCGroup"] == group_name)
                        ][["SignalDate", "MeanNetPnL", "PortfolioStatus"]].rename(columns={"MeanNetPnL": "GroupNet", "PortfolioStatus": "GroupStatus"})
                        paired = base.merge(part, on="SignalDate", how="inner")
                        paired = paired[
                            paired["AllStatus"].isin(["complete", "no_candidates"])
                            & paired["GroupStatus"].isin(["complete", "no_candidates"])
                        ].dropna(subset=["AllNet", "GroupNet"])
                        diff = paired["GroupNet"] - paired["AllNet"]
                        mean, low, high = block_bootstrap_ci(diff, seed=700 + horizon + (10 if direction == "Short" else 0) + (100 if scenario == "Stress" else 0))
                        rows.append({
                            "Partition": partition, "Direction": direction, "Horizon": horizon,
                            "CostScenario": scenario, "Comparison": f"{group_name} minus All",
                            "PairedScorableDates": int(len(paired)), "MeanNetPnLDifference": mean,
                            "DiffCI95Low": low, "DiffCI95High": high,
                            "ImprovementSupported": bool(np.isfinite(low) and low > 0),
                        })
    return pd.DataFrame(rows)


def write_report(summary: dict, metrics: pd.DataFrame, qc: pd.DataFrame, path: Path):
    def pct(value):
        return "—" if pd.isna(value) or not np.isfinite(value) else f"{value:.2%}"
    lines = [
        "# V17 Historical Replay — Live EOD Heuristic Setup Scanner", "",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Research only: **{summary.get('research_only', True)}**",
        f"- Universe sample size: **{summary.get('sample_requested', 0):,}** current symbols sampled",
        f"- Market-cap-qualified symbols downloaded: **{summary.get('symbols_downloaded', 0):,}**",
        f"- Historical signal events / dates: **{summary.get('candidate_events', 0):,} / {summary.get('candidate_dates', 0):,}**",
        f"- Price and volume lookback: **{summary.get('period', '—')}**",
        f"- Historical data range: **{summary.get('data_start', '—')} to {summary.get('data_end', '—')}**", "",
        "## Important market-cap limitation", "",
        "Yahoo Finance does not provide a reliable free point-in-time market-cap history in this workflow. The backtest uses today's market-cap snapshot to decide which symbols enter the historical sample, then applies the $5 price and $20M 20-session average-dollar-volume rules using each historical signal day's data. This is a **current-universe proxy**, not a fully point-in-time backtest; it can have survivorship and look-ahead bias. Results must not be described as exact historical replication of the market-cap rule.", "",
        "## Execution assumptions", "",
        "- Use the live scanner's unchanged score_candidate() logic and configuration for each historical daily bar.",
        "- Signal after close; enter at the next SPY-session open.",
        "- Translate stop/target distances from signal-close reference levels to the next-session entry open, consistent with the live note to recalculate levels after an opening gap.",
        "- Exit at the first stop or 2R target; if neither is hit, exit at the horizon's close. If both stop and target are inside the same daily bar, assume stop first.",
        "- Stop gaps fill at the opening price; target gaps are capped at the target price.",
        "- Base cost: 10 bps round trip / 3% annual short-borrow assumption. Stress: 25 bps / 10%. Borrow is charged for actual holding sessions.",
        "- Top 10 and Top 40 are ranked independently each signal date and direction. Unfilled slots are cash at zero. Missing/invalid bars in selected slots make the portfolio-date unscorable; lower-ranked names are not substituted.",
        "- Returns are averages per decision-date cohort with overlapping holding windows, not a compounded equity curve, CAGR, or Sharpe ratio.", "",
        "## Holdout results — All candidates, Top 10 and Top 40", "",
        "| Direction | Top K | Horizon | Cost | Scorable dates | Trades | Mean net return / date | 95% CI | Mean directional excess vs SPY | Excess 95% CI | Mean return / trade |",
        "|---|---:|---:|---|---:|---:|---:|---|---:|---|---:|",
    ]
    if metrics.empty:
        lines.append("| — | — | — | — | 0 | 0 | — | — | — | — | — |")
    else:
        rows = metrics[(metrics["Partition"] == "Holdout") & (metrics["QCGroup"] == "All")].sort_values(["Direction", "TopK", "Horizon", "CostScenario"])
        for _, r in rows.iterrows():
            lines.append(
                f"| {r['Direction']} | {int(r['TopK'])} | {int(r['Horizon'])} | {r['CostScenario']} "
                f"| {int(r['ScorableDates'])} | {int(r['TotalTrades'])} | {pct(r['MeanDailyNetPnL'])} "
                f"| [{pct(r['NetCI95Low'])}, {pct(r['NetCI95High'])}] "
                f"| {pct(r['MeanDailyExcessVsSPY'])} | [{pct(r['AlphaCI95Low'])}, {pct(r['AlphaCI95High'])}] "
                f"| {pct(r['MeanNetPnLPerTrade'])} |"
            )
    lines.extend([
        "", "## Holdout QC comparisons — Top 10", "",
        "| Direction | Horizon | Cost | Comparison | Paired dates | Mean net difference | 95% CI | Positive improvement supported? |",
        "|---|---:|---|---|---:|---:|---|---|",
    ])
    if qc.empty:
        lines.append("| — | — | — | — | 0 | — | — | No |")
    else:
        for _, r in qc[qc["Partition"] == "Holdout"].sort_values(["Direction", "Horizon", "CostScenario", "Comparison"]).iterrows():
            lines.append(
                f"| {r['Direction']} | {int(r['Horizon'])} | {r['CostScenario']} | {r['Comparison']} "
                f"| {int(r['PairedScorableDates'])} | {pct(r['MeanNetPnLDifference'])} "
                f"| [{pct(r['DiffCI95Low'])}, {pct(r['DiffCI95High'])}] "
                f"| {'Yes' if r['ImprovementSupported'] else 'No'} |"
            )
    lines.extend(["", "## Filter diagnostics", "", "| Measure | Count |", "|---|---:|"])
    for key, val in summary.get("diagnostics", {}).items():
        if isinstance(val, (int, float)):
            lines.append(f"| {key.replace('_', ' ')} | {int(val):,} |")
    lines.extend([
        "", "## Caveats", "",
        "- Historical market capitalization is not point-in-time. The static current-cap screen is a proxy and can bias historical results.",
        "- Historical membership is based on today's symbol directory; delisted and removed tickers are generally missing.",
        "- Free Yahoo Finance data can have symbol mapping, bar, volume and corporate-action issues. The backtest records exact-date data failures rather than shifting to other sessions.",
        "- The stop/target simulation uses daily OHLC bars, not intraday order sequencing. Stop-first is assumed if both barrier levels are touched in one bar.",
        "- Actual spreads, market impact, borrow availability/recalls and broker-specific fees are not modeled.",
        "- A positive result is not proof of a tradable edge. No ranking parameters are optimized here and the strategy remains research-only.",
        "", "See summary.json, portfolio_metrics.csv, daily_portfolio_returns.csv, trade_outcomes.csv, qc_comparisons.csv, and universe_sample.csv for supporting detail.", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    out_dir = ROOT / "artifacts" / "eod_setup_backtest_v17"
    out_dir.mkdir(parents=True, exist_ok=True)
    period = os.getenv("SCANNER_PERIOD", "10y")
    sample_size = int(os.getenv("SCANNER_MAX_SYMBOLS", "1000"))
    workers = int(os.getenv("SCANNER_MARKET_CAP_WORKERS", "6"))
    min_cap = float(cfg["universe"].get("min_market_cap_usd", 500_000_000))
    universe = fetch_us_universe(data_dir / "universe.csv", refresh=True)
    symbols = deterministic_sample(universe["YahooSymbol"].dropna().astype(str).tolist(), sample_size)
    display_map = dict(zip(universe["YahooSymbol"].astype(str), universe["Symbol"].astype(str)))
    print(f"Universe symbols: {len(universe):,}; stable V17 sample: {len(symbols):,}", flush=True)

    cap_map = fetch_market_caps(symbols, max_workers=workers)
    caps = pd.DataFrame([
        {"YahooSymbol": s, "Symbol": display_map.get(s, s), "MarketCapUSD": cap_map.get(s),
         "MarketCapStatus": "qualified" if market_cap_meets_threshold(cap_map.get(s), min_cap)
         else ("below_threshold" if cap_map.get(s) is not None else "unavailable")}
        for s in symbols
    ])
    qualified_symbols = caps.loc[caps["MarketCapStatus"] == "qualified", "YahooSymbol"].tolist()
    caps.to_csv(out_dir / "universe_sample.csv", index=False)
    print(
        f"Market-cap lookups: {len(cap_map):,}; qualified: {len(qualified_symbols):,}; "
        f"below threshold: {(caps.MarketCapStatus == 'below_threshold').sum():,}; "
        f"unavailable: {(caps.MarketCapStatus == 'unavailable').sum():,}.", flush=True,
    )
    if len(qualified_symbols) < 100:
        raise RuntimeError(
            f"Only {len(qualified_symbols)} sampled symbols have known market cap above USD {min_cap:,.0f}; "
            "not enough for this validation design. Check Yahoo metadata availability."
        )

    price_map = download_daily(
        qualified_symbols, period=period,
        batch_size=int(os.getenv("SCANNER_BATCH_SIZE", "50")),
        sleep_seconds=float(os.getenv("SCANNER_SLEEP_SECONDS", "0.75")),
    )
    print(f"Daily histories downloaded: {len(price_map):,} / {len(qualified_symbols):,}", flush=True)
    spy_map = download_daily(["SPY"], period=period, batch_size=1, sleep_seconds=0.2)
    spy = normalize_frame(spy_map.get("SPY"))
    if spy.empty or len(spy) < 250:
        raise RuntimeError("SPY reference history is missing or too short.")
    calendar = pd.DatetimeIndex(spy.index).normalize().unique().sort_values()
    train_end, holdout_start = partition_boundaries(calendar)
    print(
        f"SPY calendar: {calendar[0].date()} to {calendar[-1].date()}; "
        f"Train before {calendar[train_end].date()}, validation before {calendar[holdout_start].date()}.",
        flush=True,
    )

    candidates, diagnostics = build_historical_candidates(price_map, spy, cap_map, display_map, cfg)
    positions = {pd.Timestamp(date).normalize(): i for i, date in enumerate(calendar)}
    candidates["Partition"] = candidates["SignalDate"].map(
        lambda d: partition_for_position(positions.get(pd.Timestamp(d).normalize(), -1), train_end, holdout_start)
    )
    candidates = candidates[candidates["Partition"] != "Unknown"].copy()
    print(
        f"Candidate events: {len(candidates):,}; signal dates: {candidates.SignalDate.nunique():,}; "
        f"long: {(candidates.Direction == 'Long').sum():,}; short: {(candidates.Direction == 'Short').sum():,}.",
        flush=True,
    )
    candidates.to_csv(out_dir / "historical_candidates.csv", index=False)

    outcomes = make_trade_outcomes(candidates, price_map, spy, calendar, train_end, holdout_start)
    outcomes.to_csv(out_dir / "trade_outcomes.csv", index=False)
    portfolios = make_daily_portfolios(candidates, outcomes)
    portfolios.to_csv(out_dir / "daily_portfolio_returns.csv", index=False)
    metrics = summarize_portfolios(portfolios)
    metrics.to_csv(out_dir / "portfolio_metrics.csv", index=False)
    qc = compare_qc_groups(portfolios)
    qc.to_csv(out_dir / "qc_comparisons.csv", index=False)

    normalized_prices = {k: normalize_frame(v) for k, v in price_map.items()}
    recent_pass_price = recent_pass_dv = recent_pass_both = 0
    for frame in normalized_prices.values():
        if frame.empty:
            continue
        last = add_features(frame, spy=spy).iloc[-1]
        price_ok = pd.notna(last.get("Close")) and float(last["Close"]) >= float(cfg["universe"]["min_price"])
        dv_ok = pd.notna(last.get("DollarVolume20")) and float(last["DollarVolume20"]) >= float(cfg["universe"]["min_avg_dollar_volume_20d"])
        recent_pass_price += int(price_ok)
        recent_pass_dv += int(dv_ok)
        recent_pass_both += int(price_ok and dv_ok)
    diagnostics.update({
        "sample_requested": int(len(symbols)),
        "market_cap_lookups": int(len(cap_map)),
        "market_cap_qualified": int(len(qualified_symbols)),
        "market_cap_below_threshold": int((caps.MarketCapStatus == "below_threshold").sum()),
        "market_cap_unavailable": int((caps.MarketCapStatus == "unavailable").sum()),
        "price_histories_downloaded": int(len(price_map)),
        "price_histories_normalized": int(sum(not frame.empty for frame in normalized_prices.values())),
        "latest_price_pass_after_market_cap": int(recent_pass_price),
        "latest_dollar_volume_pass_after_market_cap": int(recent_pass_dv),
        "latest_price_and_dollar_volume_pass_after_market_cap": int(recent_pass_both),
    })
    outcomes_summary = {}
    for horizon in HORIZONS:
        part = outcomes[outcomes["Horizon"] == horizon] if not outcomes.empty else pd.DataFrame()
        outcomes_summary[str(horizon)] = {
            "complete": int((part.OutcomeStatus == "complete").sum()) if not part.empty else 0,
            "pending": int(part.OutcomeStatus.astype(str).str.startswith("pending").sum()) if not part.empty else 0,
            "invalid": int((~part.OutcomeStatus.isin(["complete"]) & ~part.OutcomeStatus.astype(str).str.startswith("pending")).sum()) if not part.empty else 0,
        }
    summary = {
        "status": "success", "research_only": True, "production_ready": False,
        "research_version": "V17 Historical Replay of Live EOD Heuristic Setup Scanner",
        "sample_requested": int(len(symbols)), "symbols_market_cap_qualified": int(len(qualified_symbols)),
        "symbols_downloaded": int(len(price_map)), "period": period,
        "market_cap_threshold_usd": min_cap,
        "price_threshold_usd": float(cfg["universe"]["min_price"]),
        "average_dollar_volume_threshold_usd": float(cfg["universe"]["min_avg_dollar_volume_20d"]),
        "market_cap_method": "current Yahoo Finance snapshot, applied statically to historical ticker sample; not point-in-time",
        "data_start": str(calendar[0].date()), "data_end": str(calendar[-1].date()),
        "train_cut": str(calendar[train_end].date()), "holdout_cut": str(calendar[holdout_start].date()),
        "candidate_events": int(len(candidates)), "candidate_dates": int(candidates.SignalDate.nunique()),
        "long_events": int((candidates.Direction == "Long").sum()), "short_events": int((candidates.Direction == "Short").sum()),
        "outcomes_by_horizon": outcomes_summary, "cost_assumptions": COSTS,
        "execution": "next SPY session open; signal-close stop/target offsets translated to entry open; stop-first if both barriers touched intraday; stop gaps at open; target fills capped at target; time exit at horizon close",
        "horizons": list(HORIZONS), "top_k": list(TOP_KS),
        "bootstrap": {"method": "circular moving block", "block_observations": BOOTSTRAP_BLOCK, "replicates": BOOTSTRAP_REPS, "min_observations_for_ci": 2 * BOOTSTRAP_BLOCK},
        "diagnostics": diagnostics,
        "limitations": [
            "Current Yahoo Finance market cap is used as a static proxy and is not point-in-time historical market capitalization.",
            "Today's security directory excludes most delisted historical names; survivorship and membership bias remain.",
            "No parameter optimization or trade probability calibration is performed.",
            "Daily OHLC cannot resolve intraday stop/target ordering; stop-first is assumed if both barrier levels are touched.",
            "Actual spreads, market impact, borrow availability/recalls and broker-specific fees are not modeled.",
            "Portfolio metrics are per signal-date cohorts with overlapping holding periods; not an executable compounded equity curve.",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    write_report(summary, metrics, qc, out_dir / "report.md")
    print(json.dumps(summary, indent=2, default=str), flush=True)
    primary = metrics[
        (metrics["Partition"] == "Holdout") & (metrics["QCGroup"] == "All")
        & (metrics["Horizon"].isin([5, 10])) & (metrics["CostScenario"] == "Base")
    ]
    if not primary.empty:
        print("\nPRIMARY HOLDOUT RESULTS: ALL CANDIDATES / BASE COST / 5D & 10D\n" + primary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
