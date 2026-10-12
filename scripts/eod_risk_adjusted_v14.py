from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
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
PRIMARY_H=5
PRIMARY_K=40
RIDGE_ALPHA=25.0
TARGET_CLIP=3.0
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
def sample_v13(s):
    a=sample_v8(s)|sample_v9(s)|sample_v10(s)|sample_v12(s)
    return set(sample([x for x in s if x not in a],1000))
def sample_v14(s,n=1000):
    used=sample_v8(s)|sample_v9(s)|sample_v10(s)|sample_v12(s)|sample_v13(s)
    return sample([x for x in s if x not in used],n)

def make_features(px,spy,ticker):
    f=add_features(px,spy=spy).copy().sort_index()
    close=pd.to_numeric(f["Close"],errors="coerce")
    open_=pd.to_numeric(f["Open"],errors="coerce")
    volume=pd.to_numeric(f["Volume"],errors="coerce")
    spy_close=pd.to_numeric(spy["Close"],errors="coerce").sort_index()
    spy_open=pd.to_numeric(spy["Open"],errors="coerce").sort_index()
    spy_past={h:spy_close.pct_change(h,fill_method=None).reindex(f.index) for h in (1,3,5,10,20,60,120,252)}
    spy_forward={h:(spy_close.shift(-h)/spy_open.shift(-1)-1).reindex(f.index) for h in HORIZONS}
    for h in (1,3,5,10,20,60,120,252):
        f[f"Ret{h}"]=close.pct_change(h,fill_method=None)
    for h in (1,3,5,10,20,60):
        f[f"Excess{h}"]=f[f"Ret{h}"]-spy_past[h]
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
    f["VolumeTrend5_20"]=volume.rolling(5,min_periods=5).mean()/volume.rolling(20,min_periods=20).mean()
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
    f["GapPct"]=open_/close.shift(1)-1
    f["IntradayReturn"]=close/open_-1
    f["TrueRangePct"]=f["TR"]/close
    f["Ticker"]=ticker
    f["SignalDate"]=pd.to_datetime(f.index)
    for h in HORIZONS:
        f[f"ForwardRet{h}"]=close.shift(-h)/open_.shift(-1)-1
        f[f"ForwardExcess{h}"]=f[f"ForwardRet{h}"]-spy_forward[h]
    denom=(f["ATR20Pct"]*np.sqrt(PRIMARY_H)).replace(0,np.nan)
    f["TargetRiskAdj5"]=(f["ForwardExcess5"]/denom).clip(-TARGET_CLIP,TARGET_CLIP)
    f["RiskAdjMomentum20"]=f["Excess20"]/(f["ATR20Pct"]*np.sqrt(20))
    f["RiskAdjMomentum60"]=f["Excess60"]/(f["ATR20Pct"]*np.sqrt(60))
    f["RawMomentum20"]=f["Excess20"]
    return f.replace([np.inf,-np.inf],np.nan)

def build_panel(prices,spy):
    frames=[]
    for i,(ticker,px) in enumerate(prices.items(),1):
        try: frames.append(make_features(px,spy,ticker))
        except Exception as e: print(f"FEATURE SKIP {ticker}: {e}",flush=True)
        if i%100==0: print(f"FEATURE BUILT {i}",flush=True)
    if not frames: raise RuntimeError("No feature frames built")
    p=pd.concat(frames,ignore_index=True)
    # Features and outcomes are computed before eligibility is applied, on the full per-ticker history.
    p=p[p["Close"].ge(MIN_PRICE)&p["DollarVolume20"].ge(MIN_DV)].copy()
    return p.sort_values(["SignalDate","Ticker"]).reset_index(drop=True)

def fit_regressor(train):
    model=Pipeline([
        ("scale",StandardScaler()),
        ("ridge",Ridge(alpha=RIDGE_ALPHA,fit_intercept=True)),
    ])
    model.fit(train[FEATURES],train["TargetRiskAdj5"].astype(float))
    return model

