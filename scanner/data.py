from __future__ import annotations
from pathlib import Path
import time
import pandas as pd
import yfinance as yf

def download_daily(symbols,period="1y",batch_size=100,sleep_seconds=1.0):
    out={}
    for start in range(0,len(symbols),batch_size):
        chunk=symbols[start:start+batch_size]
        try: raw=yf.download(tickers=chunk,period=period,interval="1d",auto_adjust=False,group_by="ticker",threads=True,progress=False)
        except Exception as exc:
            print(f"DOWNLOAD ERROR {start}:{start+len(chunk)}: {exc}"); time.sleep(max(2,sleep_seconds*4)); continue
        if raw.empty: continue
        if isinstance(raw.columns,pd.MultiIndex):
            lvl0=set(raw.columns.get_level_values(0))
            for ticker in chunk:
                if ticker in lvl0:
                    d=raw[ticker].copy().dropna(how="all")
                    if not d.empty: out[ticker]=d
        else: out[chunk[0]]=raw.dropna(how="all").copy()
        if sleep_seconds: time.sleep(sleep_seconds)
    return out

def save_symbol_data(price_map,directory):
    path=Path(directory); path.mkdir(parents=True,exist_ok=True)
    for ticker,df in price_map.items(): df.to_parquet(path/f"{ticker.replace('/','_').replace('.','_')}.parquet")
