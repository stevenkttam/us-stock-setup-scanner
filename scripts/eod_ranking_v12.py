from __future__ import annotations
import json,os
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from scanner.config import ROOT
from scanner.data import download_daily
from scanner.features import add_features
from scanner.universe import fetch_us_universe

MIN_PRICE=5.0; MIN_DV=20_000_000.0; H=(1,3,5,10); K=(10,20,40,80)
FEATURES=[
"Ret1","Ret3","Ret5","Ret10","Ret20","Ret60","Ret120","Ret252",
"Excess1","Excess3","Excess5","Excess10","Excess20","Excess60",
"EMADist20","EMADist50","EMADist200","EMASlope20_5","EMASlope50_10",
"ATR20Pct","ATR10Pct","ATRCompression","RealizedVol10","RealizedVol20",
"RVOL20","VolumeAcceleration","VolumeTrend5_20","DollarVolume20Log",
"CloseLoc20","CloseLoc60","DistFrom20High","DistFrom60High","DistFrom252High",
"DistFrom20Low","DistFrom60Low","GapPct","IntradayReturn","TrueRangePct",
"Trend20","Trend50","Trend200"]

def sample(s,n):
 s=list(dict.fromkeys(s))
 if len(s)<=n:return s
 last=len(s)-1
 return [s[round(i*last/(n-1))] for i in range(n)]

def fresh(s,n=1000):
 def f8(x):return set(sample(x,500))
 def f9(x):
  u=f8(x);return set(sample([z for z in x if z not in u],1000))
 def f10(x):
  u=f8(x)|f9(x);return set(sample([z for z in x if z not in u],1000))
 u=f8(s)|f9(s)|f10(s)
 return sample([z for z in s if z not in u],n)

def build(px,spy,t):
 f=add_features(px,spy=spy).copy().sort_index(); c=pd.to_numeric(f.Close,errors="coerce"); v=pd.to_numeric(f.Volume,errors="coerce")
 sp=pd.to_numeric(spy.Close,errors="coerce").sort_index()
 sr={h:sp.pct_change(h,fill_method=None).reindex(f.index) for h in H}
 for h in H:
  f[f"Ret{h}"]=c.pct_change(h,fill_method=None); f[f"Excess{h}"]=f[f"Ret{h}"]-sr[h]
 f["EMADist20"]=(c-f.EMA20)/c; f["EMADist50"]=(c-f.EMA50)/c; f["EMADist200"]=(c-f.EMA200)/c
 f["EMASlope20_5"]=f.EMA20/f.EMA20.shift(5)-1; f["EMASlope50_10"]=f.EMA50/f.EMA50.shift(10)-1
 f["ATR20Pct"]=f.ATR20/c; f["ATR10Pct"]=f.ATR10/c
 r=c.pct_change(fill_method=None); f["RealizedVol10"]=r.rolling(10,min_periods=10).std(); f["RealizedVol20"]=r.rolling(20,min_periods=20).std()
 f["VolumeTrend5_20"]=v.rolling(5,min_periods=5).mean()/v.rolling(20,min_periods=20).mean()
 f["DollarVolume20Log"]=np.log1p(pd.to_numeric(f.DollarVolume20,errors="coerce").clip(lower=0))
 hh20=f.High.rolling(20,min_periods=20).max().shift(1); ll20=f.Low.rolling(20,min_periods=20).min().shift(1)
 hh60=f.High.rolling(60,min_periods=60).max().shift(1); ll60=f.Low.rolling(60,min_periods=60).min().shift(1)
 f["DistFrom20High"]=c/hh20-1; f["DistFrom60High"]=c/hh60-1; f["DistFrom252High"]=c/c.rolling(252,min_periods=252).max()-1
 f["DistFrom20Low"]=c/ll20-1; f["DistFrom60Low"]=c/ll60-1
 f["CloseLoc20"]=(c-ll20)/(hh20-ll20).replace(0,np.nan); f["CloseLoc60"]=(c-ll60)/(hh60-ll60).replace(0,np.nan)
 f["GapPct"]=pd.to_numeric(f.Open,errors="coerce")/c.shift(1)-1; f["IntradayReturn"]=c/pd.to_numeric(f.Open,errors="coerce")-1
 f["TrueRangePct"]=f.TR/c; f["Ticker"]=t; f["SignalDate"]=pd.to_datetime(f.index)
 for h in H:
  f[f"ForwardRet{h}"]=c.shift(-h)/c-1; f[f"ForwardExcess{h}"]=f[f"ForwardRet{h}"]-sr[h]
 return f.replace([np.inf,-np.inf],np.nan)