def daily_portfolio(frame,score_col,direction,top_k,horizon,partition):
    rows=[]
    for date,g in frame.groupby("SignalDate",sort=True):
        g=g.dropna(subset=[score_col,f"ForwardRet{horizon}",f"ForwardExcess{horizon}"]).copy()
        if g.empty:continue
        g=g.sort_values(score_col,ascending=(direction=="Short")).head(top_k)
        if g.empty:continue
        sign=1.0 if direction=="Long" else -1.0
        raw=sign*g[f"ForwardRet{horizon}"]
        alpha=sign*g[f"ForwardExcess{horizon}"]
        rows.append({
            "SignalDate":pd.Timestamp(date),"Partition":partition,"Direction":direction,
            "Ranking":score_col,"TopK":top_k,"Horizon":horizon,"N":len(g),
            "MeanTradeReturn":float(raw.mean()),"MeanTradeAlphaVsSPY":float(alpha.mean()),
            "HitRate":float((g[f"ForwardRet{horizon}"]>0).mean() if direction=="Long" else (g[f"ForwardRet{horizon}"]<0).mean()),
        })
    return pd.DataFrame(rows)

def block_bootstrap_ci(values,reps=4000,block=5,seed=42):
    x=np.asarray(values,dtype=float);x=x[np.isfinite(x)];n=len(x)
    if n<10:return (float(x.mean()) if n else np.nan,np.nan,np.nan)
    rng=np.random.default_rng(seed);means=np.empty(reps);starts=np.arange(n);needed=int(np.ceil(n/block))
    for i in range(reps):
        choices=rng.choice(starts,size=needed,replace=True)
        sample_=np.concatenate([x[(k+np.arange(block))%n] for k in choices])[:n]
        means[i]=sample_.mean()
    return float(x.mean()),float(np.quantile(means,.025)),float(np.quantile(means,.975))

def daily_information_coefficients(frame,partition):
    rows=[]
    for date,g in frame.groupby("SignalDate",sort=True):
        g=g.dropna(subset=["TargetRiskAdj5","ModelScore","RiskAdjMomentum20","RiskAdjMomentum60","RawMomentum20"])
        if len(g)<20:continue
        for score in ("ModelScore","RiskAdjMomentum20","RiskAdjMomentum60","RawMomentum20"):
            corr=spearmanr(g[score],g["TargetRiskAdj5"]).statistic
            rows.append({"SignalDate":pd.Timestamp(date),"Partition":partition,"Ranking":score,"N":len(g),"SpearmanIC":float(corr)})
    return pd.DataFrame(rows)

