from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.config import ROOT
from scanner.data import download_daily
from scanner.universe import fetch_us_universe


HOLDING_DAYS = 10
WINDOWS = [
    ("2025-H1", "2025-01-01", "2025-06-30"),
    ("2025-H2", "2025-07-01", "2025-12-31"),
    ("2026-H1", "2026-01-01", "2026-06-30"),
    ("2026-H2", "2026-07-01", "2026-12-31"),
]

# All variants are predefined before looking at V4 results. No parameter tuning
# is performed on the holdout. The goal is to test trade mechanics, not fit a
# more complex model.
VARIANTS = {
    "Baseline_Reanchored": {
        "kind": "reanchored_signal_risk",
    },
    "Fixed_Signal_Levels": {
        "kind": "fixed_signal_levels",
    },
    "Entry_ATR_1.0x": {
        "kind": "entry_atr",
        "atr_mult": 1.0,
    },
    "Entry_ATR_1.25x": {
        "kind": "entry_atr",
        "atr_mult": 1.25,
    },
    "Entry_ATR_1.5x": {
        "kind": "entry_atr",
        "atr_mult": 1.5,
    },
    "Gap_Guard_2pct": {
        "kind": "gap_guard",
        "gap_threshold": 0.02,
    },
    "Gap_Guard_5pct": {
        "kind": "gap_guard",
        "gap_threshold": 0.05,
    },
    "Signal_Close_Benchmark": {
        "kind": "signal_close",
    },
}


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return np.nan


def _direction_sign(direction: str) -> int:
    return 1 if direction == "Long" else -1


def _trade_levels(s: pd.Series, entry: float, variant: dict) -> tuple[float, float, float]:
    direction = str(s["Direction"])
    sign = _direction_sign(direction)
    close = _safe_float(s["CloseSignal"])
    signal_stop = _safe_float(s["Stop"])
    signal_target = _safe_float(s["Target2R"])
    atr = _safe_float(s["ATR20"])

    if variant["kind"] == "reanchored_signal_risk":
        risk = abs(close - signal_stop)
        stop = entry - sign * risk
        target = entry + sign * 2.0 * risk
    elif variant["kind"] == "fixed_signal_levels":
        stop = signal_stop
        target = signal_target
        risk = abs(close - stop)
    elif variant["kind"] == "entry_atr":
        risk = abs(atr * float(variant["atr_mult"]))
        stop = entry - sign * risk
        target = entry + sign * 2.0 * risk
    elif variant["kind"] == "signal_close":
        risk = abs(close - signal_stop)
        stop = close - sign * risk
        target = close + sign * 2.0 * risk
        entry = close
    else:
        raise ValueError(f"Unknown variant {variant}")

    if not np.isfinite(entry) or not np.isfinite(stop) or not np.isfinite(target):
        return np.nan, np.nan, np.nan
    if not np.isfinite(risk) or risk <= 0:
        return np.nan, np.nan, np.nan
    return entry, stop, target


def _evaluate_trade(prices: pd.DataFrame, signal_idx: int, s: pd.Series,
                    variant: dict) -> tuple[int, float, str]:
    direction = str(s["Direction"])
    sign = _direction_sign(direction)
    next_idx = signal_idx + 1
    if next_idx >= len(prices):
        return 0, np.nan, "no_next_bar"

    signal_close = _safe_float(prices.iloc[signal_idx]["Close"])
    next_open = _safe_float(prices.iloc[next_idx]["Open"])
    if not np.isfinite(signal_close) or not np.isfinite(next_open):
        return 0, np.nan, "bad_price"

    gap = next_open / signal_close - 1.0
    threshold = variant.get("gap_threshold")
    if variant["kind"] == "gap_guard" and threshold is not None:
        adverse = (-gap if direction == "Long" else gap)
        if adverse > float(threshold):
            return 0, np.nan, "gap_filtered"

    entry = signal_close if variant["kind"] == "signal_close" else next_open
    # Re-use a local copy with the ATR/risk data expected by _trade_levels.
    s2 = s.copy()
    s2["CloseSignal"] = signal_close
    entry, stop, target = _trade_levels(s2, entry, variant)
    if not np.isfinite(entry) or not np.isfinite(stop) or not np.isfinite(target):
        return 0, np.nan, "invalid_levels"

    # For the signal-close benchmark, trading starts after the signal close.
    start_idx = next_idx
    end_idx = min(start_idx + HOLDING_DAYS, len(prices))
    future = prices.iloc[start_idx:end_idx]
    if future.empty:
        return 0, np.nan, "no_future_bars"

    # Opening gaps through a level are handled explicitly.
    if direction == "Long":
        if entry >= target:
            return 1, (entry - signal_close) / max(abs(signal_close - stop), 1e-12), "target_at_open"
        if entry <= stop:
            return 0, (entry - signal_close) / max(abs(signal_close - stop), 1e-12), "stop_at_open"
    else:
        if entry <= target:
            return 1, (signal_close - entry) / max(abs(signal_close - stop), 1e-12), "target_at_open"
        if entry >= stop:
            return 0, (signal_close - entry) / max(abs(signal_close - stop), 1e-12), "stop_at_open"

    risk = abs(entry - stop)
    if risk <= 0:
        return 0, np.nan, "zero_risk"

    for _, bar in future.iterrows():
        high = _safe_float(bar["High"])
        low = _safe_float(bar["Low"])
        if not np.isfinite(high) or not np.isfinite(low):
            continue

        if direction == "Long":
            stop_hit = low <= stop
            target_hit = high >= target
            if stop_hit and target_hit:
                return 0, -1.0, "same_bar_ambiguous"
            if target_hit:
                return 1, 2.0, "target"
            if stop_hit:
                return 0, -1.0, "stop"
        else:
            stop_hit = high >= stop
            target_hit = low <= target
            if stop_hit and target_hit:
                return 0, -1.0, "same_bar_ambiguous"
            if target_hit:
                return 1, 2.0, "target"
            if stop_hit:
                return 0, -1.0, "stop"

    return 0, -1.0, "timeout"


