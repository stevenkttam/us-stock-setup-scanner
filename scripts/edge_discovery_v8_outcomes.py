from __future__ import annotations

import os
import pandas as pd
import numpy as np

from scanner.config import ROOT
from scanner.data import download_daily
from scanner.historical import build_historical_signals


HORIZONS=[3,5,10]
TARGETS=[0.50,0.75,1.00,1.25,1.50,2.00]


def f(x):
    try: return float(x)
    except Exception: return np.nan


def label_trade(px, idx, direction, risk, horizon, target_r):
    if idx+1>=len(px) or not np.isfinite(risk) or risk<=0: return 0
    entry=f(px.iloc[idx+1]["Open"])
    if not np.isfinite(entry): return 0
    stop=entry-risk if direction=="Long" else entry+risk
    target=entry+target_r*risk if direction=="Long" else entry-target_r*risk
    future=px.iloc[idx+1:min(idx+1+horizon,len(px))]
    for _,bar in future.iterrows():
        hi,lo=f(bar["High"]),f(bar["Low"])
        if direction=="Long": st,ta=lo<=stop,hi>=target
        else: st,ta=hi>=stop,lo<=target
        if st and ta: return 0
        if ta: return 1
        if st: return 0
    return 0


def main():
    cfg_path=ROOT/"config.yaml"
    from scanner.config import load_config
    cfg=load_config()
    universe_path=ROOT/"data"/"universe.csv"
    from scanner.universe import fetch_us_universe
    u=fetch_us_universe(universe_path)
    syms=u["YahooSymbol"].dropna().tolist()
    max_symbols=int(os.getenv("SCANNER_MAX_SYMBOLS","500"))
    if len(syms)>max_symbols:
        last=len(syms)-1
        syms=[syms[round(j*last/(max_symbols-1))] for j in range(max_symbols)]
    period=os.getenv("SCANNER_PERIOD","10y")
    pxmap=download_daily(syms,period=period,batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")),sleep_seconds=.75)
    spy=download_daily(["SPY"],period=period,batch_size=1,sleep_seconds=.1).get("SPY")
    if spy is None or spy.empty: raise RuntimeError("SPY failed")

    signals=[]
    for t,px in pxmap.items():
        try:
            s=build_historical_signals({t:px},spy,cfg)
            if not s.empty: signals.append(s)
        except Exception as e:
            print(f"SKIP {t}: {e}")
    sig=pd.concat(signals,ignore_index=True) if signals else pd.DataFrame()
    if sig.empty: raise RuntimeError("No signals")

    rows=[]
    for _,s in sig.iterrows():
        px=pxmap.get(str(s["Ticker"]))
        if px is None: continue
        px=px.sort_index()
        try: idx=px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError: continue
        if isinstance(idx,slice) or idx+1>=len(px): continue
        risk=f(s["RiskPerShare"])
        if not np.isfinite(risk) or risk<=0: continue
        for h in HORIZONS:
            for tr in TARGETS:
                rows.append({
                    "Ticker":s["Ticker"],"SignalDate":pd.Timestamp(s["SignalDate"]),
                    "Direction":s["Direction"],"Setup":s["Setup"],"Outcome":label_trade(px,int(idx),s["Direction"],risk,h,tr),
                    "TargetR":tr,"HorizonDays":h
                })
    out=ROOT/"data"/"edge_discovery_v8_outcomes.parquet"
    pd.DataFrame(rows).to_parquet(out,index=False)
    print(f"Saved {len(rows):,} outcome rows from {len(sig):,} signals across {len(pxmap):,} symbols")


if __name__=="__main__":
    main()
