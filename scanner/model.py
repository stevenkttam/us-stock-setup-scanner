from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

FEATURES = [
    "SetupScore", "RVOL20", "BaseDays", "BreakoutATR", "ATRCompression",
    "Trend20", "Trend50", "Trend200", "RS_SPY_20", "TR_ATR20",
]


def build_model() -> Pipeline:
    base = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
    ])
    # Isotonic calibration is robust for sufficiently large samples. For small
    # samples the training script falls back to an uncalibrated model.
    return CalibratedClassifierCV(base, method="isotonic", cv=TimeSeriesSplit(n_splits=5))


def prepare_training(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    x = df.copy()
    x = x.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES + ["Outcome"])
    return x[FEATURES], x["Outcome"].astype(int)


def train_and_validate(df: pd.DataFrame) -> tuple[object, dict]:
    x, y = prepare_training(df.sort_values("SignalDate"))
    if len(x) < 1000 or y.nunique() < 2:
        raise ValueError("Insufficient training data: need >=1000 clean observations and both outcomes.")
    split = int(len(x) * 0.75)
    x_train, x_test = x.iloc[:split], x.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]
    model = build_model()
    model.fit(x_train, y_train)
    p = model.predict_proba(x_test)[:, 1]
    metrics = {
        "n_train": int(len(x_train)),
        "n_test": int(len(x_test)),
        "brier": float(brier_score_loss(y_test, p)),
        "auc": float(roc_auc_score(y_test, p)) if y_test.nunique() == 2 else None,
        "test_success_rate": float(y_test.mean()),
    }
    return model, metrics


def model_metadata(metrics: dict, direction: str) -> dict:
    return {
        "direction": direction,
        "model_type": "Logistic regression + isotonic calibration",
        **metrics,
    }
