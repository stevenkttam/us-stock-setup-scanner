from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from scanner.config import ROOT
from scanner.data import download_daily


HORIZONS = [3, 5, 10]
TARGET_R = [0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 2.50, 3.00]


def f(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def prep(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["SignalDate"] = pd.to_datetime(d["SignalDate"], errors="coerce")
    d = d.dropna(subset=["SignalDate", "Ticker", "Direction", "Setup"])
    d["CloseSignal"] = pd.to_numeric(d["Entry"], errors="coerce")
    d["ATR20"] = pd.to_numeric(d["ATR20Pct"], errors="coerce") * d["CloseSignal"]
    d["SignalRisk"] = abs(pd.to_numeric(d["Entry"], errors="coerce") - pd.to_numeric(d["Stop"], errors="coerce"))
    return d.reset_index(drop=True)


def evaluate(px: pd.DataFrame, idx: int, direction: str, risk: float, horizon: int, target_r: float):
    if idx + 1 >= len(px) or not np.isfinite(risk) or risk <= 0:
        return 0, np.nan, np.nan, "invalid"

    entry = f(px.iloc[idx + 1]["Open"])
    if not np.isfinite(entry):
        return 0, np.nan, np.nan, "bad_open"

    end = min(idx + 1 + horizon, len(px))
    future = px.iloc[idx + 1:end]
    if future.empty:
        return 0, np.nan, np.nan, "no_future"

    stop = entry - risk if direction == "Long" else entry + risk
    target = entry + target_r * risk if direction == "Long" else entry - target_r * risk

    mfe = 0.0
    mae = 0.0
    mfe_bar = None

    for bar_no, (_, bar) in enumerate(future.iterrows(), start=1):
        high, low = f(bar["High"]), f(bar["Low"])
        if not np.isfinite(high) or not np.isfinite(low):
            continue

        if direction == "Long":
            fav = (high - entry) / risk
            adv = (entry - low) / risk
            target_hit = high >= target
            stop_hit = low <= stop
        else:
            fav = (entry - low) / risk
            adv = (high - entry) / risk
            target_hit = low <= target
            stop_hit = high >= stop

        if fav > mfe:
            mfe = fav
            mfe_bar = bar_no
        if adv > mae:
            mae = adv

        if target_hit and stop_hit:
            return 0, mfe, -mae, "same_bar_ambiguous"
        if target_hit:
            return 1, mfe, -mae, f"target_{bar_no}"
        if stop_hit:
            return 0, mfe, -mae, f"stop_{bar_no}"

    return 0, mfe, -mae, "timeout"


def hit_before_stop(px, idx, direction, risk, horizon, target_r):
    outcome, mfe, mae, reason = evaluate(px, idx, direction, risk, horizon, target_r)
    return outcome, reason


def main():
    inp = ROOT / "data" / "training_signals.parquet"
    d = prep(pd.read_parquet(inp))
    if d.empty:
        raise ValueError("No signals")

    symbols = sorted(d["Ticker"].astype(str).unique())
    prices = download_daily(
        symbols,
        period=os.getenv("SCANNER_PERIOD", "3y"),
        batch_size=int(os.getenv("SCANNER_BATCH_SIZE", "50")),
        sleep_seconds=0.75,
    )

    mfe_rows = []
    target_rows = []
    for _, s in d.iterrows():
        px = prices.get(str(s["Ticker"]))
        if px is None or px.empty:
            continue
        px = px.sort_index()
        try:
            idx = px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError:
            continue
        if isinstance(idx, slice):
            continue

        signal_risk = f(s["SignalRisk"])
        atr_risk = f(s["ATR20"])
        if not np.isfinite(signal_risk) or signal_risk <= 0:
            continue
        if not np.isfinite(atr_risk) or atr_risk <= 0:
            continue

        for risk_name, risk in [("SignalRisk", signal_risk), ("ATR1x", atr_risk)]:
            for horizon in HORIZONS:
                # MFE/MAE diagnostics use a 1R target only as a neutral
                # reference; the full target grid below is evaluated separately.
                outcome, mfe, mae, reason = evaluate(
                    px, int(idx), str(s["Direction"]), risk, horizon, 1.0
                )
                mfe_rows.append({
                    "Ticker": s["Ticker"],
                    "SignalDate": s["SignalDate"],
                    "Direction": s["Direction"],
                    "Setup": s["Setup"],
                    "SetupScore": s.get("SetupScore", np.nan),
                    "RiskModel": risk_name,
                    "HorizonDays": horizon,
                    "MFE_R": mfe,
                    "MAE_R": mae,
                    "Hit1RBeforeStop": outcome,
                    "Reason1R": reason,
                })

                for target_r in TARGET_R:
                    outcome, mfe2, mae2, reason2 = evaluate(
                        px, int(idx), str(s["Direction"]), risk, horizon, target_r
                    )
                    target_rows.append({
                        "Ticker": s["Ticker"],
                        "SignalDate": s["SignalDate"],
                        "Direction": s["Direction"],
                        "Setup": s["Setup"],
                        "SetupScore": s.get("SetupScore", np.nan),
                        "RiskModel": risk_name,
                        "HorizonDays": horizon,
                        "TargetR": target_r,
                        "Outcome": outcome,
                        "Reason": reason2,
                    })

    out = ROOT / "artifacts" / "outcome_diagnostics_v5"
    out.mkdir(parents=True, exist_ok=True)
    mfe = pd.DataFrame(mfe_rows)
    targets = pd.DataFrame(target_rows)
    if mfe.empty or targets.empty:
        raise ValueError("No diagnostic rows")
    mfe_summary = (
        mfe.groupby(["Direction", "Setup", "RiskModel", "HorizonDays"], dropna=False)
        .agg(
            N=("MFE_R", "size"),
            MedianMFE_R=("MFE_R", "median"),
            P75MFE_R=("MFE_R", lambda x: x.quantile(0.75)),
            P90MFE_R=("MFE_R", lambda x: x.quantile(0.90)),
            MeanMAE_R=("MAE_R", "mean"),
            MedianMAE_R=("MAE_R", "median"),
            Hit1RRate=("Hit1RBeforeStop", "mean"),
        )
        .reset_index()
    )

    target_summary = (
        targets.groupby(["Direction", "Setup", "RiskModel", "HorizonDays", "TargetR"], dropna=False)
        .agg(
            N=("Outcome", "size"),
            SuccessRate=("Outcome", "mean"),
        )
        .reset_index()
    )
    target_summary["BinaryExpectancyR"] = target_summary["SuccessRate"] * (
        1 + target_summary["TargetR"]
    ) - 1.0

    # Direction-level diagnostic, kept separate from setup-specific detail.
    target_direction = (
        targets.groupby(["Direction", "RiskModel", "HorizonDays", "TargetR"], dropna=False)
        .agg(N=("Outcome", "size"), SuccessRate=("Outcome", "mean"))
        .reset_index()
    )
    target_direction["BinaryExpectancyR"] = target_direction["SuccessRate"] * (
        1 + target_direction["TargetR"]
    ) - 1.0

    summary = {
        "signals_input": int(len(d)),
        "tickers_with_signals": int(d["Ticker"].nunique()),
        "price_tickers_downloaded": int(len(prices)),
        "mfe_mae_horizons": HORIZONS,
        "target_grid_R": TARGET_R,
        "risk_models": ["SignalRisk", "ATR1x"],
        "note": "Diagnostic study only. No parameter is selected for production here. Same conservative daily OHLC ambiguity rule is used.",
    }

    mfe_summary.to_csv(out / "mfe_mae_summary.csv", index=False)
    target_summary.to_csv(out / "target_horizon_by_setup.csv", index=False)
    target_direction.to_csv(out / "target_horizon_direction.csv", index=False)
    mfe.to_parquet(out / "mfe_mae_rows.parquet", index=False)
    targets.to_parquet(out / "target_rows.parquet", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print("\nDIRECTION TARGET DIAGNOSTIC")
    print(target_direction.to_string(index=False))


if __name__ == "__main__":
    main()
