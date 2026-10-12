from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from scanner.config import ROOT
from scanner.data import download_daily
from scanner.features import add_features
from scanner.universe import fetch_us_universe

MIN_PRICE=5.0
MIN_DV=20_000_000.0
HORIZONS=(1,3,5,10)
TOP_KS=(10,20,40,80)
PRIMARY_HORIZON=5
FEATURES=[
"Ret1","Ret3","Ret5","Ret10","Ret20","Ret60","Ret120","Ret252",
"Excess1","Excess3","Excess5","Excess10","Excess20","Excess60",
"EMADist20","EMADist50","EMADist200","EMASlope20_5","EMASlope50_10",
"ATR20Pct","ATR10Pct","ATRCompression","RealizedVol10","RealizedVol20",
"RVOL20","VolumeAcceleration","VolumeTrend5_20","DollarVolume20Log",
"CloseLoc20","CloseLoc60","DistFrom20High","DistFrom60High","DistFrom252High",
"DistFrom20Low","DistFrom60Low","GapPct","IntradayReturn","TrueRangePct",
"Trend20","Trend50","Trend200"]

def sample(symbols,n):
    symbols=list(dict.fromkeys(symbols))
    if len(symbols)<=n:return symbols
    last=len(symbols)-1
    return [symbols[round(i*last/(n-1))] for i in range(n)]

def sample_v8(s): return set(sample(s,500))
def sample_v9(s):
    a=sample_v8(s); return set(sample([x for x in s if x not in a],1000))
def sample_v10(s):
    a=sample_v8(s)|sample_v9(s); return set(sample([x for x in s if x not in a],1000))
def sample_v12(s):
    a=sample_v8(s)|sample_v9(s)|sample_v10(s); return set(sample([x for x in s if x not in a],1000))
def sample_v13(s,n=1000):
    used=sample_v8(s)|sample_v9(s)|sample_v10(s)|sample_v12(s)
    return sample([x for x in s if x not in used],n)

def make_features(px,spy,ticker):
    f=add_features(px,spy=spy).copy().sort_index()
    close=pd.to_numeric(f["Close"],errors="coerce")
    vol=pd.to_numeric(f["Volume"],errors="coerce")
    spy_close=pd.to_numeric(spy["Close"],errors="coerce").sort_index()
    spy_ret={h:spy_close.pct_change(h,fill_method=None).reindex(f.index) for h in HORIZONS}
    for h in HORIZONS:
        f[f"Ret{h}"]=close.pct_change(h,fill_method=None)
        f[f"Excess{h}"]=f[f"Ret{h}"]-spy_ret[h]
    for h in (20,60,120,252):
        f[f"Ret{h}"]=close.pct_change(h,fill_method=None)
    for h in (20,60):
        f[f"Excess{h}"]=f[f"Ret{h}"]-spy_close.pct_change(h,fill_method=None).reindex(f.index)
    f["EMADist20"]=(close-f["EMA20"])/close
    f["EMADist50"]=(close-f["EMA50"])/close
    f["EMADist200"]=(close-f["EMA200"])/close
    f["EMASlope20_5"]=f["EMA20"]/f["EMA20"].shift(5)-1
    f["EMASlope50_10"]=f["EMA50"]/f["EMA50"].shift(10)-1
    f["ATR20Pct"]=f["ATR20"]/close
    f["ATR10Pct"]=f["ATR10"]/close
    ret=close.pct_change(fill_method=None)
    f["RealizedVol10"]=ret.rolling(10,min_periods=10).std()
    f["RealizedVol20"]=ret.rolling(20,min_periods=20).std()
    f["VolumeTrend5_20"]=vol.rolling(5,min_periods=5).mean()/vol.rolling(20,min_periods=20).mean()
    f["DollarVolume20Log"]=np.log1p(pd.to_numeric(f["DollarVolume20"],errors="coerce").clip(lower=0))
    hh20=f["High"].rolling(20,min_periods=20).max().shift(1)
    ll20=f["Low"].rolling(20,min_periods=20).min().shift(1)
    hh60=f["High"].rolling(60,min_periods=60).max().shift(1)
    ll60=f["Low"].rolling(60,min_periods=60).min().shift(1)
    f["DistFrom20High"]=close/hh20-1
    f["DistFrom60High"]=close/hh60-1
    f["DistFrom252High"]=close/close.rolling(252,min_periods=252).max()-1
    f["DistFrom20Low"]=close/ll20-1
    f["DistFrom60Low"]=close/ll60-1
    f["CloseLoc20"]=(close-ll20)/(hh20-ll20).replace(0,np.nan)
    f["CloseLoc60"]=(close-ll60)/(hh60-ll60).replace(0,np.nan)
    f["GapPct"]=pd.to_numeric(f["Open"],errors="coerce")/close.shift(1)-1
    f["IntradayReturn"]=close/pd.to_numeric(f["Open"],errors="coerce")-1
    f["TrueRangePct"]=f["TR"]/close
    f["Ticker"]=ticker
    f["SignalDate"]=pd.to_datetime(f.index)
    for h in HORIZONS:
        f[f"ForwardRet{h}"]=close.shift(-h)/close-1
        f[f"ForwardExcess{h}"]=f[f"ForwardRet{h}"]-spy_ret[h]
    return f.replace([np.inf,-np.inf],np.nan)

