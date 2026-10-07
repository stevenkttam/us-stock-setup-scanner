from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = [
    "SetupScore",
    "RVOL20",
    "VolumeAcceleration",
    "BaseDays",
    "BreakoutATR",
    "ATRCompression",
    "Trend20",
    "Trend50",
    "Trend200",
    "RS_SPY_20",
    "TR_ATR20",
    "CloseLocation",
    "MarketRegimeNum",
]


def add_model_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["MarketRegimeNum"] = x["MarketRegime"].astype(str).map(
        lambda v: 1.0 if v.startswith("Bullish") else (-1.0 if v.startswith("Bearish") else 0.0)
    )
    for c in FEATURES:
        if c != "MarketRegimeNum" and c not in x.columns:
            raise ValueError(f"Missing model feature: {c}")
    return x


def build_model() -> CalibratedClassifierCV:
    base = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced")),
    ])
    return CalibratedClassifierCV(
        base,
        method="isotonic",
        cv=TimeSeriesSplit(n_splits=5),
    )


def prepare_training(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    x = add_model_features(df)
    x = x.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES + ["Outcome", "SignalDate"])
    x = x.sort_values("SignalDate")
    return x, x["Outcome"].astype(int)


def _top_slice_metrics(y: pd.Series, p: np.ndarray, fraction: float) -> dict:
    n = max(1, int(len(p) * fraction))
    order = np.argsort(-p)[:n]
    yp = y.to_numpy()[order]
    return {
        "n": int(n),
        "success_rate": float(yp.mean()),
        "binary_expectancy_R": float(3 * yp.mean() - 1),
    }


def train_and_validate(df: pd.DataFrame) -> tuple[object, dict]:
    clean, y = prepare_training(df)
    if len(clean) < 1000 or y.nunique() < 2:
        raise ValueError("Insufficient training data: need >=1000 clean observations and both outcomes.")

    # Split by date, not row count, so the same signal date cannot leak across
    # the train/test boundary.
    dates = pd.Series(pd.to_datetime(clean["SignalDate"]).drop_duplicates().sort_values().to_list())
    split_date = dates.iloc[int(len(dates) * 0.75)]
    train_mask = clean["SignalDate"] < split_date
    test_mask = ~train_mask

    train = clean.loc[train_mask]
    test = clean.loc[test_mask]
    x_train, y_train = train[FEATURES], train["Outcome"].astype(int)
    x_test, y_test = test[FEATURES], test["Outcome"].astype(int)

    if len(x_test) < 100 or y_test.nunique() < 2:
        raise ValueError("Time-based test set is too small or contains only one outcome class.")

    model = build_model()
    model.fit(x_train, y_train)
    p = model.predict_proba(x_test)[:, 1]

    top10 = _top_slice_metrics(y_test, p, 0.10)
    top20 = _top_slice_metrics(y_test, p, 0.20)
    top30 = _top_slice_metrics(y_test, p, 0.30)

    metrics = {
        "n_total": int(len(clean)),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "train_start": str(pd.to_datetime(train["SignalDate"]).min().date()),
        "train_end": str(pd.to_datetime(train["SignalDate"]).max().date()),
        "test_start": str(pd.to_datetime(test["SignalDate"]).min().date()),
        "test_end": str(pd.to_datetime(test["SignalDate"]).max().date()),
        "brier": float(brier_score_loss(y_test, p)),
        "auc": float(roc_auc_score(y_test, p)),
        "test_success_rate": float(y_test.mean()),
        "top10": top10,
        "top20": top20,
        "top30": top30,
    }
    return model, metrics


def model_metadata(metrics: dict, direction: str) -> dict:
    return {
        "direction": direction,
        "model_type": "Logistic regression + isotonic calibration",
        "probability_definition": "Historical probability of +2R before -1R within 10 trading sessions",
        **metrics,
    }