def main():
    data=ROOT/"data";out=ROOT/"artifacts"/"eod_risk_adjusted_v14";out.mkdir(parents=True,exist_ok=True)
    uni=fetch_us_universe(data/"universe.csv",refresh=True)
    symbols=sample_v14(uni["YahooSymbol"].dropna().astype(str).tolist(),int(os.getenv("SCANNER_MAX_SYMBOLS","1000")))
    period=os.getenv("SCANNER_PERIOD","10y")
    print(f"Selected fresh V14 symbols: {len(symbols)}",flush=True)
    prices=download_daily(symbols,period=period,batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")),sleep_seconds=.75)
    print(f"Downloaded {len(prices)} symbols",flush=True)
    spy=download_daily(["SPY"],period=period,batch_size=1,sleep_seconds=.2).get("SPY")
    if spy is None or spy.empty:raise RuntimeError("SPY benchmark download failed")
    p=build_panel(prices,spy)
    if p.empty:raise RuntimeError("No eligible rows after EOD filters")
    dates=pd.Series(p["SignalDate"].drop_duplicates().sort_values().tolist())
    if len(dates)<200:raise RuntimeError(f"Too few signal dates: {len(dates)}")
    train_end=pd.Timestamp(dates.iloc[int(len(dates)*.60)])
    holdout_start=pd.Timestamp(dates.iloc[int(len(dates)*.80)])
    print(f"Panel {len(p):,} rows / {p.Ticker.nunique()} tickers / {len(dates)} dates",flush=True)
    print(f"Chronological split: train < {train_end.date()}, validation < {holdout_start.date()}, holdout >= {holdout_start.date()}",flush=True)
    # Freeze all modelling choices before looking at validation/holdout outcomes.
    needed=FEATURES+["TargetRiskAdj5"]+[f"ForwardRet{h}" for h in HORIZONS]+[f"ForwardExcess{h}" for h in HORIZONS]
    clean=p.dropna(subset=needed).copy()
    train=clean[clean["SignalDate"]<train_end].copy()
    val=clean[(clean["SignalDate"]>=train_end)&(clean["SignalDate"]<holdout_start)].copy()
    test=clean[clean["SignalDate"]>=holdout_start].copy()
    if min(len(train),len(val),len(test))<5000:raise RuntimeError(f"Partitions too small: {len(train)}/{len(val)}/{len(test)}")
    model=fit_regressor(train)
    for frame in (val,test):
        frame["ModelScore"]=model.predict(frame[FEATURES])
        frame["RiskAdjMomentum20"]=frame["Excess20"]/(frame["ATR20Pct"]*np.sqrt(20))
        frame["RiskAdjMomentum60"]=frame["Excess60"]/(frame["ATR20Pct"]*np.sqrt(60))
        frame["RawMomentum20"]=frame["Excess20"]
    val.to_parquet(out/"validation_rows.parquet",index=False)
    test.to_parquet(out/"holdout_rows.parquet",index=False)
    panel_meta={"symbols_requested":len(symbols),"symbols_downloaded":len(prices),"eligible_rows":len(p),
        "eligible_tickers":int(p.Ticker.nunique()),"unique_dates":int(p.SignalDate.nunique()),
        "train_rows":len(train),"validation_rows":len(val),"holdout_rows":len(test),
        "train_start":str(train.SignalDate.min().date()),"train_end":str(train.SignalDate.max().date()),
        "validation_start":str(val.SignalDate.min().date()),"validation_end":str(val.SignalDate.max().date()),
        "holdout_start":str(test.SignalDate.min().date()),"holdout_end":str(test.SignalDate.max().date())}
    metrics=[];daily=[];ics=[]
    for partition,frame in (("Validation",val),("Holdout",test)):
        ics.append(daily_information_coefficients(frame,partition))
        for direction in ("Long","Short"):
            for score in ("ModelScore","RiskAdjMomentum20","RiskAdjMomentum60","RawMomentum20"):
                for k in TOP_KS:
                    for h in HORIZONS:
                        d=daily_portfolio(frame,score,direction,k,h,partition)
                        if d.empty:continue
                        daily.append(d)
                        metrics.append({"Partition":partition,"Direction":direction,"Ranking":score,"TopK":k,"Horizon":h,
                            "N":int(d["N"].sum()),"Dates":len(d),"MeanDailyTradeReturn":float(d["MeanTradeReturn"].mean()),
                            "MeanDailyAlphaVsSPY":float(d["MeanTradeAlphaVsSPY"].mean()),"HitRate":float(d["HitRate"].mean())})
    metrics_df=pd.DataFrame(metrics);daily_df=pd.concat(daily,ignore_index=True);ic_df=pd.concat(ics,ignore_index=True)
    metrics_df.to_csv(out/"ranking_metrics.csv",index=False);daily_df.to_csv(out/"daily_portfolio_returns.csv",index=False)
    ic_df.to_csv(out/"daily_information_coefficients.csv",index=False)
    # Primary predeclared test: model vs 20-day risk-adjusted momentum, Top 40, 5 sessions.
    ci_rows=[]
    for direction in ("Long","Short"):
        model_daily=daily_df[(daily_df.Partition=="Holdout")&(daily_df.Direction==direction)&(daily_df.Ranking=="ModelScore")&(daily_df.TopK==PRIMARY_K)&(daily_df.Horizon==PRIMARY_H)].sort_values("SignalDate")
        base_daily=daily_df[(daily_df.Partition=="Holdout")&(daily_df.Direction==direction)&(daily_df.Ranking=="RiskAdjMomentum20")&(daily_df.TopK==PRIMARY_K)&(daily_df.Horizon==PRIMARY_H)].sort_values("SignalDate")
        for name,df in (("ModelScore",model_daily),("RiskAdjMomentum20",base_daily)):
            mean,lo,hi=block_bootstrap_ci(df["MeanTradeAlphaVsSPY"],seed=(110 if direction=="Long" else 111)+(0 if name=="ModelScore" else 10))
            ci_rows.append({"Direction":direction,"Ranking":name,"Dates":len(df),"MeanDailyTradeReturn":float(df["MeanTradeReturn"].mean()),
                "MeanDailyAlphaVsSPY":mean,"AlphaCI95Low":lo,"AlphaCI95High":hi,
                "PositiveAlphaSupported":bool(lo>0)})
        paired=pd.merge(model_daily[["SignalDate","MeanTradeAlphaVsSPY"]],base_daily[["SignalDate","MeanTradeAlphaVsSPY"]],
            on="SignalDate",suffixes=("_model","_baseline"))
        diff,lo,hi=block_bootstrap_ci(paired["MeanTradeAlphaVsSPY_model"]-paired["MeanTradeAlphaVsSPY_baseline"],seed=210 if direction=="Long" else 211)
        ci_rows.append({"Direction":direction,"Ranking":"ModelMinusRiskAdjMomentum20","Dates":len(paired),
            "MeanDailyTradeReturn":np.nan,"MeanDailyAlphaVsSPY":diff,"AlphaCI95Low":lo,"AlphaCI95High":hi,
            "PositiveAlphaSupported":bool(lo>0)})
    ci_df=pd.DataFrame(ci_rows);ci_df.to_csv(out/"primary_holdout_confidence_intervals.csv",index=False)
    ic_summary=ic_df[ic_df.Partition=="Holdout"].groupby("Ranking",observed=True).agg(
        Dates=("SpearmanIC","count"),MeanIC=("SpearmanIC","mean"),MedianIC=("SpearmanIC","median"),
        PositiveICPct=("SpearmanIC",lambda x:float((x>0).mean())),ICStd=("SpearmanIC","std")).reset_index()
    ic_summary["ICIR"]=ic_summary["MeanIC"]/ic_summary["ICStd"].replace(0,np.nan)
    ic_summary.to_csv(out/"holdout_ic_summary.csv",index=False)
    summary={"research_version":"V14 EOD Risk-Adjusted Continuous Return Ranking","symbols_requested":len(symbols),"symbols_downloaded":len(prices),
        "eligible_rows":len(p),"eligible_tickers":int(p.Ticker.nunique()),"unique_dates":int(p.SignalDate.nunique()),
        "target":"Five-session forward excess return vs SPY from next-session open to close, scaled by signal-date ATR20% times sqrt(5), clipped to +/-3",
        "feature_timing":"All features use signal-date daily data only; rolling and forward outcomes computed from full stock histories before eligibility filtering",
        "model":"Fixed standardized Ridge regression, alpha=25; no hyperparameter search",
        "baselines":["20-day risk-adjusted excess momentum","60-day risk-adjusted excess momentum","20-day raw excess momentum"],
        "split":"60% chronological training / 20% validation / 20% untouched holdout; model fit on training only",
        "primary_test":"Equal-weight Top 40 / Bottom 40; 5-session forward return, alpha vs SPY, block-bootstrap CI",
        "production_gate":"Research only; no probability/A-tier production switch. Require positive net-relevant holdout result, positive confidence lower bound, and robust replication.",
        "survivorship_note":"Uses current listed universe and a fresh current-universe ticker partition; historical delisted stocks and point-in-time membership are unavailable",
        "partitions":panel_meta,"primary_holdout_confidence_intervals":ci_df.to_dict(orient="records"),
        "holdout_ic_summary":ic_summary.to_dict(orient="records")}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")
    print(json.dumps(summary,indent=2,default=str),flush=True)
    print("\nPRIMARY HOLDOUT CIs\n"+ci_df.to_string(index=False),flush=True)
    print("\nHOLDOUT IC SUMMARY\n"+ic_summary.to_string(index=False),flush=True)
    print("\nTOP-40 / 5-SESSION METRICS\n"+metrics_df[(metrics_df.Partition=="Holdout")&(metrics_df.TopK==40)&(metrics_df.Horizon==5)].to_string(index=False),flush=True)

if __name__=="__main__":main()
