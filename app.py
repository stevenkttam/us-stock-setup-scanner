from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import streamlit as st
from scanner.config import load_config
from scanner.sample import sample_rankings

st.set_page_config(page_title="US Daily Setup Scanner",layout="wide")
config=load_config()
st.title("US Daily Setup Scanner")
with st.sidebar:
    st.header("Scanner Controls")
    mode=st.radio("Data mode",["Sample dashboard","Live data"])
    st.radio("Ranking view",["Pure Top 40","Diversified Top 40"])
    min_prob=st.slider("Probability threshold",.50,.90,config["ranking"]["initial_trade_probability_threshold"],.01)
    st.write(f"Initial A-tier: **{min_prob:.0%}**")
    st.write("Daily signal → next-session entry → 10-session outcome")

if mode=="Sample dashboard":
    longs,shorts=sample_rankings(); regime="Bullish"; universe="Broad US universe"; date="Sample"; source="Illustrative sample — NOT live market data"
else:
    path=Path("data/latest_scan.parquet")
    if not path.exists():
        st.warning("Live scan data is not available yet. Run the historical smoke test first, then the validated training workflow.")
        longs=shorts=pd.DataFrame(); regime="Not available"; universe="Not available"; date="—"; source="No validated live scan loaded"
    else:
        d=pd.read_parquet(path); longs=d[d.Direction=="Long"].copy(); shorts=d[d.Direction=="Short"].copy()
        regime=str(d["MarketRegime"].iloc[0]); universe=f"{int(d['UniverseSize'].iloc[0]):,} stocks"; date=str(d["SignalDate"].iloc[0]); source="Validated scanner output"

m1,m2,m3,m4=st.columns(4); m1.metric("Market Regime",regime); m2.metric("Universe",universe); m3.metric("Long Candidates",len(longs)); m4.metric("Short Candidates",len(shorts))
st.caption(f"Signal date: **{date}** · {source}")

def render_table(df,direction):
    st.subheader(f"{direction.upper()} — TOP 40")
    if df.empty: st.info("No candidates available."); return
    d=df.copy()
    d["ProbabilityPct"]=d["Probability"].apply(lambda p:p*100 if pd.notna(p) and p<=1 else p) if "Probability" in d else pd.NA
    d["Status"]=d["ProbabilityPct"].apply(lambda p:"A — Trade" if pd.notna(p) and p>=min_prob*100 else ("B — Watch" if pd.notna(p) and p>=60 else "C — Research"))
    d=d.sort_values(["ProbabilityPct","SetupScore"],ascending=False).head(40)
    cols=["Ticker","ProbabilityPct","SetupScore","Setup","RVOL20","BaseDays","BreakoutATR","Trend","ExpectedR","Status","Why"]
    st.dataframe(d[[c for c in cols if c in d.columns]],use_container_width=True,hide_index=True,column_config={"ProbabilityPct":st.column_config.NumberColumn("Probability",format="%.0f%%"),"SetupScore":st.column_config.NumberColumn("Score",format="%.0f"),"RVOL20":st.column_config.NumberColumn("RVOL",format="%.1fx"),"BreakoutATR":st.column_config.NumberColumn("Breakout / ATR",format="%.1f"),"ExpectedR":st.column_config.NumberColumn("Expected R",format="%.2f")})
render_table(longs,"Long"); render_table(shorts,"Short")

st.divider(); st.subheader("Intraday Confirmation"); st.write("Daily candidate → 15-minute confirmation → 5-minute trigger")
if mode=="Sample dashboard":
    st.dataframe(pd.DataFrame({"Ticker":["NVDA","AMD","TSLA"],"Direction":["Long","Long","Short"],"Daily Probability":[78,76,75],"15m Confirmation":["Confirmed","Confirmed","Waiting"],"5m Trigger":["Waiting","Triggered","Waiting"],"Status":["WATCH","TRADE-READY","WATCH"]}),use_container_width=True,hide_index=True)
else:
    st.info("15m/5m confirmation is an execution-layer build and is not used to create the daily probability.")

st.divider(); st.subheader("Backtest / Model Health")
c1,c2,c3,c4=st.columns(4); c1.metric("Primary Success","+2R before -1R"); c2.metric("Holding Window","10 sessions"); c3.metric("Initial A-tier",f"≥ {min_prob:.0%}"); c4.metric("Validation","Time-aware OOS")
meta=[]
for direction in ["long","short"]:
    p=Path(f"models/{direction}_metadata.json")
    if p.exists(): meta.append(json.loads(p.read_text()))
if meta: st.dataframe(pd.DataFrame(meta),use_container_width=True,hide_index=True)
else: st.info("No trained model metadata yet. Do not use live probabilities until historical training and out-of-sample validation are complete.")
st.caption("Research system only. Probabilities are calibrated historical estimates, not guarantees or investment advice.")