def panel(price_map,spy):
 a=[]
 for i,(t,px) in enumerate(price_map.items(),1):
  try:a.append(build(px,spy,t))
  except Exception as e:print("SKIP",t,e,flush=True)
  if i%100==0:print("FEATURE",i,flush=True)
 if not a:raise RuntimeError("No feature data")
 p=pd.concat(a,ignore_index=True); p=p[p.Close.ge(MIN_PRICE)&p.DollarVolume20.ge(MIN_DV)].copy()
 q=p.dropna(subset=["ForwardExcess5"]).groupby("SignalDate")["ForwardExcess5"].rank(pct=True,method="average")
 p["LongLabel"]=np.nan;p["ShortLabel"]=np.nan;p.loc[q.index,"LongLabel"]=(q>=.8).astype(int);p.loc[q.index,"ShortLabel"]=(q<=.2).astype(int)
 return p.sort_values(["SignalDate","Ticker"]).reset_index(drop=True)

def fit(x,y):
 m=Pipeline([("scale",StandardScaler()),("clf",LogisticRegression(C=.25,class_weight="balanced",solver="lbfgs",max_iter=1500,random_state=42))]);m.fit(x[FEATURES],x[y].astype(int));return m

def ranks(d,col,direction,part):
 z=d.copy();z["Rank"]=z.groupby("SignalDate",observed=True)[col].rank(ascending=False,method="first");out=[]
 for k in K:
  s=z[z.Rank<=k]
  for h in H:
   fr=s[f"ForwardRet{h}"];ex=s[f"ForwardExcess{h}"];tr=fr if direction=="Long" else -fr;te=ex if direction=="Long" else -ex
   hit=fr.gt(0) if direction=="Long" else fr.lt(0)
   out.append(dict(Partition=part,Direction=direction,Ranking=col,TopK=k,Horizon=h,N=len(s),MeanTradeReturn=float(tr.mean()),MeanTradeExcess=float(te.mean()),HitRate=float(hit.mean()),TopQuintileRate=float(s["LongLabel" if direction=="Long" else "ShortLabel"].mean())))
 return pd.DataFrame(out)

