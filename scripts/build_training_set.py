from __future__ import annotations

from pathlib import Path
import yfinance as yf

from scanner.config import ROOT, load_config
from scanner.data import download_daily, save_symbol_data
from scanner.historical import build_historical_signals, add_outcomes
from scanner.universe import fetch_us_universe


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    universe = fetch_us_universe(data_dir / "universe.csv")
    symbols = universe["Symbol"].dropna().tolist()
    price_map = download_daily(symbols, period="10y", batch_size=50, sleep_seconds=0.75)
    spy = yf.download("SPY", period="10y", interval="1d", auto_adjust=False, progress=False)
    if hasattr(spy.columns, "levels") and len(spy.columns.levels) > 1:
        spy = spy.xs("SPY", axis=1, level=0)
    signals = build_historical_signals(price_map, spy, cfg)
    labelled = add_outcomes(signals, price_map, cfg)
    labelled.to_parquet(data_dir / "training_signals.parquet", index=False)
    save_symbol_data({"SPY": spy}, data_dir / "prices_cache")
    print(f"Saved {len(labelled):,} labelled historical setups")


if __name__ == "__main__":
    main()
