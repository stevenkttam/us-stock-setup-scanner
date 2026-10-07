from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

NUMERIC_FEATURES = [
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
    "BaseRangePct",
    "ATR20Pct",
    "RiskATR",
    "DollarVolume20Log",
    "GapPct",
]

CATEGORICAL_FEATURES = [
    "Setup",
    "MarketRegime",
    "Trend",
]

ALL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

V1_NUMERIC_FEATURES = [
    "SetupScore",
    "RVOL20",
    "BaseDays",
    "BreakoutATR",
    "ATRCompression",
    "Trend20",
    "Trend50",
    "Trend200",
    "RS_SPY_20",
    "TR_ATR20",
    "MarketRegimeNum",
]


def add_v2_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["MarketRegime"] = x["MarketRegime"].astype(str)
    x["Setup"] = x["Setup"].astype(str)
    x["Trend"] = x["Trend"].astype(str)

    if "DollarVolume20" in x.columns and "DollarVolume20Log" not in x.columns:
        x["DollarVolume20Log"] = np.log1p(pd.to_numeric(x["DollarVolume20"], errors="coerce").clip(lower=0))
    if "DollarVolume20Log" not in x.columns:
        raise ValueError("Missing DollarVolume20/DollarVolume20Log feature")

    required = ALL_FEATURES + ["Outcome", "SignalDate"]
    missing = [c for c in required if c not in x.columns]
    if missing:
        raise ValueError(f"Missing V2 model features: {missing}")

    x["SignalDate"] = pd.to_datetime(x["SignalDate"], errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)
    x = x.dropna(subset=NUMERIC_FEATURES + ["Outcome", "SignalDate"])
    x["Outcome"] = x["Outcome"].astype(int)
    return x.sort_values("SignalDate").reset_index(drop=True)


def _prepare_v1(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["MarketRegimeNum"] = x["MarketRegime"].astype(str).map(
        lambda v: 1.0 if v.startswith("Bullish") else (-1.0 if v.startswith("Bearish") else 0.0)
    )
    x["SignalDate"] = pd.to_datetime(x["SignalDate"], errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan).dropna(
        subset=V1_NUMERIC_FEATURES + ["Outcome", "SignalDate"]
    )
    return x.sort_values("SignalDate").reset_index(drop=True)


def build_logistic_v1_baseline() -> CalibratedClassifierCV:
    base = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced")),
    ])
    return CalibratedClassifierCV(
        base,
        method="isotonic",
        cv=TimeSeriesSplit(n_splits=5),
    )


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        sparse_threshold=0.0,
    )


def _preprocessor_tree() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("num", "passthrough", NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        sparse_threshold=0.0,
    )


def build_logistic_v2() -> CalibratedClassifierCV:
    base = Pipeline([
        ("prep", _preprocessor()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced")),
    ])
    return CalibratedClassifierCV(
        base,
        method="isotonic",
        cv=TimeSeriesSplit(n_splits=5),
    )


def build_hist_gradient_boosting() -> CalibratedClassifierCV:
    base = Pipeline([
        ("prep", _preprocessor_tree()),
        (
            "clf",
            HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=250,
                max_leaf_nodes=15,
                min_samples_leaf=40,
                l2_regularization=1.0,
                class_weight="balanced",
                random_state=42,
            ),
        ),
    ])
    return CalibratedClassifierCV(
        base,
        method="isotonic",
        cv=TimeSeriesSplit(n_splits=5),
    )


def _date_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.Series(df["SignalDate"].drop_duplicates().sort_values().tolist())
    if len(dates) < 60:
        raise ValueError("Not enough unique signal dates for train/validation/holdout split.")

    train_end = dates.iloc[int(len(dates) * 0.60)]
    valid_end = dates.iloc[int(len(dates) * 0.75)]

    train = df[df["SignalDate"] < train_end].copy()
    valid = df[(df["SignalDate"] >= train_end) & (df["SignalDate"] < valid_end)].copy()
    holdout = df[df["SignalDate"] >= valid_end].copy()

    if min(len(train), len(valid), len(holdout)) < 100:
        raise ValueError("One of the time-based partitions has fewer than 100 observations.")
    if min(train["Outcome"].nunique(), valid["Outcome"].nunique(), holdout["Outcome"].nunique()) < 2:
        raise ValueError("Each time-based partition must contain both outcome classes.")
    return train, valid, holdout


def _metrics(y: pd.Series, p: np.ndarray) -> dict[str, Any]:
    yv = y.to_numpy(dtype=int)
    p = np.asarray(p, dtype=float)
    order = np.argsort(-p)

    def top(frac: float) -> dict[str, Any]:
        n = max(1, int(len(p) * frac))
        yy = yv[order[:n]]
        sr = float(yy.mean())
        return {
            "n": int(n),
            "success_rate": sr,
            "binary_expectancy_R": float(3 * sr - 1),
        }

    return {
        "n": int(len(yv)),
        "success_rate": float(yv.mean()),
        "brier": float(brier_score_loss(yv, p)),
        "auc": float(roc_auc_score(yv, p)) if np.unique(yv).size == 2 else None,
        "top10": top(0.10),
        "top20": top(0.20),
        "top30": top(0.30),
    }


