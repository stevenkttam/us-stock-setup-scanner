from __future__ import annotations

from pathlib import Path
import json
import pandas as pd
import yfinance as yf

from scanner.config import ROOT, load_config
from scanner.universe import fetch_us_universe


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    artifacts_dir = ROOT / "artifacts"
    data_dir.mkdir(exist_ok=True)
    artifacts_dir.mkdir(exist_ok=True)

    universe = fetch_us_universe(data_dir / "universe.csv")
    symbols = universe["Symbol"].dropna().tolist()

    # Conservative starter batch. The production runner should chunk requests
    # and use caching/backoff to stay within free-provider rate limits.
    batch_size = 100
    chunks = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    all_rows = []
    for idx, chunk in enumerate(chunks, 1):
        raw = yf.download(
            tickers=chunk,
            period="1y",
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        if raw.empty:
            continue
        # Save the raw batch for diagnostics. Feature generation and signal
        # extraction are kept separate from download concerns in this V1.
        raw.to_parquet(data_dir / f"raw_batch_{idx:03d}.parquet")

    meta = {
        "status": "download_complete",
        "universe_rows": int(len(universe)),
        "batches": len(chunks),
        "note": "Feature extraction/backtest training should run as a separate scheduled job once the historical data store is established.",
    }
    (artifacts_dir / "run_status.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
