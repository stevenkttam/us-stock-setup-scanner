from __future__ import annotations
import json
from pathlib import Path
import joblib
import pandas as pd
import yfinance as yf
from scanner.config import ROOT,load_config
from scanner.data import download_daily
from scanner.features import add_features,classify_regime
from scanner.scan import scan_symbol
from scanner.universe import fetch_us_universe
from scanner.model import FEATURES

def main():
    cfg=load_config(); data_dir=ROOT/"data"; models_dir=ROOT/"models"; artifacts=ROOT/"artifacts"; artifacts.mkdir(exist_ok=True)
    universe=fetch_us_universe(data_dir/"universe.csv",refresh=True)
    mapping=dict(zip(universe["YahooSymbol"],universe["Symbol"])) if "YahooSymbol" in universe.columns else {}
    symbols=universe["YahooSymbol"].dropna().tolist() if "YahooSymbol" in universe.columns else universe["Symbol"].dropna().tolist()
    price_map=download_daily(symbols,period="1y",batch_size=100,sleep_seconds=.5)
    spy=yf.download("SPY",period="1y",interval="1d",auto_adjust=False,progress=False)
    if isinstance(spy.columns,pd.MultiIndex): spy=spy.xs("SPY",axis=1,level=0)
    regime=str(classify_regime(spy).iloc[-1]); rows=[]
    for yahoo_ticker,prices in price_map.items():
        try:
            latest=prices.iloc[-1]; avg_dollar=(prices["Close"]*prices["Volume"]).rolling(20).mean().iloc[-1]
            if float(latest["Close"])<cfg["universe"]["min_price"] or float(avg_dollar)<cfg["universe"]["min_avg_dollar_volume_20d"]: continue
            f=add_features(prices,spy=spy); longs,shorts=scan_symbol(f,mapping.get(yahoo_ticker,yahoo_ticker),regime,cfg); rows.extend(longs+shorts)
        except Exception as exc: print(f"SKIP {yahoo_ticker}: {exc}")
    candidates=pd.DataFrame(rows)
    if candidates.empty: raise SystemExit("No candidates generated.")
    for direction in ["Long","Short"]:
        path=models_dir/f"{direction.lower()}_probability.joblib"; mask=candidates["Direction"].eq(direction)
        if not path.exists(): candidates.loc[mask,"Probability"]=pd.NA; continue
        model=joblib.load(path); x=candidates.loc[mask].replace([float("inf"),float("-inf")],float("nan")).fillna(0.0)
        candidates.loc[mask,"Probability"]=model.predict_proba(x[FEATURES])[:,1]
    candidates["ExpectedR"]=candidates["Probability"].apply(lambda p:(3*p-1) if pd.notna(p) else pd.NA)
    candidates["Priority"]=candidates["Probability"].apply(lambda p:"A — Trade" if pd.notna(p) and p>=cfg["ranking"]["initial_trade_probability_threshold"] else ("B — Watch" if pd.notna(p) and p>=cfg["ranking"]["watch_probability_floor"] else "C — Research"))
    signal_date=pd.Timestamp(spy.index[-1]).date().isoformat()
    candidates["SignalDate"]=signal_date; candidates["UniverseSize"]=len(universe); candidates["MarketRegime"]=regime
    candidates.to_parquet(data_dir/"latest_scan.parquet",index=False)
    status={"status":"success","signal_date":signal_date,"universe_size":len(universe),"eligible_downloads":len(price_map),"candidates":len(candidates),"longs":int((candidates.Direction=="Long").sum()),"shorts":int((candidates.Direction=="Short").sum()),"regime":regime}
    (artifacts/"live_scan_status.json").write_text(json.dumps(status,indent=2),encoding="utf-8"); print(json.dumps(status,indent=2))

if __name__=="__main__": main()