def _calibration_table(y: pd.Series, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    out = pd.DataFrame({"Outcome": y.to_numpy(dtype=int), "Probability": np.asarray(p, dtype=float)})
    out["Bin"] = pd.qcut(
        out["Probability"].rank(method="first"),
        q=min(bins, len(out)),
        labels=False,
    )
    g = out.groupby("Bin", observed=True).agg(
        n=("Outcome", "size"),
        predicted_probability=("Probability", "mean"),
        observed_success_rate=("Outcome", "mean"),
    ).reset_index()
    g["calibration_error"] = g["observed_success_rate"] - g["predicted_probability"]
    return g


def _fit_and_predict(model: Any, train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    model.fit(train[ALL_FEATURES], train["Outcome"])
    return model.predict_proba(target[ALL_FEATURES])[:, 1]


def _model_rank_key(metrics: dict[str, Any]) -> tuple[float, float, float]:
    # Validation selection: positive top-10/top-20 expectancy first, then lower
    # Brier. This does not inspect the final holdout.
    return (
        metrics["top10"]["binary_expectancy_R"],
        metrics["top20"]["binary_expectancy_R"],
        -metrics["brier"],
    )


def train_and_validate_v2(df: pd.DataFrame, direction: str) -> tuple[Any, dict[str, Any]]:
    clean = add_v2_features(df)
    clean_v1 = _prepare_v1(df)
    train, valid, holdout = _date_split(clean)

    candidates = {
        "Logistic V1 Baseline": build_logistic_v1_baseline(),
        "Logistic V2": build_logistic_v2(),
        "HistGradientBoosting V2": build_hist_gradient_boosting(),
    }

    validation = {}

    baseline_train, baseline_valid, baseline_holdout = _date_split(clean_v1)
    v1_split_matches = (
        baseline_valid["SignalDate"].min() == valid["SignalDate"].min()
        and baseline_holdout["SignalDate"].min() == holdout["SignalDate"].min()
    )
    if not v1_split_matches:
        raise ValueError("V1 baseline and V2 sample do not share the same chronological boundaries.")

    v1_model = candidates["Logistic V1 Baseline"]
    p_valid = v1_model.fit(
        baseline_train[V1_NUMERIC_FEATURES], baseline_train["Outcome"]
    ).predict_proba(baseline_valid[V1_NUMERIC_FEATURES])[:, 1]
    validation["Logistic V1 Baseline"] = _metrics(baseline_valid["Outcome"], p_valid)

    selected_name = max(validation, key=lambda name: _model_rank_key(validation[name]))

    # Refit the selected model on train + validation, then evaluate once on the
    # untouched chronological holdout. The holdout is never used for selection.
    dev = pd.concat([train, valid], ignore_index=True)
    selected_model = candidates[selected_name]

    if selected_name == "Logistic V1 Baseline":
        dev_v1 = pd.concat([baseline_train, baseline_valid], ignore_index=True)
        selected_model.fit(dev_v1[V1_NUMERIC_FEATURES], dev_v1["Outcome"])
        p_holdout = selected_model.predict_proba(baseline_holdout[V1_NUMERIC_FEATURES])[:, 1]
    else:
        p_holdout = _fit_and_predict(selected_model, dev, holdout)
    holdout_metrics = _metrics(holdout["Outcome"], p_holdout)
    calibration = _calibration_table(holdout["Outcome"], p_holdout)

    metadata = {
        "direction": direction,
        "selected_model": selected_name,
        "model_candidates": {
            name: {"validation": metrics}
            for name, metrics in validation.items()
        },
        "probability_definition": "Historical probability of +2R before -1R within 10 trading sessions",
        "split": {
            "train_start": str(train["SignalDate"].min().date()),
            "train_end": str(train["SignalDate"].max().date()),
            "validation_start": str(valid["SignalDate"].min().date()),
            "validation_end": str(valid["SignalDate"].max().date()),
            "holdout_start": str(holdout["SignalDate"].min().date()),
            "holdout_end": str(holdout["SignalDate"].max().date()),
            "n_train": int(len(train)),
            "n_validation": int(len(valid)),
            "n_holdout": int(len(holdout)),
        },
        "holdout": holdout_metrics,
        "features": V1_NUMERIC_FEATURES if selected_name == "Logistic V1 Baseline" else ALL_FEATURES,
        "selection_rule": "Choose on chronological validation only using top-10 expectancy, then top-20 expectancy, then lower Brier; evaluate once on untouched holdout.",
    }

    return selected_model, {
        "metadata": metadata,
        "calibration": calibration,
    }


def write_v2_outputs(model: Any, report: dict[str, Any], direction: str, model_dir: Path, artifact_dir: Path) -> None:
    import joblib

    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, model_dir / f"{direction.lower()}_probability_v2.joblib")
    metadata_path = model_dir / f"{direction.lower()}_metadata_v2.json"
    metadata_path.write_text(json.dumps(report["metadata"], indent=2), encoding="utf-8")

    report["calibration"].to_csv(
        artifact_dir / f"{direction.lower()}_holdout_calibration.csv",
        index=False,
    )
