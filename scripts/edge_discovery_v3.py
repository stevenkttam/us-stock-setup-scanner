from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.config import ROOT

BIN_SPECS = {
    "RVOL20": [-np.inf, 1.5, 2.0, 3.0, np.inf],
    "BreakoutATR": [-np.inf, 0.5, 1.0, 1.5, 2.0, np.inf],
    "ATRCompression": [-np.inf, 0.10, 0.20, 0.30, 0.40, np.inf],
    "Trend20": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "Trend50": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "Trend200": [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
    "RS_SPY_20": [-np.inf, -0.05, 0.0, 0.05, 0.10, np.inf],
    "TR_ATR20": [-np.inf, 1.0, 1.5, 2.0, 3.0, np.inf],
    "CloseLocation": [-np.inf, 0.35, 0.50, 0.65, 0.80, np.inf],
    "BaseRangePct": [-np.inf, 0.04, 0.06, 0.08, 0.10, 0.12, np.inf],
    "ATR20Pct": [-np.inf, 0.01, 0.02, 0.04, 0.06, 0.10, np.inf],
    "RiskATR": [-np.inf, 0.50, 0.75, 1.00, 1.25, np.inf],
    "GapPct": [-np.inf, -0.05, -0.02, 0.0, 0.02, 0.05, np.inf],
}


def split_by_date(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    d = d.copy()
    d["SignalDate"] = pd.to_datetime(d["SignalDate"], errors="coerce")
    d = d.dropna(subset=["SignalDate"]).sort_values("SignalDate")
    dates = pd.Series(d["SignalDate"].drop_duplicates().tolist())
    train_end = dates.iloc[int(len(dates) * 0.60)]
    valid_end = dates.iloc[int(len(dates) * 0.75)]
    return (
        d[d["SignalDate"] < train_end].copy(),
        d[(d["SignalDate"] >= train_end) & (d["SignalDate"] < valid_end)].copy(),
        d[d["SignalDate"] >= valid_end].copy(),
    )


def stats(g: pd.DataFrame) -> dict:
    n = len(g)
    if n == 0:
        return {"n": 0, "success_rate": np.nan, "expectancy_R": np.nan}
    sr = float(g["Outcome"].mean())
    return {
        "n": int(n),
        "success_rate": sr,
        "expectancy_R": float(3 * sr - 1),
    }


def main() -> None:
    p = ROOT / "data" / "training_signals.parquet"
    if not p.exists():
        raise FileNotFoundError(p)
    d = pd.read_parquet(p)
    if d.empty:
        raise ValueError("Training signal dataset is empty")

    train, valid, holdout = split_by_date(d)
    out = ROOT / "artifacts" / "edge_discovery_v3"
    out.mkdir(parents=True, exist_ok=True)

    # Broad setup and context diagnostics on validation and holdout.
    for direction in ["Long", "Short"]:
        v = valid[valid["Direction"] == direction].copy()
        h = holdout[holdout["Direction"] == direction].copy()

        setup_rows = []
        for setup, vg in v.groupby("Setup"):
            hg = h[h["Setup"] == setup]
            sv = stats(vg)
            sh = stats(hg)
            setup_rows.append({
                "Direction": direction,
                "Setup": setup,
                "Validation_n": sv["n"],
                "Validation_success_rate": sv["success_rate"],
                "Validation_expectancy_R": sv["expectancy_R"],
                "Holdout_n": sh["n"],
                "Holdout_success_rate": sh["success_rate"],
                "Holdout_expectancy_R": sh["expectancy_R"],
            })
        pd.DataFrame(setup_rows).sort_values(
            ["Validation_expectancy_R", "Validation_n"], ascending=[False, False]
        ).to_csv(out / f"{direction.lower()}_by_setup.csv", index=False)

        feature_rows = []
        for feature, bins in BIN_SPECS.items():
            if feature not in v.columns:
                continue
            labels = pd.cut(v[feature], bins=bins, include_lowest=True)
            for label, vg in v.groupby(labels, observed=False):
                if len(vg) < 50:
                    continue
                hg = h[pd.cut(h[feature], bins=bins, include_lowest=True) == label]
                sv, sh = stats(vg), stats(hg)
                feature_rows.append({
                    "Direction": direction,
                    "Feature": feature,
                    "Bin": str(label),
                    "Validation_n": sv["n"],
                    "Validation_success_rate": sv["success_rate"],
                    "Validation_expectancy_R": sv["expectancy_R"],
                    "Holdout_n": sh["n"],
                    "Holdout_success_rate": sh["success_rate"],
                    "Holdout_expectancy_R": sh["expectancy_R"],
                })
        pd.DataFrame(feature_rows).sort_values(
            ["Validation_expectancy_R", "Validation_n"], ascending=[False, False]
        ).to_csv(out / f"{direction.lower()}_by_feature_bin.csv", index=False)

        # Pairwise setup x feature-bin rules. These are discovery candidates only;
        # the holdout is kept strictly for confirmation.
        pair_rows = []
        for feature, bins in BIN_SPECS.items():
            if feature not in v.columns:
                continue
            v_bins = pd.cut(v[feature], bins=bins, include_lowest=True)
            h_bins = pd.cut(h[feature], bins=bins, include_lowest=True)
            for setup in sorted(v["Setup"].dropna().unique()):
                for label in v_bins.dropna().unique():
                    vg = v[(v["Setup"] == setup) & (v_bins == label)]
                    if len(vg) < 50:
                        continue
                    hg = h[(h["Setup"] == setup) & (h_bins == label)]
                    sv, sh = stats(vg), stats(hg)
                    pair_rows.append({
                        "Direction": direction,
                        "Setup": setup,
                        "Feature": feature,
                        "Bin": str(label),
                        "Validation_n": sv["n"],
                        "Validation_success_rate": sv["success_rate"],
                        "Validation_expectancy_R": sv["expectancy_R"],
                        "Holdout_n": sh["n"],
                        "Holdout_success_rate": sh["success_rate"],
                        "Holdout_expectancy_R": sh["expectancy_R"],
                    })

        pair = pd.DataFrame(pair_rows)
        if not pair.empty:
            pair = pair.sort_values(
                ["Validation_expectancy_R", "Validation_n"], ascending=[False, False]
            )
        pair.to_csv(out / f"{direction.lower()}_candidate_rules.csv", index=False)

        top = pair.head(15).copy() if not pair.empty else pd.DataFrame()
        top.to_csv(out / f"{direction.lower()}_top15_validation_rules.csv", index=False)

    summary = {
        "dataset_signals": int(len(d)),
        "train_signals": int(len(train)),
        "validation_signals": int(len(valid)),
        "holdout_signals": int(len(holdout)),
        "train_start": str(train["SignalDate"].min().date()),
        "train_end": str(train["SignalDate"].max().date()),
        "validation_start": str(valid["SignalDate"].min().date()),
        "validation_end": str(valid["SignalDate"].max().date()),
        "holdout_start": str(holdout["SignalDate"].min().date()),
        "holdout_end": str(holdout["SignalDate"].max().date()),
        "note": "Rules are discovered on validation only; holdout is confirmation and must not be used for tuning.",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
