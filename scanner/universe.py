from __future__ import annotations

from io import StringIO
from pathlib import Path
import pandas as pd
import requests

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"


def fetch_us_universe(cache_path: str | Path | None = None) -> pd.DataFrame:
    """Fetch a broad current US symbol universe from Nasdaq Trader.

    This is a *current* universe. It should not be treated as a point-in-time
    historical universe for unbiased long-horizon backtests.
    """
    if cache_path and Path(cache_path).exists():
        return pd.read_csv(cache_path)

    r = requests.get(
        NASDAQ_TRADED_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    r.raise_for_status()
    text = r.text
    lines = [x for x in text.splitlines() if not x.startswith("File Creation")]
    df = pd.read_csv(StringIO("\n".join(lines)), sep="|", dtype=str)
    df = df[df["Symbol"].notna()].copy()
    df = df[~df["Symbol"].str.contains(r"\$|\^", regex=True, na=False)]
    df = df[df["Test Issue"].fillna("N") == "N"]

    if "ETF" in df.columns:
        df = df[df["ETF"].fillna("N") != "Y"]

    keep = [c for c in ["Symbol", "Security Name", "Listing Exchange", "ETF", "Test Issue"] if c in df.columns]
    df = df[keep].drop_duplicates("Symbol").reset_index(drop=True)

    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df
