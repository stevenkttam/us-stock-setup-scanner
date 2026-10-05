from __future__ import annotations

import json
from pathlib import Path
import joblib
import pandas as pd

from scanner.config import ROOT
from scanner.model import train_and_validate, model_metadata


def main():
    data = pd.read_parquet(ROOT / "data" / "training_signals.parquet")
    model_dir = ROOT / "models"
    model_dir.mkdir(exist_ok=True)
    for direction in ["Long", "Short"]:
        subset = data[data["Direction"] == direction].copy()
        model, metrics = train_and_validate(subset)
        joblib.dump(model, model_dir / f"{direction.lower()}_probability.joblib")
        (model_dir / f"{direction.lower()}_metadata.json").write_text(json.dumps(model_metadata(metrics, direction), indent=2), encoding="utf-8")
        print(direction, metrics)


if __name__ == "__main__":
    main()
