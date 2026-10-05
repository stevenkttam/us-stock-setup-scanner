from __future__ import annotations

from io import StringIO
from pathlib import Path
import re
import pandas as pd
import requests

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

EXCLUDED_NAME_PATTERNS = (
    r"\bPREFERRED\b",
    r"\bWARRANTS?\b",
    r"\bRIGHTS?\b",
    r"\bUNITS?\b",
    r"\bNOTE(S)?\b",
    r"\bBOND(S)?\b",
    r"\bDEBENTURE(S)?\b",
    r"\bETF\b",
    r"\bEXCHANGE TRADED FUND\b",
    r"\bFUND\b",
)

COMMON_NAME_PATTERN = re.compile(
    r"(COMMON STOCK|COMMON SHARES|ORDINARY SHARES|AMERICAN DEPOSITARY SHARES|CLASS [A-Z] COMMON STOCK|CLASS [A-Z] ORDINARY SHARES)",
    re.I,
)

def to_yahoo_symbol(symbol: str) -> str:
    """Convert common Nasdaq/NYSE class-dot symbols to Yahoo's dash form."""
    return str(symbol).strip().replace(".", "-")

def _looks_like_common_equity(name: str) -> bool:
    text = str(name or "").strip().upper()
    if any(re.search(p, text) for p in EXCLUDED_NAME_PATTERNS):
        return False
    return bool(COMMON_NAME_PATTERN.search(text))

def fetch_us_universe(cache_path: str | Path | None = None, refresh: bool = False) -> pd.DataFrame:
    """Fetch the current listed US equity universe from Nasdaq Trader.

    The source is a current trading-day symbol directory. It is suitable for
    the live scanner universe, but NOT a point-in-time historical universe.
    """
    if cache_path and Path(cache_path).exists() and not refresh:
        return pd.read_csv(cache_path)

    r = requests.get(
        NASDAQ_TRADED_URL,
        headers={"User-Agent": "USStockSetupScanner/1.0"},
        timeout=30,
    )
    r.raise_for_status()

    lines = [x for x in r.text.splitlines() if x and not x.startswith("File Creation")]
    df = pd.read_csv(StringIO("\n".join(lines)), sep="|", dtype=str)
    required = {"Symbol", "Security Name", "Listing Exchange", "ETF", "Test Issue"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Nasdaq Trader universe is missing columns: {sorted(missing)}")

    df = df[(df["Symbol"].notna()) & (df["Test Issue"].fillna("N") == "N")].copy()
    df = df[df["ETF"].fillna("N") != "Y"]
    df = df[~df["Symbol"].str.contains(r"\$|\^", regex=True, na=False)]
    df = df[df["Security Name"].map(_looks_like_common_equity)]

    df["Symbol"] = df["Symbol"].str.strip()
    df["YahooSymbol"] = df["Symbol"].map(to_yahoo_symbol)
    df = df.drop_duplicates("Symbol").reset_index(drop=True)

    keep = ["Symbol", "YahooSymbol", "Security Name", "Listing Exchange", "Market Category", "ETF", "Financial Status"]
    keep = [c for c in keep if c in df.columns]
    df = df[keep]

    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df