def build_panel(prices,spy):
    frames=[]
    for i,(ticker,px) in enumerate(prices.items(),1):
        try: frames.append(make_features(px,spy,ticker))
        except Exception as e: print(f"FEATURE SKIP {ticker}: {e}",flush=True)
        if i%100==0: print(f"FEATURE BUILT {i}",flush=True)
    if not frames: raise RuntimeError("No feature frames built")
    p=pd.concat(frames,ignore_index=True)
    p=p[p["Close"].ge(MIN_PRICE)&p["DollarVolume20"].ge(MIN_DV)].copy()
    p=p.sort_values(["Ticker","SignalDate"]).reset_index(drop=True)
    # Recalculate historical returns after panel assembly, preserving identical feature definitions across symbols.
    spy_close=pd.to_numeric(spy["Close"],errors="coerce").sort_index()
    for h in HORIZONS:
        p[f"Ret{h}"]=p.groupby("Ticker",observed=True)["Close"].pct_change(h,fill_method=None)
        p[f"ForwardRet{h}"]=p.groupby("Ticker",observed=True)["Close"].shift(-h)/p["Close"]-1
        p[f"ForwardExcess{h}"]=p[f"ForwardRet{h}"]-pd.to_datetime(p["SignalDate"]).map(spy_close.pct_change(h,fill_method=None))
    for h in (20,60,120,252):
        p[f"Ret{h}"]=p.groupby("Ticker",observed=True)["Close"].pct_change(h,fill_method=None)
    for h in (20,60):
        p[f"Excess{h}"]=p[f"Ret{h}"]-pd.to_datetime(p["SignalDate"]).map(spy_close.pct_change(h,fill_method=None))
    rank=p.dropna(subset=["ForwardExcess5"]).groupby("SignalDate",observed=True)["ForwardExcess5"].rank(pct=True,method="average")
    p["LongLabel"]=np.nan;p["ShortLabel"]=np.nan
    p.loc[rank.index,"LongLabel"]=(rank>=.80).astype(float)
    p.loc[rank.index,"ShortLabel"]=(rank<=.20).astype(float)
    return p.sort_values(["SignalDate","Ticker"]).reset_index(drop=True)

def fit_model(train,label):
    model=Pipeline([("scale",StandardScaler()),("clf",LogisticRegression(C=.25,class_weight="balanced",solver="lbfgs",max_iter=2000,random_state=42))])
    model.fit(train[FEATURES],train[label].astype(int))
    return model

def daily_portfolio(d,ranking,direction,top_k,h):
    rows=[]
    for date,g in d.groupby("SignalDate",sort=True):
        g=g.dropna(subset=[ranking,f"ForwardRet{h}",f"ForwardExcess{h}"]).sort_values(ranking,ascending=False).head(top_k)
        if g.empty: continue
        sign=1 if direction=="Long" else -1
        fr=sign*g[f"ForwardRet{h}"]; ex=sign*g[f"ForwardExcess{h}"]
        rows.append({"SignalDate":pd.Timestamp(date),"Direction":direction,"Ranking":ranking,"TopK":top_k,"Horizon":h,
                     "MeanTradeReturn":float(fr.mean()),"MeanTradeExcess":float(ex.mean()),
                     "HitRate":float((g[f"ForwardRet{h}"]>0).mean() if direction=="Long" else (g[f"ForwardRet{h}"]<0).mean()),"N":len(g)})
    return pd.DataFrame(rows)

