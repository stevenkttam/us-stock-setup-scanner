from __future__ import annotations

import json
from pathlib import Path
import joblib
import pandas as pd
import yfinance as yf

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.scan import scan_symbol
from scanner.universe import fetch_us_universe
from scanner.model import FEATURES


def _latest_row_map(price_map, spy, cfg):
    regime = classify_regime(spy).iloc[-1]
    rows = []
    for ticker, prices in price_map.items():
        try:
            f = add_features(prices, spy=spy)
            longs, shorts = scan_symbol(f, ticker, str(regime), cfg)
            rows.extend(longs + shorts)
        except Exception as exc:
            print(f"SKIP {ticker}: {exc}")
    return pd.DataFrame(rows)


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    models_dir = ROOT / "models"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    universe = fetch_us_universe(data_dir / "universe.csv")
    symbols = universe["Symbol"].dropna().tolist()
    price_map = download_daily(symbols, period="1y", batch_size=100, sleep_seconds=0.5)
    spy = yf.download("SPY", period="1y", interval="1d", auto_adjust=False, progress=False)
    if hasattr(spy.columns, "levels") and len(spy.columns.levels) > 1:
        spy = spy.xs("SPY", axis=1, level=0)

    candidates = _latest_row_map(price_map, spy, cfg)
    if candidates.empty:
        raise SystemExit("No candidates generated.")

    probabilities = []
    model_status = {}
    for direction in ["Long", "Short"]:
        path = models_dir / f"{direction.lower()}_probability.joblib"
        mask = candidates["Direction"].eq(direction)
        if path.exists():
            model = joblib.load(path)
            # Current scanner currently lacks direct exposure of all model
            # features; reconstruct the subset available in the candidate row.
            # Missing raw features are filled conservatively from candidate data.
            x = candidates.loc[mask].copy()
            for col in FEATURES:
                if col not in x.columns:
                    x[col] = 0.0
            x = x.replace([float("inf"), float("-inf")], float("nan")).fillna(0.0)
            p = model.predict_proba(x[FEATURES])[:, 1]
            probabilities.extend([(idx, float(prob)) for idx, prob in zip(x.index, p)])
            model_status[direction] = "loaded"
        else:
            model_status[direction] = "not_available"

    for idx, p in probabilities:
        candidates.loc[idx, "Probability"] = p
    candidates["BinaryExpectedR"] = candidates["Probability"].apply(lambda p: (3 * p - 1) if pd.notna(p) else None)
    candidates["Priority"] = candidates["Probability"].fillna(0).apply(lambda p: "A — Trade" if p >= cfg["ranking"]["initial_trade_probability_threshold"] else ("B — Watch" if p >= cfg["ranking"]["watch_probability_floor"] else "C — Research"))
    out = data_dir / "latest_scan.parquet"
    candidates.to_parquet(out, index=False)
    (artifacts / "live_scan_status.json").write_text(json.dumps({"model_status": model_status, "candidates": int(len(candidates))}, indent=2), encoding="utf-8")
    print(f"Saved {len(candidates):,} candidates to {out}")


if __name__ == "__main__":
    main()
