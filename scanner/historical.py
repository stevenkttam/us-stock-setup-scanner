from __future__ import annotations
import numpy as np
import pandas as pd
from .features import add_features,classify_regime
from .scan import score_candidate

def _best_daily_candidates(df,ticker,config,regime=None):
    rows=[]; warmup=220; min_price=float(config["universe"]["min_price"]); min_dollar=float(config["universe"]["min_avg_dollar_volume_20d"])
    for i in range(warmup,len(df)):
        row=df.iloc[i]; reg=str(regime.iloc[i]) if regime is not None and i<len(regime) else "Neutral"
        if not np.isfinite(row.get("Close",np.nan)) or row.get("Close",0)<min_price or not np.isfinite(row.get("DollarVolume20",np.nan)) or row.get("DollarVolume20",0)<min_dollar: continue
        for direction in ("Long","Short"):
            candidates=[]
            for n in config["signal"]["consolidation_lengths"]:
                c=score_candidate(row,direction,n,reg,config["signal"])
                if c: c.update(Ticker=ticker,SignalDate=pd.Timestamp(df.index[i])); candidates.append(c)
            if candidates: rows.append(max(candidates,key=lambda x:x["SetupScore"]))
    return pd.DataFrame(rows)

def build_historical_signals(price_map,spy,config):
    regime_series=classify_regime(spy); all_rows=[]
    for ticker,prices in price_map.items():
        try:
            f=add_features(prices,spy=spy); r=regime_series.reindex(f.index).ffill().fillna("Neutral"); c=_best_daily_candidates(f,ticker,config,r)
            if not c.empty: all_rows.append(c)
        except Exception as exc: print(f"SKIP {ticker}: {exc}")
    return pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()

def add_outcomes(signals,price_map,config):
    from .backtest import label_trade
    rows=[]; h=int(config["trade"]["holding_period_days"])
    for _,s in signals.iterrows():
        prices=price_map.get(s["Ticker"])
        if prices is None or prices.empty: continue
        prices=prices.sort_index()
        try: loc=prices.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError: continue
        if isinstance(loc,slice) or loc+1>=len(prices): continue
        entry=float(prices.iloc[loc+1]["Open"]); rps=float(s["RiskPerShare"])
        if not np.isfinite(rps) or rps<=0: continue
        stop=entry-rps if s["Direction"]=="Long" else entry+rps; target=entry+2*rps if s["Direction"]=="Long" else entry-2*rps
        outcome=label_trade(prices,int(loc),s["Direction"],entry,stop,target,h)
        rows.append({**s.to_dict(),"EntryActual":entry,"StopActual":stop,"TargetActual":target,"Outcome":outcome})
    return pd.DataFrame(rows)
