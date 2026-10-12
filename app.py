from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from scanner.config import load_config
from scanner.sample import sample_rankings


st.set_page_config(page_title="US Daily Setup Scanner", layout="wide")
config = load_config()
st.title("US Daily Setup Scanner")

with st.sidebar:
    st.header("Scanner Controls")
    mode = st.radio("Data mode", ["Sample dashboard", "Live data"])
    st.write("Daily signal → next-session open entry → 10-session research horizon")
    st.info("Research only: calibrated probabilities and A/B/C trade tiers are disabled.")

data = pd.DataFrame()
if mode == "Sample dashboard":
    longs, shorts = sample_rankings()
    regime = "Illustrative"
    universe = "Example universe"
    date = "Sample"
    source = "Illustrative sample — NOT live market data"
else:
    path = Path("data/latest_scan.parquet")
    if not path.exists():
        st.warning("Live scan data is not available yet. Open the repository's GitHub Actions tab and run 'Live Daily Scan'.")
        longs = shorts = pd.DataFrame()
        regime = "Not available"
        universe = "Not available"
        date = "—"
        source = "No live scan loaded"
    else:
        data = pd.read_parquet(path)
        if "SignalClose" not in data.columns and "Entry" in data.columns:
            data = data.rename(columns={"Entry": "SignalClose"})
        longs = data[data["Direction"] == "Long"].copy()
        shorts = data[data["Direction"] == "Short"].copy()
        regime = str(data["MarketRegime"].iloc[0]) if not data.empty else "Not available"
        universe = f"{int(data['UniverseSize'].iloc[0]):,} listed symbols" if not data.empty else "Not available"
        date = str(data["SignalDate"].iloc[0]) if not data.empty else "—"
        source = "Live EOD data — unvalidated heuristic ranking; not trading-ready"

m1, m2, m3, m4 = st.columns(4)
m1.metric("Market Regime", regime)
m2.metric("Universe", universe)
m3.metric("Long Candidates", len(longs))
m4.metric("Short Candidates", len(shorts))
st.caption(f"Signal date: **{date}** · {source}")
if mode == "Live data" and not data.empty:
    flagged = int((data.get("QualityFlags", pd.Series("No automatic QC flags", index=data.index)) != "No automatic QC flags").sum())
    unusual_volume = int(data.get("QualityStatus", pd.Series("", index=data.index)).astype(str).str.startswith("CHECK — unusual volume").sum())
    countertrend = int(data.get("MarketContext", pd.Series("", index=data.index)).eq("Countertrend vs SPY").sum())
    q1, q2, q3 = st.columns(3)
    q1.metric("QC-flagged candidates", flagged)
    q2.metric("Unusual-volume checks", unusual_volume)
    q3.metric("Countertrend candidates", countertrend)
    st.warning(
        "QC flags are review prompts, not proven rejection rules. They do not alter the heuristic score. "
        "Every candidate remains research-only."
    )


def render_table(df: pd.DataFrame, direction: str, live: bool = False) -> None:
    st.subheader(f"{direction.upper()} — TOP 40")
    if df.empty:
        st.info("No candidates available.")
        return

    ranked = df.copy()
    if "Entry" in ranked.columns and "SignalClose" not in ranked.columns:
        ranked = ranked.rename(columns={"Entry": "SignalClose"})
    ranked["SetupScore"] = pd.to_numeric(ranked.get("SetupScore"), errors="coerce")
    ranked = ranked.sort_values("SetupScore", ascending=False, na_position="last").head(40)

    if live:
        columns = [
            "Ticker", "SetupScore", "Setup", "RVOL20", "VolumeAcceleration",
            "BaseDays", "BreakoutATR", "ATR20Pct", "RiskATR", "SignalClose",
            "Stop", "Target2R", "Trend", "MarketRegime", "QualityStatus",
            "QualityFlags", "MarketContext", "Why",
        ]
    else:
        columns = [
            "Ticker", "SetupScore", "Setup", "RVOL20", "VolumeAcceleration",
            "BaseDays", "BreakoutATR", "ATR20Pct", "Trend", "Why",
        ]
        ranked["SampleStatus"] = "ILLUSTRATIVE ONLY"
        columns.append("SampleStatus")

    column_config = {
        "SetupScore": st.column_config.NumberColumn("Heuristic score", format="%.1f"),
        "RVOL20": st.column_config.NumberColumn("RVOL", format="%.1fx"),
        "VolumeAcceleration": st.column_config.NumberColumn("Volume change", format="%.2fx"),
        "BreakoutATR": st.column_config.NumberColumn("Trigger / ATR", format="%.2f"),
        "ATR20Pct": st.column_config.NumberColumn("ATR 20d %", format="%.1%"),
        "RiskATR": st.column_config.NumberColumn("Risk / ATR", format="%.2f"),
        "SignalClose": st.column_config.NumberColumn("Signal close", format="$%.2f"),
        "Stop": st.column_config.NumberColumn("Reference stop", format="$%.2f"),
        "Target2R": st.column_config.NumberColumn("Reference 2R target", format="$%.2f"),
        "QualityStatus": st.column_config.TextColumn("QC status", help="Research review label only; does not change score."),
        "QualityFlags": st.column_config.TextColumn("QC warnings", help="Flags for unusual volume, extension, stop distance or countertrend context."),
        "MarketContext": st.column_config.TextColumn("SPY context"),
    }
    st.dataframe(
        ranked[[column for column in columns if column in ranked.columns]],
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
    )

    if live:
        st.caption(
            "Scores are heuristic ranks, not probabilities. Signal close, stop and target are reference levels "
            "based on the signal-date close; the planned entry is next-session open, so recalculate levels after any gap."
        )


render_table(longs, "Long", live=(mode == "Live data"))
render_table(shorts, "Short", live=(mode == "Live data"))

st.divider()
st.subheader("Execution notes")
if mode == "Sample dashboard":
    st.info("This is illustrative sample content, not current market data or real trade recommendations.")
else:
    st.write("EOD candidate → check the next-session open → recalculate risk levels → use 15-minute confirmation and 5-minute trigger only if separately available.")
    st.warning("Intraday confirmation is not currently generated by this live EOD scan.")

st.divider()
st.subheader("Backtest / Model Health")
if mode == "Live data":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Production status", "Not approved")
    c2.metric("Probability output", "Disabled")
    c3.metric("A/B/C tiers", "Disabled")
    c4.metric("Ranking", "Heuristic only")
    st.info(
        "Prior strategy tests did not establish robust long-side edge. V14 showed positive historical alpha for its "
        "short-ranked portfolio in one holdout, but the model's improvement over a momentum baseline was not conclusive. "
        "Results are gross of transaction costs, slippage and short-borrow fees, and the universe has survivorship bias."
    )
else:
    st.info(
        "Historical model probabilities and A/B/C trade tiers are not shown. The example rankings above are placeholders "
        "for demonstrating the dashboard layout only."
    )

st.caption(
    "Research system only. A successful data download does not imply a validated trading edge or a recommendation to trade."
)
