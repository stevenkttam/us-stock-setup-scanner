from __future__ import annotations

import json
import os
import pandas as pd
import numpy as np

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.universe import fetch_us_universe


VARIANTS = {
    "VCP_Moderate": {"range_decay": 0.90, "volume_decay": 0.90, "atr_ratio_max": 0.80},
    "VCP_Strict": {"range_decay": 0.80, "volume_decay": 0.85, "atr_ratio_max": 0.75},
}
TARGETS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
HORIZONS = [5, 10]
WARMUP = 220


def f(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def half_label(ts):
    y, m = ts.year, ts.month
    return f"{y}-H1" if m <= 6 else f"{y}-H2"


def detect_vcp(d, ticker, cfg, regime):
    rows=[]
    min_price=float(cfg["universe"]["min_price"])
    min_dv=float(cfg["universe"]["min_avg_dollar_volume_20d"])
    for i in range(WARMUP, len(d)):
        row=d.iloc[i]
        close=f(row["Close"])
        atr=f(row["ATR20"])
        rvol=f(row["RVOL20"])
        loc=f(row["CloseLocation"])
        dv=f(row["DollarVolume20"])
        resistance=f(row["HH_15"])
        if not all(np.isfinite(x) for x in [close, atr, rvol, loc, dv, resistance]):
            continue
        if close < min_price or dv < min_dv or rvol < float(cfg["signal"]["rvol20_min"]):
            continue
        if close <= resistance or loc < float(cfg["signal"]["close_location_long_min"]):
            continue
        br=(close-resistance)/atr
        if br < float(cfg["signal"]["breakout_atr_min"]):
            continue

        base=d.iloc[i-15:i]
        segs=[base.iloc[:5],base.iloc[5:10],base.iloc[10:15]]
        ranges=[(s["High"].max()-s["Low"].min())/s["Close"].mean() for s in segs]
        vols=[s["Volume"].mean() for s in segs]
        atr_ratio=f(row["ATR10"])/f(row["ATR40"]) if f(row["ATR40"])>0 else np.nan
        if not np.isfinite(atr_ratio):
            continue

        for name,v in VARIANTS.items():
            rd=v["range_decay"]; vd=v["volume_decay"]
            range_ok=(ranges[1] <= ranges[0]*rd) and (ranges[2] <= ranges[1]*rd)
            vol_ok=(vols[1] <= vols[0]*vd) and (vols[2] <= vols[1]*vd)
            if not (range_ok and vol_ok and atr_ratio <= v["atr_ratio_max"]):
                continue
            rows.append({
                "Ticker":ticker,"SignalDate":pd.Timestamp(d.index[i]),"Direction":"Long",
                "Variant":name,"RVOL20":rvol,"BreakoutATR":br,
                "CloseLocation":loc,"BaseRangePct":ranges[-1],"ATRCompression":1-atr_ratio,
                "Trend20":f(row["Trend20"]),"Trend50":f(row["Trend50"]),"RS_SPY_20":f(row["RS_SPY_20"]),
                "MarketRegime":str(regime.iloc[i]),"ATR20":atr,"CloseSignal":close,
                "SignalRisk":atr,"SetupScore":np.nan
            })
        # mirrored downside VCP
        if close >= resistance:
            pass
        support=f(row["LL_15"])
        if not np.isfinite(support) or close >= support or loc > float(cfg["signal"]["close_location_short_max"]):
            continue
        brd=(support-close)/atr
        if brd < float(cfg["signal"]["breakout_atr_min"]):
            continue
        for name,v in VARIANTS.items():
            rd=v["range_decay"]; vd=v["volume_decay"]
            range_ok=(ranges[1] <= ranges[0]*rd) and (ranges[2] <= ranges[1]*rd)
            vol_ok=(vols[1] <= vols[0]*vd) and (vols[2] <= vols[1]*vd)
            if not (range_ok and vol_ok and atr_ratio <= v["atr_ratio_max"]):
                continue
            rows.append({
                "Ticker":ticker,"SignalDate":pd.Timestamp(d.index[i]),"Direction":"Short",
                "Variant":name,"RVOL20":rvol,"BreakoutATR":brd,
                "CloseLocation":loc,"BaseRangePct":ranges[-1],"ATRCompression":1-atr_ratio,
                "Trend20":f(row["Trend20"]),"Trend50":f(row["Trend50"]),"RS_SPY_20":f(row["RS_SPY_20"]),
                "MarketRegime":str(regime.iloc[i]),"ATR20":atr,"CloseSignal":close,
                "SignalRisk":atr,"SetupScore":np.nan
            })
    return pd.DataFrame(rows)


def label_trade(px, idx, direction, risk, horizon, target_r):
    if idx+1>=len(px) or risk<=0: return 0, "invalid"
    entry=f(px.iloc[idx+1]["Open"])
    if not np.isfinite(entry): return 0,"bad_open"
    stop=entry-risk if direction=="Long" else entry+risk
    target=entry+target_r*risk if direction=="Long" else entry-target_r*risk
    future=px.iloc[idx+1:min(idx+1+horizon,len(px))]
    for _,b in future.iterrows():
        hi,lo=f(b["High"]),f(b["Low"])
        if direction=="Long":
            st,ta=lo<=stop, hi>=target
        else:
            st,ta=hi>=stop, lo<=target
        if st and ta: return 0,"ambiguous"
        if ta: return 1,"target"
        if st: return 0,"stop"
    return 0,"timeout"


def main():
    cfg=load_config()
    universe=fetch_us_universe(ROOT/"data"/"universe.csv")
    syms=universe["YahooSymbol"].dropna().tolist()
    max_symbols=int(os.getenv("SCANNER_MAX_SYMBOLS","500"))
    if len(syms)>max_symbols:
        last=len(syms)-1
        idxs=[round(j*last/(max_symbols-1)) for j in range(max_symbols)]
        syms=[syms[x] for x in idxs]
    period=os.getenv("SCANNER_PERIOD","3y")
    prices=download_daily(syms,period=period,batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")),sleep_seconds=.75)
    spy=download_daily(["SPY"],period=period,batch_size=1,sleep_seconds=.1).get("SPY")
    if spy is None or spy.empty: raise RuntimeError("SPY download failed")
    regime=classify_regime(spy)
    signals=[]
    for t,p in prices.items():
        try:
            feat=add_features(p,spy=spy)
            reg=regime.reindex(feat.index).ffill().fillna("Neutral")
            s=detect_vcp(feat,t,cfg,reg)
            if not s.empty: signals.append(s)
        except Exception as e:
            print(f"SKIP {t}: {e}")
    sig=pd.concat(signals,ignore_index=True) if signals else pd.DataFrame()
    if sig.empty: raise RuntimeError("No VCP signals found")

    rows=[]
    for _,s in sig.iterrows():
        px=prices.get(s["Ticker"])
        if px is None: continue
        px=px.sort_index()
        try: idx=px.index.get_loc(pd.Timestamp(s["SignalDate"]))
        except KeyError: continue
        if isinstance(idx,slice): continue
        for h in HORIZONS:
            for tr in TARGETS:
                o,reason=label_trade(px,int(idx),s["Direction"],float(s["SignalRisk"]),h,tr)
                rows.append({**s.to_dict(),"HorizonDays":h,"TargetR":tr,"Outcome":o,"Reason":reason,"Half":half_label(pd.Timestamp(s["SignalDate"]))})
    out=ROOT/"artifacts"/"vcp_validation_v6"
    out.mkdir(parents=True,exist_ok=True)
    r=pd.DataFrame(rows)
    if r.empty: raise RuntimeError("No VCP outcomes")
    summary=(r.groupby(["Variant","Direction","HorizonDays","TargetR"],dropna=False)
        .agg(N=("Outcome","size"),SuccessRate=("Outcome","mean"))
        .reset_index())
    summary["ExpectancyR"]=summary["SuccessRate"]*(1+summary["TargetR"])-1
    by_half=(r.groupby(["Variant","Direction","Half","HorizonDays","TargetR"],dropna=False)
        .agg(N=("Outcome","size"),SuccessRate=("Outcome","mean"))
        .reset_index())
    by_half["ExpectancyR"]=by_half["SuccessRate"]*(1+by_half["TargetR"])-1
    by_variant=(r.groupby(["Variant","Direction"],dropna=False)
        .agg(N=("Outcome","size"),Signals=("SignalDate","nunique"))
        .reset_index())
    # Use the 1R/10d row as a neutral primary diagnostic and report calendar persistence.
    primary=by_half[(by_half["TargetR"]==1.0)&(by_half["HorizonDays"]==10)].copy()
    persistence=(primary.groupby(["Variant","Direction"])
        .agg(PositiveHalves=("ExpectancyR",lambda x:int((x>0).sum())),
             HalvesObserved=("ExpectancyR","size"),
             MeanHalfExpectancyR=("ExpectancyR","mean"))
        .reset_index())
    primary2=summary[(summary["TargetR"]==1.0)&(summary["HorizonDays"]==10)].copy()
    persistence=persistence.merge(primary2[["Variant","Direction","N","SuccessRate","ExpectancyR"]],on=["Variant","Direction"],how="left")
    persistence["RobustAt1R10d"]=(
        (persistence["PositiveHalves"]>=3)&
        (persistence["HalvesObserved"]>=3)&
        (persistence["SuccessRate"]>1/2)&
        (persistence["ExpectancyR"]>0)
    )
    sig.to_csv(out/"vcp_signals.csv",index=False)
    summary.to_csv(out/"target_summary.csv",index=False)
    by_half.to_csv(out/"by_half.csv",index=False)
    persistence.to_csv(out/"persistence.csv",index=False)
    r.to_parquet(out/"outcomes.parquet",index=False)
    meta={"symbols_requested":len(syms),"symbols_downloaded":len(prices),"vcp_signals":int(len(sig)),"variants":list(VARIANTS),"targets":TARGETS,"horizons":HORIZONS,"primary_rule":"1R target within 10 sessions; persistence requires positive expectancy in >=3 observed half-years, >50% success and positive combined expectancy.","note":"V6 is hypothesis-driven pattern validation; no production rule is selected automatically."}
    (out/"summary.json").write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2))
    print("\nPERSISTENCE")
    print(persistence.to_string(index=False))


if __name__=="__main__":
    main()
