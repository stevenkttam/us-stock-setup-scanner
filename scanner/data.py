from __future__ import annotations

from pathlib import Path
import time
import pandas as pd
import yfinance as yf


def download_daily(symbols: list[str], period: str = "1y", batch_size: int = 100, sleep_seconds: float = 1.0) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV in chunks and return one dataframe per symbol."""
    out: dict[str, pd.DataFrame] = {}
    for start in range(0, len(symbols), batch_size):
        chunk = symbols[start:start + batch_size]
        raw = yf.download(
            tickers=chunk,
            period=period,
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        if raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            lvl0 = set(raw.columns.get_level_values(0))
            for ticker in chunk:
                if ticker in lvl0:
                    d = raw[ticker].copy()
                    d = d.dropna(how="all")
                    if not d.empty:
                        out[ticker] = d
        else:
            ticker = chunk[0]
            out[ticker] = raw.dropna(how="all").copy()
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return out


def save_symbol_data(price_map: dict[str, pd.DataFrame], directory: str | Path) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    for ticker, df in price_map.items():
        safe = ticker.replace("/", "_").replace(".", "_")
        df.to_parquet(path / f"{safe}.parquet")
