from __future__ import annotations
import os
from pathlib import Path
import pandas as pd
import yfinance as yf
from scanner.config import ROOT,load_config
from scanner.data import download_daily, _normalize_yf_frame
from scanner.historical import build_historical_signals,add_outcomes
from scanner.universe import fetch_us_universe

def main():
    cfg=load_config(); data_dir=ROOT/"data"; chunks_dir=data_dir/"training_chunks"; chunks_dir.mkdir(parents=True,exist_ok=True)
    universe=fetch_us_universe(data_dir/"universe.csv")
    symbols=universe["YahooSymbol"].dropna().tolist() if "YahooSymbol" in universe.columns else universe["Symbol"].dropna().tolist()
    max_symbols=int(os.getenv("SCANNER_MAX_SYMBOLS","0")); symbols=symbols[:max_symbols] if max_symbols else symbols
    batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")); period=os.getenv("SCANNER_PERIOD","10y")
    spy_raw=yf.download("SPY",period=period,interval="1d",auto_adjust=False,progress=False,multi_level_index=True)
    spy=_normalize_yf_frame(spy_raw,"SPY")
    if spy.empty: raise RuntimeError("SPY historical data download returned no usable rows")
    completed=0
    for start in range(0,len(symbols),batch_size):
        chunk=symbols[start:start+batch_size]; chunk_id=start//batch_size+1; out=chunks_dir/f"signals_{chunk_id:04d}.parquet"
        if out.exists(): print(f"SKIP completed chunk {chunk_id}"); continue
        price_map=download_daily(chunk,period=period,batch_size=batch_size,sleep_seconds=.75)
        if not price_map: continue
        signals=build_historical_signals(price_map,spy,cfg); labelled=add_outcomes(signals,price_map,cfg)
        if not labelled.empty: labelled.to_parquet(out,index=False)
        completed+=len(price_map); print(f"Chunk {chunk_id}: {len(price_map)} symbols, {len(labelled)} labelled signals")
    files=sorted(chunks_dir.glob("signals_*.parquet")); frames=[pd.read_parquet(f) for f in files]
    combined=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    combined.to_parquet(data_dir/"training_signals.parquet",index=False)
    print(f"Saved {len(combined):,} labelled setups across {completed:,} downloaded symbols")

if __name__=="__main__": main()
