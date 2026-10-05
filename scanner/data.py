from __future__ import annotations
from pathlib import Path
import time
import pandas as pd
import yfinance as yf

def _normalize_yf_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw.dropna(how="all").copy()
    ticker = str(ticker)
    for level in range(raw.columns.nlevels):
        vals=set(map(str,raw.columns.get_level_values(level)))
        if ticker in vals:
            out=raw.xs(ticker,axis=1,level=level)
            if isinstance(out.columns,pd.MultiIndex):
                out.columns=[str(c[-1]) if str(c[-1]) in {"Open","High","Low","Close","Adj Close","Volume"} else str(c[0]) for c in out.columns]
            return out.dropna(how="all").copy()
    return pd.DataFrame()

def download_daily(symbols,period="1y",batch_size=100,sleep_seconds=1.0):
    out={}
    for start in range(0,len(symbols),batch_size):
        chunk=symbols[start:start+batch_size]
        try:
            raw=yf.download(tickers=chunk,period=period,interval="1d",auto_adjust=False,group_by="ticker",threads=True,progress=False,multi_level_index=True)
        except Exception as exc:
            print(f"DOWNLOAD ERROR {start}:{start+len(chunk)}: {exc}",flush=True)
            time.sleep(max(2,sleep_seconds*4)); continue
        if raw.empty:
            print(f"EMPTY DOWNLOAD {start}:{start+len(chunk)}",flush=True); continue
        for ticker in chunk:
            d=_normalize_yf_frame(raw,ticker)
            if not d.empty: out[ticker]=d
        if sleep_seconds: time.sleep(sleep_seconds)
    return out

def save_symbol_data(price_map,directory):
    path=Path(directory); path.mkdir(parents=True,exist_ok=True)
    for ticker,df in price_map.items(): df.to_parquet(path/f"{ticker.replace('/','_').replace('.','_')}.parquet")