def _load_prices(signals: pd.DataFrame, period: str) -> dict[str, pd.DataFrame]:
    symbols = sorted(signals["Ticker"].dropna().astype(str).unique().tolist())
    batch_size = int(os.getenv("SCANNER_BATCH_SIZE", "50"))
    return download_daily(symbols, period=period, batch_size=batch_size, sleep_seconds=0.75)


def _prepare_signals(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["SignalDate"] = pd.to_datetime(d["SignalDate"], errors="coerce")
    d = d.dropna(subset=["SignalDate", "Ticker", "Direction", "Setup"]).copy()

    # Save the signal-date close so that V4 explicitly distinguishes it from
    # the actual next-day entry open.
    if "Entry" in d.columns:
        d["CloseSignal"] = pd.to_numeric(d["Entry"], errors="coerce")
    else:
        d["CloseSignal"] = np.nan

    # Reconstruct ATR20 from ATR20Pct x signal close when ATR20 itself is not
    # stored in the training parquet.
    d["ATR20"] = pd.to_numeric(d.get("ATR20Pct", np.nan), errors="coerce") * d["CloseSignal"]
    d["Stop"] = pd.to_numeric(d["Stop"], errors="coerce")
    d["Target2R"] = pd.to_numeric(d["Target2R"], errors="coerce")
    d["GapPct"] = pd.to_numeric(d.get("GapPct", np.nan), errors="coerce")
    return d.sort_values(["SignalDate", "Ticker"]).reset_index(drop=True)


def _run(signals: pd.DataFrame, prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict] = []
    for _, s in signals.iterrows():
        px = prices.get(str(s["Ticker"]))
        if px is None or px.empty:
            continue
        px = px.sort_index()
        try:
            idx = px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError:
            continue
        if isinstance(idx, slice) or idx < 0 or idx + 1 >= len(px):
            continue

        signal_close = _safe_float(px.iloc[idx]["Close"])
        next_open = _safe_float(px.iloc[idx + 1]["Open"])
        if not np.isfinite(signal_close) or not np.isfinite(next_open):
            continue

        gap = next_open / signal_close - 1.0
        adverse_gap = -gap if s["Direction"] == "Long" else gap

        for name, variant in VARIANTS.items():
            outcome, realized_r, reason = _evaluate_trade(px, int(idx), s, variant)
            rows.append({
                "Ticker": s["Ticker"],
                "SignalDate": s["SignalDate"],
                "Direction": s["Direction"],
                "Setup": s["Setup"],
                "SetupScore": s.get("SetupScore", np.nan),
                "Variant": name,
                "Outcome": outcome,
                "RealizedR": realized_r,
                "Reason": reason,
                "GapPctActual": gap,
                "AdverseGapPct": adverse_gap,
                "MarketRegime": s.get("MarketRegime", "Unknown"),
            })
    return pd.DataFrame(rows)


def _summary(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    def agg(g):
        n = len(g)
        return pd.Series({
            "N": n,
            "SuccessRate": float(g["Outcome"].mean()) if n else np.nan,
            "BinaryExpectancyR": float(3.0 * g["Outcome"].mean() - 1.0) if n else np.nan,
            "MeanRealizedR": float(g["RealizedR"].mean()) if n else np.nan,
            "MedianRealizedR": float(g["RealizedR"].median()) if n else np.nan,
            "GapFiltered": int((g["Reason"] == "gap_filtered").sum()),
            "Timeouts": int((g["Reason"] == "timeout").sum()),
        })
    return df.groupby(by, dropna=False).apply(agg, include_groups=False).reset_index()


def _window_label(ts: pd.Timestamp) -> str:
    for name, start, end in WINDOWS:
        if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
            return name
    return "pre-2025"


def main() -> None:
    in_path = ROOT / "data" / "training_signals.parquet"
    if not in_path.exists():
        raise FileNotFoundError(in_path)

    signals = _prepare_signals(pd.read_parquet(in_path))
    if signals.empty:
        raise ValueError("No usable training signals")

    # 3y is enough for this mechanics study and matches the V2/V3 validation
    # window, making changes attributable to execution mechanics rather than
    # a different sample.
    prices = _load_prices(signals, os.getenv("SCANNER_PERIOD", "3y"))
    result = _run(signals, prices)
    if result.empty:
        raise ValueError("No mechanics outcomes were produced")

    out = ROOT / "artifacts" / "trade_mechanics_v4"
    out.mkdir(parents=True, exist_ok=True)

    result["Window"] = result["SignalDate"].map(_window_label)

    overall = _summary(result, ["Variant", "Direction"])
    by_setup = _summary(result, ["Variant", "Direction", "Setup"])
    by_window = _summary(result, ["Variant", "Direction", "Window"])
    by_regime = _summary(result, ["Variant", "Direction", "MarketRegime"])

    gap_bins = [-np.inf, -0.05, -0.02, 0.0, 0.02, 0.05, np.inf]
    gap_labels = ["<-5%", "-5% to -2%", "-2% to 0%", "0% to 2%", "2% to 5%", ">5%"]
    # Adverse gap is already direction-normalized.
    result["AdverseGapBin"] = pd.cut(
        result["AdverseGapPct"], bins=gap_bins, labels=gap_labels, include_lowest=True
    )
    gap_summary = _summary(result, ["Direction", "AdverseGapBin"])

    # Robustness screen is descriptive only: no variant parameters were tuned
    # here. A "survivor" means positive mean realized R in >=3 of 4 named
    # calendar windows and >33.3% success in the combined 2025+ sample.
    w = by_window.copy()
    score_rows = []
    for (variant, direction), g in overall.groupby(["Variant", "Direction"]):
        wg = w[(w["Variant"] == variant) & (w["Direction"] == direction)]
        named = wg[wg["Window"].isin([x[0] for x in WINDOWS])]
        positive_windows = int((named["MeanRealizedR"] > 0).sum())
        n_windows = int(len(named))
        survivor = bool(
            n_windows >= 3
            and positive_windows >= 3
            and float(g["SuccessRate"].iloc[0]) > (1.0 / 3.0)
            and float(g["MeanRealizedR"].iloc[0]) > 0
        )
        score_rows.append({
            "Variant": variant,
            "Direction": direction,
            "PositiveWindows": positive_windows,
            "WindowsObserved": n_windows,
            "OverallSuccessRate": float(g["SuccessRate"].iloc[0]),
            "OverallMeanRealizedR": float(g["MeanRealizedR"].iloc[0]),
            "RobustSurvivor": survivor,
        })
    robustness = pd.DataFrame(score_rows).sort_values(
        ["RobustSurvivor", "OverallMeanRealizedR", "OverallSuccessRate"],
        ascending=[False, False, False],
    )

    summary = {
        "signals_input": int(len(signals)),
        "tickers_with_signals": int(signals["Ticker"].nunique()),
        "mechanics_rows": int(len(result)),
        "price_tickers_downloaded": int(len(prices)),
        "windows": [x[0] for x in WINDOWS],
        "success_threshold_for_zero_binary_expectancy": 1.0 / 3.0,
        "robust_survivor_definition": "positive mean realized R in >=3 of 4 calendar windows, combined success >33.3%, combined mean realized R >0",
        "note": "All V4 variants were predefined before inspecting results. This study is execution-mechanics sensitivity, not model fitting. Daily OHLC ambiguity remains conservative.",
    }

    overall.to_csv(out / "overall.csv", index=False)
    by_setup.to_csv(out / "by_setup.csv", index=False)
    by_window.to_csv(out / "by_window.csv", index=False)
    by_regime.to_csv(out / "by_regime.csv", index=False)
    gap_summary.to_csv(out / "by_adverse_gap.csv", index=False)
    robustness.to_csv(out / "robustness_screen.csv", index=False)
    result.to_parquet(out / "trade_mechanics_rows.parquet", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print("\nROBUSTNESS SCREEN")
    print(robustness.to_string(index=False))
    print("\nOVERALL")
    print(
        overall.sort_values(["Direction", "MeanRealizedR"], ascending=[True, False]).to_string(index=False)
    )


if __name__ == "__main__":
    main()
