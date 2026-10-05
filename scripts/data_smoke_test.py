from __future__ import annotations
import os
from pathlib import Path
import pandas as pd
import yfinance as yf
from scanner.universe import fetch_us_universe
from scanner.data import download_daily
from scanner.features import add_features
from scanner.historical import build_historical_signals, add_outcomes
from scanner.config import ROOT, load_config

def main():
    print("=== US STOCK SCANNER DATA SMOKE TEST ===",flush=True)
    cfg=load_config(); max_symbols=int(os.getenv("SCANNER_MAX_SYMBOLS","25")); period=os.getenv("SCANNER_PERIOD","2y")
    print(f"Config: max_symbols={max_symbols}, period={period}",flush=True)

    print("[1/6] Fetching current US universe...",flush=True)
    universe=fetch_us_universe(ROOT/"data"/"universe.csv",refresh=True)
    print(f"Universe rows: {len(universe):,}",flush=True)
    if universe.empty: raise RuntimeError("Universe is empty")
    symbols=universe["YahooSymbol"].dropna().tolist()[:max_symbols]
    if not symbols: raise RuntimeError("No Yahoo symbols in universe")

    print("[2/6] Downloading SPY...",flush=True)
    spy=yf.download("SPY",period=period,interval="1d",auto_adjust=False,progress=False,multi_level_index=True)
    if isinstance(spy.columns,pd.MultiIndex):
        for level in range(spy.columns.nlevels):
            vals=set(map(str,spy.columns.get_level_values(level)))
            if "SPY" in vals:
                spy=spy.xs("SPY",axis=1,level=level); break
    spy=spy.dropna(how="all")
    print(f"SPY rows: {len(spy):,}; columns={list(spy.columns)}",flush=True)
    if len(spy)<250: raise RuntimeError("Insufficient SPY history")

    print(f"[3/6] Downloading {len(symbols)} sample stocks...",flush=True)
    price_map=download_daily(symbols,period=period,batch_size=25,sleep_seconds=.5)
    print(f"Successful symbol downloads: {len(price_map):,}/{len(symbols):,}",flush=True)
    if len(price_map)<3: raise RuntimeError("Too few successful symbol downloads")

    print("[4/6] Calculating features...",flush=True)
    test_ticker=next(iter(price_map)); f=add_features(price_map[test_ticker],spy=spy)
    for required in ["RVOL20","ATR20","EMA20","EMA50","EMA200"]:
        if required not in f.columns: raise RuntimeError(f"Missing feature: {required}")
    print(f"Feature rows for {test_ticker}: {len(f):,}",flush=True)

    print("[5/6] Generating historical signals and outcomes...",flush=True)
    signals=build_historical_signals(price_map,spy,cfg); labelled=add_outcomes(signals,price_map,cfg)
    print(f"Signals: {len(signals):,}; labelled outcomes: {len(labelled):,}",flush=True)
    labelled.to_parquet(ROOT/"data"/"training_signals_smoke.parquet",index=False)

    print("[6/6] Smoke test complete.",flush=True)
    if labelled.empty:
        print("WARNING: pipeline executed successfully but sample produced zero qualifying setups.",flush=True)
    else:
        print(labelled.groupby("Direction")["Outcome"].agg(["count","mean"]).to_string(),flush=True)

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"SMOKE TEST FAILED: {type(exc).__name__}: {exc}",flush=True); raise