def main():
 data=ROOT/"data";out=ROOT/"artifacts"/"eod_ranking_v12";out.mkdir(parents=True,exist_ok=True)
 u=fetch_us_universe(data/"universe.csv",refresh=True); sy=fresh(u.YahooSymbol.dropna().astype(str).tolist(),int(os.getenv("SCANNER_MAX_SYMBOLS","1000")))
 period=os.getenv("SCANNER_PERIOD","10y");print("Selected",len(sy),flush=True)
 px=download_daily(sy,period=period,batch_size=int(os.getenv("SCANNER_BATCH_SIZE","50")),sleep_seconds=.75);print("Downloaded",len(px),flush=True)
 spy=download_daily(["SPY"],period=period,batch_size=1,sleep_seconds=.2).get("SPY")
 if spy is None or spy.empty:raise RuntimeError("SPY download failed")
 p=panel(px,spy);print("Panel",len(p),p.Ticker.nunique(),p.SignalDate.nunique(),flush=True)
 dates=pd.Series(p.SignalDate.drop_duplicates().sort_values().tolist());te=pd.Timestamp(dates.iloc[int(len(dates)*.6)]);he=pd.Timestamp(dates.iloc[int(len(dates)*.8)])
 allc=[];allr=[];meta={}
 for direction in ("Long","Short"):
  lab="LongLabel" if direction=="Long" else "ShortLabel";req=FEATURES+[lab]+[f"ForwardRet{h}" for h in H]+[f"ForwardExcess{h}" for h in H]
  d=p.dropna(subset=req);tr=d[d.SignalDate<te];va=d[(d.SignalDate>=te)&(d.SignalDate<he)].copy();ho=d[d.SignalDate>=he].copy()
  m=fit(tr,lab)
  for x in (va,ho):
   x["ModelProbability"]=m.predict_proba(x[FEATURES])[:,1];x["Momentum20Score"]=x.Excess20 if direction=="Long" else -x.Excess20
  for part,x in (("Validation",va),("Holdout",ho)):
   allc += [dict(Direction=direction,Ranking="ModelProbability",Partition=part,N=len(x),BaseRate=float(x[lab].mean()),AUC=float(roc_auc_score(x[lab],x.ModelProbability)),Brier=float(brier_score_loss(x[lab],x.ModelProbability))),
            dict(Direction=direction,Ranking="Momentum20Score",Partition=part,N=len(x),BaseRate=float(x[lab].mean()),AUC=np.nan,Brier=np.nan)]
   allr += [ranks(x,q,direction,part) for q in ("ModelProbability","Momentum20Score")]
  vm=allr[-2][(allr[-2].TopK==40)&(allr[-2].Horizon==5)].iloc[0];vb=allr[-1][(allr[-1].TopK==40)&(allr[-1].Horizon==5)].iloc[0]
  sel="ModelProbability" if (vm.MeanTradeExcess>vb.MeanTradeExcess or (np.isclose(vm.MeanTradeExcess,vb.MeanTradeExcess) and vm.HitRate>vb.HitRate)) else "Momentum20Score"
  meta[direction]=dict(train_rows=len(tr),validation_rows=len(va),holdout_rows=len(ho),selected=sel,validation_model_excess5d=float(vm.MeanTradeExcess),validation_momentum_excess5d=float(vb.MeanTradeExcess))
  ho2=pd.concat([r for r in allr if len(r) and r.iloc[0].Partition=="Holdout"],ignore_index=True);hm=ho2[(ho2.Ranking==sel)&(ho2.Direction==direction)&(ho2.TopK==40)&(ho2.Horizon==5)].iloc[0]
  meta[direction].update(holdout_top40_trade_return5d=float(hm.MeanTradeReturn),holdout_top40_trade_excess5d=float(hm.MeanTradeExcess),holdout_top40_hit5d=float(hm.HitRate))
  va.to_parquet(out/f"{direction.lower()}_validation_ranked_rows.parquet",index=False);ho.to_parquet(out/f"{direction.lower()}_holdout_ranked_rows.parquet",index=False)
 c=pd.DataFrame(allc);r=pd.concat(allr,ignore_index=True);c.to_csv(out/"classification_metrics.csv",index=False);r.to_csv(out/"ranking_metrics.csv",index=False)
 summary=dict(research_version="V12 EOD Cross-Sectional Ranking",purpose="EOD-only US stock ranking; no intraday price action",universe_requested=len(sy),symbols_downloaded=len(px),panel_rows=len(p),unique_tickers=int(p.Ticker.nunique()),unique_dates=int(p.SignalDate.nunique()),primary_target="Top 20% Long / bottom 20% Short by 5-day forward excess return vs SPY",horizons=list(H),top_k=list(K),model="Fixed L2 logistic regression C=0.25; no hyperparameter search",baseline="20-day excess-return momentum",split="60% chronological train / 20% validation / 20% untouched holdout",production_gate="Research only; no live probability/A-tier until independent positive and persistent holdout edge plus calibration",survivorship_note="Current-universe historical survivorship and membership bias remains",directions=meta)
 (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
 print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__":main()
