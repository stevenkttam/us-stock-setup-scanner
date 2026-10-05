from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from scanner.config import load_config
from scanner.sample import sample_rankings

st.set_page_config(page_title="US Daily Setup Scanner", layout="wide")
config = load_config()

st.title("US Daily Setup Scanner")
st.caption("V1 research dashboard — sample mode until the live scanner data pipeline is connected.")

with st.sidebar:
    st.header("Scanner Controls")
    mode = st.radio("Data mode", ["Sample dashboard", "Live data (when configured)"])
    view = st.radio("Ranking view", ["Pure Top 40", "Diversified Top 40"])
    min_prob = st.slider("Initial probability threshold", 0.50, 0.90, config["ranking"]["initial_trade_probability_threshold"], 0.01)
    st.write(f"A-tier threshold: **{min_prob:.0%}**")
    st.write("Daily signal → next-session entry → 10-session outcome")

if mode == "Sample dashboard":
    longs, shorts = sample_rankings()
    market_regime = "Bullish"
    universe_count = "Broad US universe"
    source_label = "Illustrative sample only — not live market data"
else:
    st.info("Live-data mode is intentionally disabled in this V1 review build. The scanner modules and daily workflow are included; connect the data pipeline before trading on live output.")
    longs = pd.DataFrame()
    shorts = pd.DataFrame()
    market_regime = "Not available"
    universe_count = "Not available"
    source_label = "No live data loaded"

m1, m2, m3, m4 = st.columns(4)
m1.metric("Market Regime", market_regime)
m2.metric("Universe", universe_count)
m3.metric("Long Candidates", len(longs))
m4.metric("Short Candidates", len(shorts))
st.caption(source_label)


def render_table(df: pd.DataFrame, direction: str):
    st.subheader(f"{direction.upper()} — TOP 40")
    if df.empty:
        st.warning("No data loaded.")
        return
    d = df.copy()
    d["Status"] = d["Probability"].apply(lambda p: "A — Trade" if p >= min_prob * 100 else ("B — Watch" if p >= 60 else "C — Research"))
    d = d.sort_values(["Probability", "ExpectedR"], ascending=False).head(40)
    st.dataframe(
        d[["Ticker", "Probability", "SetupScore", "Setup", "RVOL20", "BaseDays", "BreakoutATR", "Trend", "ExpectedR", "Status", "Why"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Probability": st.column_config.NumberColumn("Probability", format="%.0f%%"),
            "SetupScore": st.column_config.NumberColumn("Score", format="%.0f"),
            "RVOL20": st.column_config.NumberColumn("RVOL", format="%.1fx"),
            "BreakoutATR": st.column_config.NumberColumn("Breakout / ATR", format="%.1f"),
            "ExpectedR": st.column_config.NumberColumn("Expected R", format="%.2f"),
        },
    )

render_table(longs, "Long")
render_table(shorts, "Short")

st.divider()
st.subheader("Intraday Confirmation")
st.write("Daily candidate → 15-minute confirmation → 5-minute trigger")
st.dataframe(pd.DataFrame({
    "Ticker": ["NVDA", "AMD", "TSLA"],
    "Direction": ["Long", "Long", "Short"],
    "Daily Probability": [78, 76, 75],
    "15m Confirmation": ["Confirmed", "Confirmed", "Waiting"],
    "5m Trigger": ["Waiting", "Triggered", "Waiting"],
    "Status": ["WATCH", "TRADE-READY", "WATCH"],
}), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Backtest / Model Health")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Primary Success", "+2R before -1R")
c2.metric("Holding Window", "10 sessions")
c3.metric("Initial A-tier", f"≥ {min_prob:.0%}")
c4.metric("Validation", "Walk-forward + OOS")
st.info("The live version will only show model probabilities after the historical training set has been built and validated. Sample probabilities above are illustrative placeholders and must not be treated as trading signals.")

st.caption("Design posture: candidates are research/trading candidates, not guaranteed outcomes or investment recommendations.")
