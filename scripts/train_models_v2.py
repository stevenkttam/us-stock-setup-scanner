from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scanner.config import ROOT
from scanner.model_v2 import train_and_validate_v2, write_v2_outputs


def main() -> None:
    data_path = ROOT / "data" / "training_signals.parquet"
    if not data_path.exists():
        raise FileNotFoundError(data_path)

    data = pd.read_parquet(data_path)
    if data.empty:
        raise ValueError("Training signal dataset is empty.")

    model_dir = ROOT / "models" / "v2"
    artifact_dir = ROOT / "artifacts" / "probability_v2_500_3y"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    combined = {
        "dataset": {
            "signals": int(len(data)),
            "unique_tickers": int(data["Ticker"].nunique()),
            "signal_start": str(pd.to_datetime(data["SignalDate"]).min().date()),
            "signal_end": str(pd.to_datetime(data["SignalDate"]).max().date()),
        },
        "directions": {},
    }

    for direction in ["Long", "Short"]:
        subset = data[data["Direction"] == direction].copy()
        model, report = train_and_validate_v2(subset, direction)
        write_v2_outputs(model, report, direction, model_dir, artifact_dir)

        combined["directions"][direction] = report["metadata"]
        print(
            f"{direction}: selected={report['metadata']['selected_model']} "
            f"holdout_auc={report['metadata']['holdout']['auc']:.4f} "
            f"holdout_brier={report['metadata']['holdout']['brier']:.4f} "
            f"top10_expectancy={report['metadata']['holdout']['top10']['binary_expectancy_R']:.3f}"
        )

    (artifact_dir / "validation_summary_v2.json").write_text(
        json.dumps(combined, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