def bootstrap_ci(x,reps=4000,block=5,seed=42):
    a=np.asarray(x,dtype=float);a=a[np.isfinite(a)];n=len(a)
    if n<10:return (float(np.mean(a)) if n else np.nan,np.nan,np.nan)
    rng=np.random.default_rng(seed);out=np.empty(reps);starts=np.arange(n);blocks=int(np.ceil(n/block))
    for i in range(reps):
        ids=rng.choice(starts,size=blocks,replace=True)
        out[i]=np.concatenate([a[(j+np.arange(block))%n] for j in ids])[:n].mean()
    return float(a.mean()),float(np.quantile(out,.025)),float(np.quantile(out,.975))

def main():
    data=ROOT/"data";out=ROOT/"artifacts"/"eod_replication_v13";out.mkdir(parents=True,exist_ok=True)
    uni=fetch_us_universe(data/"universe.csv",refresh=True)
    symbols=sample_v13(uni["YahooSymbol"].dropna().astype(str).tolist(),int(os.getenv("SCANNER_MAX_SYMBOLS","1000")))
    print(f"Selected fresh V13 symbols: {len(symbols)}",flush=True)
    period=os.getenv("SCANNER_PERIOD","10y")
    prices=download_daily(symbols,period=period,batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")),sleep_seconds=.75)
    print(f"Downloaded {len(prices)} symbols",flush=True)
    spy=download_daily(["SPY"],period=period,batch_size=1,sleep_seconds=.2).get("SPY")
    if spy is None or spy.empty:raise RuntimeError("SPY download failed")
    p=build_panel(prices,spy)
    if p.empty:raise RuntimeError("No eligible EOD rows")
    dates=pd.Series(p["SignalDate"].drop_duplicates().sort_values().tolist())
    if len(dates)<200:raise RuntimeError(f"Too few dates: {len(dates)}")
    train_end=pd.Timestamp(dates.iloc[int(len(dates)*.60)])
    holdout_start=pd.Timestamp(dates.iloc[int(len(dates)*.80)])
    p.to_parquet(out/"v13_eod_panel.parquet",index=False)
    all_metrics=[];all_class=[];metas={};daily_all=[]
    # Frozen from V12 validation: do not reselect rankings using V13 validation or holdout.
    selected={"Long":"Momentum20Score","Short":"ModelProbability"}
    for direction in ("Long","Short"):
        label="LongLabel" if direction=="Long" else "ShortLabel"
        required=FEATURES+[label]+[f"ForwardRet{h}" for h in HORIZONS]+[f"ForwardExcess{h}" for h in HORIZONS]
        d=p.dropna(subset=required).copy()
        train=d[d["SignalDate"]<train_end].copy()
        val=d[(d["SignalDate"]>=train_end)&(d["SignalDate"]<holdout_start)].copy()
        test=d[d["SignalDate"]>=holdout_start].copy()
        if min(len(train),len(val),len(test))<5000:raise RuntimeError(f"{direction} too few rows: {len(train)}/{len(val)}/{len(test)}")
        model=fit_model(train,label)
        for part,frame in (("Validation",val),("Holdout",test)):
            frame["ModelProbability"]=model.predict_proba(frame[FEATURES])[:,1]
            frame["Momentum20Score"]=frame["Excess20"] if direction=="Long" else -frame["Excess20"]
            y=frame[label].astype(int);prob=frame["ModelProbability"].to_numpy()
            all_class.append({"Direction":direction,"Partition":part,"N":len(frame),"BaseRate":float(y.mean()),
                "AUC":float(roc_auc_score(y,prob)) if y.nunique()>1 else np.nan,"Brier":float(brier_score_loss(y,prob))})
            for ranking in ("ModelProbability","Momentum20Score"):
                for k in TOP_KS:
                    for h in HORIZONS:
                        port=daily_portfolio(frame,ranking,direction,k,h)
                        if port.empty:continue
                        port["Partition"]=part
                        daily_all.append(port)
                        all_metrics.append({"Direction":direction,"Partition":part,"Ranking":ranking,"TopK":k,"Horizon":h,
                            "N":int(port["N"].sum()),"Dates":int(len(port)),"MeanDailyTradeReturn":float(port["MeanTradeReturn"].mean()),
                            "MeanDailyTradeExcess":float(port["MeanTradeExcess"].mean()),"HitRate":float(port["HitRate"].mean())})
        test.to_parquet(out/f"{direction.lower()}_holdout_rows.parquet",index=False)
        pval=val["ModelProbability"] if "ModelProbability" in val else pd.Series(dtype=float)
        metas[direction]={"train_rows":len(train),"validation_rows":len(val),"holdout_rows":len(test),
            "train_start":str(train["SignalDate"].min().date()),"train_end":str(train["SignalDate"].max().date()),
            "validation_start":str(val["SignalDate"].min().date()),"validation_end":str(val["SignalDate"].max().date()),
            "holdout_start":str(test["SignalDate"].min().date()),"holdout_end":str(test["SignalDate"].max().date()),
            "frozen_primary_ranking":selected[direction],
            "holdout_AUC":float(roc_auc_score(test[label],test["ModelProbability"])) if test[label].nunique()>1 else None,
            "holdout_Brier":float(brier_score_loss(test[label],test["ModelProbability"]))}
    metrics=pd.DataFrame(all_metrics);classes=pd.DataFrame(all_class);daily=pd.concat(daily_all,ignore_index=True)
    metrics.to_csv(out/"ranking_metrics.csv",index=False);classes.to_csv(out/"classification_metrics.csv",index=False)
    daily.to_csv(out/"daily_portfolio_returns.csv",index=False)
    # Confidence intervals are a predeclared decision check for fixed Top-40/5-day rankings.
    ci_rows=[]
    for direction in ("Long","Short"):
        ranking=selected[direction]
        chosen=daily[(daily.Partition=="Holdout")&(daily.Direction==direction)&(daily.Ranking==ranking)&(daily.TopK==40)&(daily.Horizon==5)].sort_values("SignalDate")
        model=daily[(daily.Partition=="Holdout")&(daily.Direction==direction)&(daily.Ranking=="ModelProbability")&(daily.TopK==40)&(daily.Horizon==5)].sort_values("SignalDate")
        base=daily[(daily.Partition=="Holdout")&(daily.Direction==direction)&(daily.Ranking=="Momentum20Score")&(daily.TopK==40)&(daily.Horizon==5)].sort_values("SignalDate")
        mean,lo,hi=bootstrap_ci(chosen["MeanTradeExcess"],seed=100 if direction=="Long" else 101)
        diff=pd.merge(model[["SignalDate","MeanTradeExcess"]],base[["SignalDate","MeanTradeExcess"]],on="SignalDate",suffixes=("_model","_momentum"))
        dm,dl,dh=bootstrap_ci(diff["MeanTradeExcess_model"]-diff["MeanTradeExcess_momentum"],seed=200 if direction=="Long" else 201)
        ci_rows.append({"Direction":direction,"FrozenRanking":ranking,"Dates":len(chosen),"MeanDailyExcess":mean,"ExcessCI95Low":lo,"ExcessCI95High":hi,
            "ModelMinusMomentumMean":dm,"DiffCI95Low":dl,"DiffCI95High":dh,"PrimaryEdgeSupported":bool(lo>0 and dl>0)})
    cis=pd.DataFrame(ci_rows);cis.to_csv(out/"primary_holdout_confidence_intervals.csv",index=False)
    summary={"research_version":"V13 Frozen EOD Replication","symbols_requested":len(symbols),"symbols_downloaded":len(prices),
        "eligible_panel_rows":len(p),"eligible_tickers":int(p.Ticker.nunique()),"unique_dates":int(p.SignalDate.nunique()),
        "primary_objective":"Top 20% Long / bottom 20% Short of 5-day forward excess return vs SPY",
        "frozen_from_V12_validation":{"Long":"Momentum20Score","Short":"ModelProbability"},
        "split":"60% chronological train / 20% validation / 20% untouched holdout",
        "primary_test":"Top 40; 5-day holding period; mean daily equal-weight return and excess vs SPY; 5-session circular block bootstrap",
        "no_reselection":"V13 validation and holdout do not change the V12-based ranking choice, features, or model hyperparameters",
        "probability_warning":"Balanced-class logistic outputs are ranking scores only; Brier diagnostics are reported, but values are not approved as calibrated probabilities",
        "survivorship_note":"Uses today's listing universe and a fresh current-universe ticker partition; historic delisted names and point-in-time membership are unavailable",
        "directions":metas,"primary_holdout_confidence_intervals":cis.to_dict(orient="records")}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")
    print(json.dumps(summary,indent=2,default=str),flush=True)
    print("\nPRIMARY HOLDOUT CONFIDENCE INTERVALS\n"+cis.to_string(index=False),flush=True)
    print("\nTOP-40 HOLDOUT METRICS\n"+metrics[(metrics.Partition=="Holdout")&(metrics.TopK==40)&(metrics.Horizon==5)].to_string(index=False),flush=True)

if __name__=="__main__":main()
