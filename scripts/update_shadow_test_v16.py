from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.config import ROOT
from scanner.data import download_daily
from scanner.universe import fetch_us_universe


HORIZONS = (1, 3, 5, 10)
TOP_KS = (10, 40)
MIN_DECISION_DATES = 200
BOOTSTRAP_BLOCK_SESSIONS = 10
COSTS = {
    "Base": {"round_trip_cost_bps": 10.0, "annual_short_borrow_rate": 0.03},
    "Stress": {"round_trip_cost_bps": 25.0, "annual_short_borrow_rate": 0.10},
}
KEY_COLUMNS = ["SignalDate", "Ticker", "Direction"]
OUTCOME_STATUS_COLUMNS = [f"OutcomeStatus_{h}d" for h in HORIZONS]
LEDGER_DIR = ROOT / "data" / "shadow"


def normalize_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def date_text(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def signal_key(row) -> tuple[str, str, str]:
    return (date_text(row["SignalDate"]), str(row["Ticker"]), str(row["Direction"]))


def horizon_dates(calendar: pd.DatetimeIndex, signal_date, horizon: int):
    """Return the next-session entry and h-session exit dates on the SPY calendar."""
    signal = pd.Timestamp(signal_date).normalize()
    locations = np.flatnonzero(calendar == signal)
    if len(locations) == 0:
        return None, None, "signal_date_not_in_market_calendar"
    i = int(locations[0])
    if i + horizon >= len(calendar):
        return (calendar[i + 1] if i + 1 < len(calendar) else None), None, "pending_future_exit"
    return calendar[i + 1], calendar[i + horizon], "ready"


def net_directional_pnl(direction: str, stock_return: float, horizon: int, cost_case: str) -> float:
    assumptions = COSTS[cost_case]
    sign = 1.0 if direction == "Long" else -1.0
    borrow = assumptions["annual_short_borrow_rate"] * horizon / 252.0 if direction == "Short" else 0.0
    return sign * stock_return - assumptions["round_trip_cost_bps"] / 10_000.0 - borrow


def calculate_outcome(stock: pd.DataFrame, spy: pd.DataFrame, entry_date, exit_date, direction: str, horizon: int) -> dict:
    """Calculate open-to-close holding return over exactly matched SPY dates."""
    entry_date = pd.Timestamp(entry_date).normalize()
    exit_date = pd.Timestamp(exit_date).normalize()
    stock = normalize_dates(stock)
    spy = normalize_dates(spy)
    if entry_date not in stock.index or exit_date not in stock.index:
        raise KeyError("stock_entry_or_exit_bar_missing")
    if entry_date not in spy.index or exit_date not in spy.index:
        raise KeyError("spy_entry_or_exit_bar_missing")

    entry_price = float(stock.loc[entry_date, "Open"])
    exit_price = float(stock.loc[exit_date, "Close"])
    spy_entry = float(spy.loc[entry_date, "Open"])
    spy_exit = float(spy.loc[exit_date, "Close"])
    if not all(np.isfinite(x) and x > 0 for x in (entry_price, exit_price, spy_entry, spy_exit)):
        raise ValueError("invalid_entry_or_exit_price")

    stock_return = exit_price / entry_price - 1.0
    spy_return = spy_exit / spy_entry - 1.0
    sign = 1.0 if direction == "Long" else -1.0
    result = {
        "EntryDate": date_text(entry_date),
        "ExitDate": date_text(exit_date),
        "EntryPrice": entry_price,
        "ExitPrice": exit_price,
        f"StockReturn_{horizon}d": stock_return,
        f"SPYReturn_{horizon}d": spy_return,
        f"DirectionalGrossPnL_{horizon}d": sign * stock_return,
        f"DirectionalExcessVsSPY_{horizon}d": sign * (stock_return - spy_return),
    }
    for case in COSTS:
        result[f"Net{case}_{horizon}d"] = net_directional_pnl(direction, stock_return, horizon, case)
    return result


def quality_flags(row) -> dict:
    """Fallback for older snapshots without stored QC booleans."""
    flags = str(row.get("QualityFlags", ""))
    return {
        "QCFlagged": bool(flags and flags != "No automatic QC flags" and flags.lower() != "nan"),
        "QCUnusualVolume": ("VOLUME" in flags.upper() and ("EXTREME" in flags.upper() or "HIGH" in flags.upper())),
        "QCCountertrend": "COUNTERTREND" in flags.upper(),
    }


def load_universe_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        universe = pd.read_csv(path)
        if {"Symbol", "YahooSymbol"}.issubset(universe.columns):
            return dict(zip(universe["Symbol"].astype(str), universe["YahooSymbol"].astype(str)))
    except Exception as exc:
        print(f"Universe ticker mapping unavailable: {type(exc).__name__}: {exc}", flush=True)
    return {}


def blank_outcome_fields() -> dict:
    fields = {
        "SignalBarStatus": "pending",
        "EntryDate": "",
        "EntryPrice": np.nan,
        "TrackingStatus": "pending",
    }
    for h in HORIZONS:
        fields.update({
            f"OutcomeStatus_{h}d": "pending",
            f"ExitDate_{h}d": "",
            f"ExitPrice_{h}d": np.nan,
            f"StockReturn_{h}d": np.nan,
            f"SPYReturn_{h}d": np.nan,
            f"DirectionalGrossPnL_{h}d": np.nan,
            f"DirectionalExcessVsSPY_{h}d": np.nan,
            f"NetBase_{h}d": np.nan,
            f"NetStress_{h}d": np.nan,
        })
    return fields


def add_new_signal_rows(current: pd.DataFrame, ledger: pd.DataFrame, symbol_map: dict[str, str], spy_calendar: pd.DatetimeIndex, allow_new: bool, expected_signal_date: str = ""):
    if not allow_new:
        print("This run is for outcome updates/validation only; new signals will not be seeded.", flush=True)
        return ledger, 0, "new signal insertion disabled for this event"

    if current.empty:
        return ledger, 0, "latest scan is empty"

    current = current.copy()
    current["SignalDate"] = pd.to_datetime(current["SignalDate"], errors="coerce").dt.normalize()
    signal_dates = current["SignalDate"].dropna().drop_duplicates()
    if len(signal_dates) != 1:
        return ledger, 0, f"refusing inconsistent snapshot signal dates: {signal_dates.astype(str).tolist()}"
    signal_date = pd.Timestamp(signal_dates.iloc[0])
    if signal_date not in spy_calendar:
        return ledger, 0, f"refusing stale/unmatched snapshot: {date_text(signal_date)} is not in the SPY calendar"
    if date_text(signal_date) != date_text(spy_calendar[-1]):
        return ledger, 0, f"refusing stale snapshot: signal date {date_text(signal_date)} does not match latest market date {date_text(spy_calendar[-1])}"
    if expected_signal_date and date_text(signal_date) != date_text(expected_signal_date):
        return ledger, 0, f"refusing stale/non-session snapshot: signal date {date_text(signal_date)} does not match scheduled run date {date_text(expected_signal_date)}"

    current["SetupScore"] = pd.to_numeric(current["SetupScore"], errors="coerce")
    current["SignalRank"] = current.groupby("Direction")["SetupScore"].rank(method="first", ascending=False).astype("Int64")
    if "YahooSymbol" not in current.columns:
        current["YahooSymbol"] = current["Ticker"].astype(str).map(symbol_map).fillna(current["Ticker"].astype(str))

    existing = set()
    if not ledger.empty:
        existing = set(ledger.apply(lambda row: signal_key(row), axis=1))

    additions = []
    for _, row in current.iterrows():
        key = signal_key(row)
        if key in existing:
            continue
        record = row.to_dict()
        record["SignalDate"] = date_text(row["SignalDate"])
        record["Ticker"] = str(row["Ticker"])
        record["Direction"] = str(row["Direction"])
        record.update(quality_flags(row))
        record.update(blank_outcome_fields())
        additions.append(record)

    if additions:
        ledger = pd.concat([ledger, pd.DataFrame(additions)], ignore_index=True, sort=False)
    note = f"added {len(additions)} unique candidate events for signal date {date_text(signal_date)}"
    print(note, flush=True)
    return ledger, len(additions), note


def update_outcomes(ledger: pd.DataFrame, price_map: dict[str, pd.DataFrame], spy: pd.DataFrame):
    spy = normalize_dates(spy)
    calendar = pd.DatetimeIndex(spy.index).normalize().unique().sort_values()
    # The price downloader uses Yahoo tickers. Cache normalized frames by ticker for repeated lookup.
    normalized_prices = {}
    for ticker, frame in price_map.items():
        try:
            normalized_prices[str(ticker)] = normalize_dates(frame)
        except Exception:
            continue

    counts = {"complete": 0, "pending": 0, "invalid": 0}
    if ledger.empty:
        return ledger, counts

    for idx, row in ledger.iterrows():
        ticker = str(row.get("YahooSymbol", row.get("Ticker", "")))
        ticker = ticker if ticker and ticker.lower() != "nan" else str(row.get("Ticker", ""))
        stock = normalized_prices.get(ticker)
        if stock is None or stock.empty:
            counts["pending"] += 1
            continue

        signal_date = pd.Timestamp(row["SignalDate"]).normalize()
        if row.get("SignalBarStatus", "pending") in ("pending", "", np.nan) or pd.isna(row.get("SignalBarStatus")):
            if signal_date not in stock.index:
                # A missing download isn't the same as a confirmed stale bar; try on the next run.
                latest_stock_date = stock.index.max()
                if latest_stock_date < signal_date:
                    counts["pending"] += 1
                    continue
                ledger.at[idx, "SignalBarStatus"] = "invalid: no ticker bar on signal date"
            else:
                observed_close = float(stock.loc[signal_date, "Close"])
                signal_close = pd.to_numeric(pd.Series([row.get("SignalClose", row.get("Entry", np.nan))]), errors="coerce").iloc[0]
                if pd.notna(signal_close) and signal_close > 0 and abs(observed_close / float(signal_close) - 1.0) > 0.005:
                    ledger.at[idx, "SignalBarStatus"] = "invalid: signal close mismatch >0.5%"
                else:
                    ledger.at[idx, "SignalBarStatus"] = "valid"

        bar_status = str(ledger.at[idx, "SignalBarStatus"])
        if bar_status.startswith("invalid"):
            for h in HORIZONS:
                status_col = f"OutcomeStatus_{h}d"
                if str(ledger.at[idx, status_col]) in ("pending", "", "nan"):
                    ledger.at[idx, status_col] = "signal_invalid"
            ledger.at[idx, "TrackingStatus"] = "invalid_signal_data"
            counts["invalid"] += 1
            continue
        if bar_status != "valid":
            counts["pending"] += 1
            continue

        signal_pos = np.flatnonzero(calendar == signal_date)
        if len(signal_pos) == 0:
            for h in HORIZONS:
                if str(ledger.at[idx, f"OutcomeStatus_{h}d"]) == "pending":
                    ledger.at[idx, f"OutcomeStatus_{h}d"] = "invalid_market_calendar"
            ledger.at[idx, "TrackingStatus"] = "invalid_market_calendar"
            counts["invalid"] += 1
            continue

        signal_i = int(signal_pos[0])
        for horizon in HORIZONS:
            status_col = f"OutcomeStatus_{horizon}d"
            if str(ledger.at[idx, status_col]) == "complete":
                continue

            entry_date, exit_date, horizon_status = horizon_dates(calendar, signal_date, horizon)
            if horizon_status == "signal_date_not_in_market_calendar":
                ledger.at[idx, status_col] = "invalid_market_calendar"
                continue
            if horizon_status == "pending_future_exit":
                ledger.at[idx, status_col] = "pending"
                continue

            # If a bar is not available yet, keep it pending; if data has already advanced
            # past that expected session, mark it invalid instead of silently shifting dates.
            if entry_date not in stock.index or exit_date not in stock.index:
                last_stock_date = stock.index.max()
                if last_stock_date < exit_date:
                    ledger.at[idx, status_col] = "pending"
                else:
                    ledger.at[idx, status_col] = "invalid_missing_stock_bar"
                continue
            if entry_date not in spy.index or exit_date not in spy.index:
                ledger.at[idx, status_col] = "invalid_missing_spy_bar"
                continue

            try:
                outcome = calculate_outcome(stock, spy, entry_date, exit_date, str(row["Direction"]), horizon)
                for key, value in outcome.items():
                    if key in ("EntryDate", "EntryPrice"):
                        ledger.at[idx, key] = value
                    elif key.startswith("ExitDate_"):
                        ledger.at[idx, key] = value
                    elif key.startswith("ExitPrice_"):
                        ledger.at[idx, key] = value
                    elif key in (f"StockReturn_{horizon}d", f"SPYReturn_{horizon}d",
                                 f"DirectionalGrossPnL_{horizon}d", f"DirectionalExcessVsSPY_{horizon}d",
                                 f"NetBase_{horizon}d", f"NetStress_{horizon}d"):
                        ledger.at[idx, key] = value
                ledger.at[idx, f"ExitDate_{horizon}d"] = date_text(exit_date)
                ledger.at[idx, f"ExitPrice_{horizon}d"] = float(stock.loc[exit_date, "Close"])
                ledger.at[idx, status_col] = "complete"
                counts["complete"] += 1
            except Exception as exc:
                print(f"OUTCOME ERROR {ticker} {date_text(signal_date)} h={horizon}: {exc}", flush=True)
                ledger.at[idx, status_col] = "invalid_calculation"

        statuses = [str(ledger.at[idx, f"OutcomeStatus_{h}d"]) for h in HORIZONS]
        ledger.at[idx, "TrackingStatus"] = "all_horizons_complete" if all(s == "complete" for s in statuses) else (
            "partially_complete" if any(s == "complete" for s in statuses) else "tracking"
        )

    return ledger, counts


def make_daily_portfolios(ledger: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if ledger.empty:
        return pd.DataFrame()
    data = ledger.copy()
    data["SignalDate"] = pd.to_datetime(data["SignalDate"], errors="coerce")
    data["SetupScore"] = pd.to_numeric(data["SetupScore"], errors="coerce")
    data["QCFlagged"] = data["QCFlagged"].fillna(False).astype(bool)
    data["QCUnusualVolume"] = data["QCUnusualVolume"].fillna(False).astype(bool)

    # Rank using the full valid signal-date cohort first; never substitute a lower-ranked
    # name just because a higher-ranked name has not produced a valid outcome yet.
    signaled = data[data["SignalBarStatus"] == "valid"].copy()
    for horizon in HORIZONS:
        status_col = f"OutcomeStatus_{horizon}d"
        gross_col = f"DirectionalGrossPnL_{horizon}d"
        excess_col = f"DirectionalExcessVsSPY_{horizon}d"
        for (signal_date, direction), g in signaled.groupby(["SignalDate", "Direction"], sort=True):
            groups = {
                "All": g,
                "QC Clear": g[~g["QCFlagged"]],
                "QC Flagged": g[g["QCFlagged"]],
                "No Unusual Volume": g[~g["QCUnusualVolume"]],
                "Unusual Volume": g[g["QCUnusualVolume"]],
            }
            for group_name, subset in groups.items():
                ranked_subset = subset.sort_values(
                    ["SetupScore", "Ticker"], ascending=[False, True], na_position="last"
                )
                for top_k in TOP_KS:
                    selected = ranked_subset.head(top_k).copy()
                    if selected.empty:
                        # An empty filtered portfolio is cash: 0 return and zero slot coverage.
                        for cost_case in COSTS:
                            rows.append({
                                "SignalDate": date_text(signal_date),
                                "Direction": direction,
                                "QCGroup": group_name,
                                "TopK": top_k,
                                "Horizon": horizon,
                                "CostScenario": cost_case,
                                "NTrades": 0,
                                "TargetSlots": int(top_k),
                                "SlotCoverage": 0.0,
                                "SelectedSignals": 0,
                                "PendingOutcomeSlots": 0,
                                "MissingOutcomeSlots": 0,
                                "PortfolioStatus": "no_candidates",
                                "MeanDirectionalGrossPnL": 0.0,
                                "MeanDirectionalExcessVsSPY": 0.0,
                                "MeanNetPnL": 0.0,
                                "MeanNetPnLPerTrade": np.nan,
                                "GrossHitRate": np.nan,
                                "NetHitRate": np.nan,
                            })
                        continue

                    statuses = selected[status_col].fillna("pending").astype(str)
                    pending_mask = statuses.isin(["", "nan", "None", "pending"])
                    if pending_mask.any():
                        # Explicitly log pending portfolios so the dashboard can show data maturity.
                        # Pending rows have NaN returns and are excluded from performance estimates.
                        pending_count = int(pending_mask.sum())
                        for cost_case in COSTS:
                            rows.append({
                                "SignalDate": date_text(signal_date),
                                "Direction": direction,
                                "QCGroup": group_name,
                                "TopK": top_k,
                                "Horizon": horizon,
                                "CostScenario": cost_case,
                                "NTrades": 0,
                                "TargetSlots": int(top_k),
                                "SlotCoverage": np.nan,
                                "SelectedSignals": int(len(selected)),
                                "PendingOutcomeSlots": pending_count,
                                "MissingOutcomeSlots": 0,
                                "PortfolioStatus": "pending_outcomes",
                                "MeanDirectionalGrossPnL": np.nan,
                                "MeanDirectionalExcessVsSPY": np.nan,
                                "MeanNetPnL": np.nan,
                                "MeanNetPnLPerTrade": np.nan,
                                "GrossHitRate": np.nan,
                                "NetHitRate": np.nan,
                            })
                        continue

                    complete = statuses.eq("complete")
                    missing_count = int((~complete).sum())
                    completed_selected = selected.loc[complete].copy()
                    gross = pd.to_numeric(completed_selected[gross_col], errors="coerce")
                    excess = pd.to_numeric(completed_selected[excess_col], errors="coerce")
                    net_raw_by_case = {
                        cost_case: pd.to_numeric(
                            completed_selected[f"Net{cost_case}_{horizon}d"], errors="coerce"
                        )
                        for cost_case in COSTS
                    }
                    all_returns_valid = (
                        gross.notna().all()
                        and excess.notna().all()
                        and all(series.notna().all() for series in net_raw_by_case.values())
                    )
                    # A terminal data error is not treated as a zero-return cash position.
                    # We mark the whole portfolio-date unscorable instead.
                    is_scorable = missing_count == 0 and bool(all_returns_valid)
                    portfolio_status = "complete" if is_scorable else "unscorable_missing_outcome"
                    ntrades = int(len(completed_selected)) if is_scorable else 0
                    gross_portfolio = float(gross.sum() / top_k) if is_scorable and ntrades else (0.0 if is_scorable else np.nan)
                    excess_portfolio = float(excess.sum() / top_k) if is_scorable and ntrades else (0.0 if is_scorable else np.nan)
                    for cost_case, net_raw in net_raw_by_case.items():
                        net = net_raw
                        net_portfolio = float(net.sum() / top_k) if is_scorable and ntrades else (0.0 if is_scorable else np.nan)
                        rows.append({
                            "SignalDate": date_text(signal_date),
                            "Direction": direction,
                            "QCGroup": group_name,
                            "TopK": top_k,
                            "Horizon": horizon,
                            "CostScenario": cost_case,
                            "NTrades": ntrades,
                            "TargetSlots": int(top_k),
                            "SlotCoverage": float(ntrades / top_k) if is_scorable else np.nan,
                            "SelectedSignals": int(len(selected)),
                            "PendingOutcomeSlots": 0,
                            "MissingOutcomeSlots": missing_count,
                            "PortfolioStatus": portfolio_status,
                            "MeanDirectionalGrossPnL": gross_portfolio,
                            "MeanDirectionalExcessVsSPY": excess_portfolio,
                            "MeanNetPnL": net_portfolio,
                            "MeanNetPnLPerTrade": float(net.mean()) if is_scorable and ntrades else np.nan,
                            "GrossHitRate": float((gross > 0).mean()) if is_scorable and ntrades else np.nan,
                            "NetHitRate": float((net > 0).mean()) if is_scorable and ntrades else np.nan,
                        })
    return pd.DataFrame(rows)


def block_bootstrap_ci(values, reps=2500, block=BOOTSTRAP_BLOCK_SESSIONS, seed=42):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return np.nan, np.nan, np.nan
    observed_mean = float(np.mean(x))
    # Display descriptive means early, but do not fabricate a CI from fewer than two full blocks.
    if n < 2 * block:
        return observed_mean, np.nan, np.nan
    rng = np.random.default_rng(seed)
    estimates = np.empty(reps)
    blocks_needed = int(np.ceil(n / block))
    for i in range(reps):
        starts = rng.integers(0, n, size=blocks_needed)
        sample = np.concatenate([x[(s + np.arange(block)) % n] for s in starts])[:n]
        estimates[i] = np.mean(sample)
    return float(np.mean(x)), float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def summarize_daily_portfolios(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if daily.empty:
        return pd.DataFrame()
    keys = ["Direction", "QCGroup", "TopK", "Horizon", "CostScenario"]
    for key, g in daily.groupby(keys, sort=True):
        scoreable = g[pd.to_numeric(g["MeanNetPnL"], errors="coerce").notna()].copy()
        mean, lo, hi = block_bootstrap_ci(scoreable["MeanNetPnL"], seed=100 + int(key[3]))
        rows.append({
            **dict(zip(keys, key)),
            "SignalDates": int(g["SignalDate"].nunique()),
            "ScorableDates": int(scoreable["SignalDate"].nunique()),
            "UnscorableDates": int(g.loc[g["PortfolioStatus"] == "unscorable_missing_outcome", "SignalDate"].nunique()),
            "PendingDates": int(g.loc[g["PortfolioStatus"] == "pending_outcomes", "SignalDate"].nunique()),
            "PendingOutcomeSlots": int(g["PendingOutcomeSlots"].sum()),
            "MissingOutcomeSlots": int(g["MissingOutcomeSlots"].sum()),
            "TotalTrades": int(g["NTrades"].sum()),
            "MeanDailyGrossPnL": float(scoreable["MeanDirectionalGrossPnL"].mean()) if not scoreable.empty else np.nan,
            "MeanDailyExcessVsSPY": float(scoreable["MeanDirectionalExcessVsSPY"].mean()) if not scoreable.empty else np.nan,
            "MeanDailyNetPnL": mean,
            "MeanNetPnLPerTrade": float(scoreable["MeanNetPnLPerTrade"].mean()) if not scoreable.empty else np.nan,
            "NetCI95Low": lo,
            "NetCI95High": hi,
            "NetPositiveDatePct": float((scoreable["MeanNetPnL"] > 0).mean()) if not scoreable.empty else np.nan,
            "MeanSlotCoverage": float(scoreable["SlotCoverage"].mean()) if not scoreable.empty else np.nan,
            "CIStatus": "available" if np.isfinite(lo) else f"need >= {2 * BOOTSTRAP_BLOCK_SESSIONS} scorable dates for block CI",
        })
    return pd.DataFrame(rows)


def compare_group_to_all(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if daily.empty:
        return pd.DataFrame()
    for horizon in HORIZONS:
        primary = daily[(daily["Horizon"] == horizon) & (daily["TopK"] == 10)]
        for direction in ("Long", "Short"):
            for scenario in COSTS:
                base = primary[
                    (primary["Direction"] == direction)
                    & (primary["CostScenario"] == scenario)
                    & (primary["QCGroup"] == "All")
                ][["SignalDate", "MeanNetPnL"]].rename(columns={"MeanNetPnL": "AllNet"})
                for group in ("QC Clear", "QC Flagged"):
                    part = primary[
                        (primary["Direction"] == direction)
                        & (primary["CostScenario"] == scenario)
                        & (primary["QCGroup"] == group)
                    ][["SignalDate", "MeanNetPnL"]].rename(columns={"MeanNetPnL": "GroupNet"})
                    paired = base.merge(part, on="SignalDate", how="left")
                    # Missing rows are not assumed to be cash; only explicit no_candidates rows have 0.
                    paired = paired.dropna(subset=["AllNet", "GroupNet"])
                    differences = paired["GroupNet"] - paired["AllNet"]
                    diff, lo, hi = block_bootstrap_ci(
                        differences,
                        seed=500 + horizon + (0 if direction == "Long" else 10)
                        + (100 if scenario == "Stress" else 0)
                        + (1000 if group == "QC Flagged" else 0),
                    )
                    rows.append({
                        "Direction": direction,
                        "CostScenario": scenario,
                        "Horizon": horizon,
                        "Comparison": f"{group} minus All",
                        "PairedSignalDates": int(len(paired)),
                        "MeanNetPnLDifference": float(differences.mean()) if len(paired) else np.nan,
                        "DiffCI95Low": lo,
                        "DiffCI95High": hi,
                        "DifferencePositiveSupported": bool(np.isfinite(lo) and lo > 0),
                        "CIStatus": "available" if np.isfinite(lo) else f"need >= {2 * BOOTSTRAP_BLOCK_SESSIONS} paired scorable dates for block CI",
                    })
    return pd.DataFrame(rows)


def apply_evidence_gates(summary: pd.DataFrame, comparisons: pd.DataFrame):
    """Attach pre-declared, conservative evidence gates; never enables production."""
    def safe_int(value) -> int:
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return int(number) if pd.notna(number) else 0

    summary = summary.copy()
    comparisons = comparisons.copy()

    if not summary.empty:
        summary["MinimumDecisionDates"] = MIN_DECISION_DATES
        summary["DecisionStatus"] = "Collecting; minimum date count not met"
        keys = ["Direction", "QCGroup", "TopK", "Horizon"]
        for key, group in summary.groupby(keys, sort=False):
            row_indices = group.index
            scenarios = {str(row["CostScenario"]): row for _, row in group.iterrows()}
            minimum_dates = min(
                [safe_int(row.get("ScorableDates", 0)) for row in scenarios.values()]
                or [0]
            )
            if minimum_dates < MIN_DECISION_DATES:
                status = f"Collecting; need {MIN_DECISION_DATES} scorable dates under both cost cases"
            else:
                robust_positive = True
                for scenario in ("Base", "Stress"):
                    row = scenarios.get(scenario)
                    if row is None:
                        robust_positive = False
                        break
                    mean = pd.to_numeric(pd.Series([row.get("MeanDailyNetPnL")]), errors="coerce").iloc[0]
                    low = pd.to_numeric(pd.Series([row.get("NetCI95Low")]), errors="coerce").iloc[0]
                    if not (pd.notna(mean) and pd.notna(low) and mean > 0 and low > 0):
                        robust_positive = False
                        break
                status = (
                    "Positive under both cost cases; still research-only and requires independent replication"
                    if robust_positive
                    else "No confirmed positive net edge under both cost cases"
                )
            summary.loc[row_indices, "DecisionStatus"] = status

    if not comparisons.empty:
        comparisons["MinimumPairedDates"] = MIN_DECISION_DATES
        comparisons["FilterEvidenceStatus"] = "Collecting; minimum paired-date count not met"
        keys = ["Direction", "Horizon", "Comparison"]
        for key, group in comparisons.groupby(keys, sort=False):
            row_indices = group.index
            scenarios = {str(row["CostScenario"]): row for _, row in group.iterrows()}
            minimum_dates = min(
                [safe_int(row.get("PairedSignalDates", 0)) for row in scenarios.values()]
                or [0]
            )
            if minimum_dates < MIN_DECISION_DATES:
                status = f"Collecting; need {MIN_DECISION_DATES} paired dates under both cost cases"
            else:
                robust_difference = True
                for scenario in ("Base", "Stress"):
                    row = scenarios.get(scenario)
                    if row is None:
                        robust_difference = False
                        break
                    low = pd.to_numeric(pd.Series([row.get("DiffCI95Low")]), errors="coerce").iloc[0]
                    if not (pd.notna(low) and low > 0):
                        robust_difference = False
                        break
                status = (
                    "Positive difference under both cost cases; still research-only"
                    if robust_difference
                    else "QC-group difference not established under both cost cases"
                )
            comparisons.loc[row_indices, "FilterEvidenceStatus"] = status

    return summary, comparisons


def main():
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "artifacts" / "shadow_test_v16"
    out_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = LEDGER_DIR / "shadow_ledger.csv"
    portfolios_path = LEDGER_DIR / "shadow_daily_portfolios.csv"
    summary_path = LEDGER_DIR / "shadow_group_summary.csv"
    comparisons_path = LEDGER_DIR / "shadow_qc_comparisons.csv"
    status_path = ROOT / "artifacts" / "shadow_test_status.json"

    ledger = pd.read_csv(ledger_path) if ledger_path.exists() else pd.DataFrame()
    current = pd.read_parquet(data_dir / "latest_scan.parquet") if (data_dir / "latest_scan.parquet").exists() else pd.DataFrame()
    if not current.empty and not {"SignalDate", "Ticker", "Direction", "SetupScore", "SignalClose"}.issubset(current.columns):
        raise RuntimeError("Latest scan is missing required fields; refusing to start shadow tracking.")

    spy_map = download_daily(["SPY"], period="1y", batch_size=1, sleep_seconds=0.1)
    spy = spy_map.get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY download failed; refusing to update shadow outcomes.")
    spy = normalize_dates(spy)
    calendar = pd.DatetimeIndex(spy.index).normalize().unique().sort_values()

    try:
        current_universe = fetch_us_universe(data_dir / "universe.csv", refresh=True)
        symbol_map = dict(zip(current_universe["Symbol"].astype(str), current_universe["YahooSymbol"].astype(str)))
        print(f"Refreshed Yahoo ticker mapping for {len(symbol_map)} listed symbols.", flush=True)
    except Exception as exc:
        symbol_map = load_universe_mapping(data_dir / "universe.csv")
        print(f"Universe refresh failed; using cached ticker mapping ({type(exc).__name__}: {exc}).", flush=True)
    # Only download stocks with incomplete outcomes plus today's candidates if the event allows a new signal.
    allow_new = os.getenv("SHADOW_ADD_NEW_SIGNALS", "false").lower() == "true"
    expected_signal_date = os.getenv("SHADOW_EXPECTED_SIGNAL_DATE", "").strip()
    required_tickers = set()
    if not ledger.empty:
        pending_mask = ledger[OUTCOME_STATUS_COLUMNS].astype(str).apply(lambda col: col.ne("complete"), axis=0).any(axis=1)
        required_tickers.update(ledger.loc[pending_mask, "YahooSymbol"].dropna().astype(str).tolist())
    if allow_new and not current.empty:
        mapped = current["Ticker"].astype(str).map(symbol_map).fillna(current["Ticker"].astype(str))
        required_tickers.update(mapped.tolist())
    required_tickers.discard("")
    required_tickers.discard("nan")
    print(f"Shadow tracker: {len(ledger)} existing events; {len(required_tickers)} stock tickers to refresh; allow_new={allow_new}", flush=True)

    price_map = download_daily(sorted(required_tickers), period="1y", batch_size=50, sleep_seconds=0.5) if required_tickers else {}
    print(f"Shadow tracker downloaded {len(price_map)} / {len(required_tickers)} required stocks.", flush=True)

    ledger, added, seed_note = add_new_signal_rows(current, ledger, symbol_map, calendar, allow_new, expected_signal_date)
    if not ledger.empty:
        # Make CSV-created booleans robust and default old rows safely.
        for col in ("QCFlagged", "QCUnusualVolume", "QCCountertrend"):
            if col not in ledger.columns:
                ledger[col] = False
            ledger[col] = ledger[col].map(lambda x: str(x).lower() in ("true", "1", "yes") if pd.notna(x) else False)
        ledger, outcome_counts = update_outcomes(ledger, price_map, spy)
    else:
        outcome_counts = {"complete": 0, "pending": 0, "invalid": 0}

    daily = make_daily_portfolios(ledger)
    summary = summarize_daily_portfolios(daily)
    comparisons = compare_group_to_all(daily)
    summary, comparisons = apply_evidence_gates(summary, comparisons)

    if not ledger.empty:
        ledger = ledger.sort_values(["SignalDate", "Direction", "SignalRank", "Ticker"], ascending=[True, True, True, True])
        ledger["SignalDate"] = pd.to_datetime(ledger["SignalDate"]).dt.strftime("%Y-%m-%d")
        ledger.to_csv(ledger_path, index=False)
    daily.to_csv(portfolios_path, index=False)
    summary.to_csv(summary_path, index=False)
    comparisons.to_csv(comparisons_path, index=False)

    outcome_status_counts = {}
    for horizon in HORIZONS:
        column = f"OutcomeStatus_{horizon}d"
        values = ledger[column].fillna("pending").astype(str) if not ledger.empty and column in ledger.columns else pd.Series(dtype=str)
        complete_count = int(values.eq("complete").sum())
        pending_count = int(values.isin(["", "nan", "None", "pending"]).sum())
        invalid_count = int((~values.isin(["", "nan", "None", "pending", "complete"])).sum())
        outcome_status_counts[str(horizon)] = {
            "complete": complete_count,
            "pending": pending_count,
            "invalid": invalid_count,
        }

    qc_counts = {
        "clear": int((~ledger["QCFlagged"].astype(bool)).sum()) if not ledger.empty else 0,
        "flagged": int(ledger["QCFlagged"].astype(bool).sum()) if not ledger.empty else 0,
        "unusual_volume": int(ledger["QCUnusualVolume"].astype(bool).sum()) if not ledger.empty else 0,
        "countertrend": int(ledger["QCCountertrend"].astype(bool).sum()) if not ledger.empty else 0,
    }

    status = {
        "status": "success",
        "research_version": "V16 Prospective QC Shadow Test",
        "research_only": True,
        "production_ready": False,
        "new_signals_added_this_run": int(added),
        "signal_seed_note": seed_note,
        "allow_new_signals": allow_new,
        "ledger_events": int(len(ledger)),
        "unique_signal_dates": int(ledger["SignalDate"].nunique()) if not ledger.empty else 0,
        "long_events": int(ledger["Direction"].eq("Long").sum()) if not ledger.empty else 0,
        "short_events": int(ledger["Direction"].eq("Short").sum()) if not ledger.empty else 0,
        "valid_signal_bars": int(ledger["SignalBarStatus"].eq("valid").sum()) if not ledger.empty and "SignalBarStatus" in ledger.columns else 0,
        "invalid_signal_bars": int(ledger["SignalBarStatus"].astype(str).str.startswith("invalid").sum()) if not ledger.empty and "SignalBarStatus" in ledger.columns else 0,
        "completed_1d_outcomes": outcome_status_counts["1"]["complete"],
        "pending_1d_outcomes": outcome_status_counts["1"]["pending"],
        "invalid_1d_outcomes": outcome_status_counts["1"]["invalid"],
        "completed_3d_outcomes": outcome_status_counts["3"]["complete"],
        "pending_3d_outcomes": outcome_status_counts["3"]["pending"],
        "invalid_3d_outcomes": outcome_status_counts["3"]["invalid"],
        "completed_5d_outcomes": outcome_status_counts["5"]["complete"],
        "pending_5d_outcomes": outcome_status_counts["5"]["pending"],
        "invalid_5d_outcomes": outcome_status_counts["5"]["invalid"],
        "completed_10d_outcomes": outcome_status_counts["10"]["complete"],
        "pending_10d_outcomes": outcome_status_counts["10"]["pending"],
        "invalid_10d_outcomes": outcome_status_counts["10"]["invalid"],
        "outcome_status_counts_by_horizon": outcome_status_counts,
        "qc_event_counts": qc_counts,
        "outcome_update_counts_this_run": outcome_counts,
        "latest_market_date": date_text(calendar[-1]),
        "cost_assumptions": COSTS,
        "execution_convention": "EOD signal; entry next SPY session open; exit close of the h-th session after the signal; all outcomes require exact stock bars on entry/exit dates and are benchmarked over the matched SPY window.",
        "portfolio_return_convention": "Returns are normalized by target slots; unused slots are cash at 0%. Portfolios are selected from the full valid signal cohort before checking outcome maturity. Any terminal missing/invalid outcome in selected slots makes that portfolio-date unscorable rather than substituting lower-ranked names.",
        "group_summary_rows": int(len(summary)),
        "comparison_rows": int(len(comparisons)),
        "minimum_signal_dates_for_decision": MIN_DECISION_DATES,
        "bootstrap_block_sessions": BOOTSTRAP_BLOCK_SESSIONS,
        "decision_gate": "For a strategy group, require >=200 scorable signal dates and a positive mean net return with the 95% 10-session block-bootstrap CI entirely above zero under both Base and Stress costs. QC-filter improvement requires >=200 paired scorable dates and the paired-difference CI lower bound above zero under both cost cases. Passing this gate is still not approval to trade.",
        "thresholds_or_scores_changed": False,
        "limitations": [
            "This is prospective shadow tracking, not a live strategy or recommendation.",
            "New signal rows are inserted only after a successful Live Daily Scan workflow run and only when the snapshot date matches the latest SPY date.",
            "Transaction-cost and short-borrow inputs are fixed assumptions, not broker-specific actual costs.",
            "No look-ahead is intended: outcomes become complete only after the specified exit close is present.",
            "Missing/stale stock bars are flagged and not silently shifted to a different trading date.",
            "Current-universe membership and source-data limitations still prevent a fully survivorship-free record."
        ],
        "data_files": [
            "data/shadow/shadow_ledger.csv",
            "data/shadow/shadow_daily_portfolios.csv",
            "data/shadow/shadow_group_summary.csv",
            "data/shadow/shadow_qc_comparisons.csv"
        ]
    }
    status_path.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    (out_dir / "status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    print(json.dumps(status, indent=2, default=str), flush=True)
    if not summary.empty:
        primary = summary[(summary["TopK"] == 10) & (summary["Horizon"].isin([1, 3, 5, 10])) & (summary["CostScenario"] == "Base")]
        print("\\nCURRENT PROSPECTIVE SHADOW SUMMARY (descriptive until enough dates accumulate)\\n", flush=True)
        print(primary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
