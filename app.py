from __future__ import annotations

from pathlib import Path
import json
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
st.subheader("Prospective Shadow Test — V16")

if mode != "Live data":
    st.info("Switch to Live data to view the prospective shadow-test record. This test tracks future outcomes without changing the scanner's ranking.")
else:
    shadow_status_path = Path("artifacts/shadow_test_status.json")
    shadow_ledger_path = Path("data/shadow/shadow_ledger.csv")
    shadow_summary_path = Path("data/shadow/shadow_group_summary.csv")
    shadow_comparison_path = Path("data/shadow/shadow_qc_comparisons.csv")

    shadow_status = {}
    if shadow_status_path.exists():
        try:
            shadow_status = json.loads(shadow_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            st.warning(f"Shadow-test status file could not be read: {exc}")

    shadow_ledger_snapshot = pd.DataFrame()
    if shadow_ledger_path.exists():
        try:
            shadow_ledger_snapshot = pd.read_csv(shadow_ledger_path)
        except Exception as exc:
            st.warning(f"Shadow signal ledger could not be read for status reconciliation: {exc}")

    if not shadow_status:
        st.info(
            "The prospective shadow tracker has not published its first status file yet. "
            "It is designed to start recording only after a successful scheduled after-close scan "
            "whose signal date matches the actual market session."
        )
    else:
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Signal events", f"{int(shadow_status.get('ledger_events', 0)):,}")
        h2.metric("Signal dates", f"{int(shadow_status.get('unique_signal_dates', 0)):,}")
        h3.metric("Valid signal bars", f"{int(shadow_status.get('valid_signal_bars', 0)):,}")
        h4.metric("Invalid signal bars", f"{int(shadow_status.get('invalid_signal_bars', 0)):,}")

        o1, o2, o3, o4 = st.columns(4)
        for metric, horizon in [(o1, 1), (o2, 3), (o3, 5), (o4, 10)]:
            outcome_column = f"OutcomeStatus_{horizon}d"
            outcome_values = (
                shadow_ledger_snapshot[outcome_column].fillna("pending").astype(str)
                if not shadow_ledger_snapshot.empty and outcome_column in shadow_ledger_snapshot.columns
                else pd.Series(dtype=str)
            )
            complete = int(shadow_status.get(f"completed_{horizon}d_outcomes", outcome_values.eq("complete").sum()))
            pending = int(shadow_status.get(
                f"pending_{horizon}d_outcomes",
                outcome_values.isin(["", "nan", "None", "pending"]).sum(),
            ))
            invalid = int(shadow_status.get(
                f"invalid_{horizon}d_outcomes",
                (~outcome_values.isin(["", "nan", "None", "pending", "complete"])).sum(),
            ))
            metric.metric(
                f"{horizon}-session outcomes",
                f"{complete:,} complete",
                delta=f"{pending:,} pending · {invalid:,} invalid",
                delta_color="off",
            )

        qc_counts = shadow_status.get("qc_event_counts", {})
        if not qc_counts and not shadow_ledger_snapshot.empty:
            for col in ("QCFlagged", "QCUnusualVolume", "QCCountertrend"):
                if col in shadow_ledger_snapshot.columns:
                    shadow_ledger_snapshot[col] = shadow_ledger_snapshot[col].map(
                        lambda value: str(value).lower() in ("true", "1", "yes") if pd.notna(value) else False
                    )
            qc_counts = {
                "clear": int((~shadow_ledger_snapshot["QCFlagged"]).sum()) if "QCFlagged" in shadow_ledger_snapshot.columns else 0,
                "flagged": int(shadow_ledger_snapshot["QCFlagged"].sum()) if "QCFlagged" in shadow_ledger_snapshot.columns else 0,
                "unusual_volume": int(shadow_ledger_snapshot["QCUnusualVolume"].sum()) if "QCUnusualVolume" in shadow_ledger_snapshot.columns else 0,
                "countertrend": int(shadow_ledger_snapshot["QCCountertrend"].sum()) if "QCCountertrend" in shadow_ledger_snapshot.columns else 0,
            }
        st.caption(
            f"QC composition across the recorded ledger: "
            f"{int(qc_counts.get('clear', 0)):,} clear · "
            f"{int(qc_counts.get('flagged', 0)):,} flagged · "
            f"{int(qc_counts.get('unusual_volume', 0)):,} unusual volume · "
            f"{int(qc_counts.get('countertrend', 0)):,} countertrend."
        )

        st.caption(
            f"Latest market date tracked: **{shadow_status.get('latest_market_date', '—')}** · "
            f"Research version: **{shadow_status.get('research_version', 'V16')}** · "
            f"New signals added this run: **{int(shadow_status.get('new_signals_added_this_run', 0))}**"
        )
        st.warning(
            "Prospective paper/shadow results only. Net returns use fixed cost assumptions, not actual broker fills. "
            "Small samples and confidence intervals that include zero do not establish a trading edge."
        )

    if shadow_summary_path.exists():
        try:
            shadow_summary = pd.read_csv(shadow_summary_path)
        except pd.errors.EmptyDataError:
            shadow_summary = pd.DataFrame()
        except Exception as exc:
            shadow_summary = pd.DataFrame()
            st.warning(f"Could not read shadow group summary: {exc}")

        if not shadow_summary.empty:
            st.markdown("**Top 10 prospective portfolio results — base cost assumption**")
            st.caption(
                "The tracker freezes the selected candidate cohort before checking returns. Results stay pending until "
                "the selected signals mature; a terminal missing-data issue makes that portfolio-date unscorable rather "
                "than replacing the candidate. Unused portfolio slots are treated as cash at 0% return."
            )
            view = shadow_summary[
                (pd.to_numeric(shadow_summary.get("TopK"), errors="coerce") == 10)
                & (shadow_summary.get("CostScenario", pd.Series(index=shadow_summary.index, dtype=str)) == "Base")
            ].copy()
            if not view.empty:
                for col in ["MeanDailyGrossPnL", "MeanDailyExcessVsSPY", "MeanDailyNetPnL", "MeanNetPnLPerTrade", "NetCI95Low", "NetCI95High", "NetPositiveDatePct", "MeanSlotCoverage"]:
                    if col in view.columns:
                        view[col] = pd.to_numeric(view[col], errors="coerce")
                view = view.sort_values(["Horizon", "Direction", "QCGroup"])
                st.dataframe(
                    view[[
                        c for c in [
                            "Direction", "QCGroup", "Horizon", "SignalDates", "ScorableDates", "PendingDates", "UnscorableDates",
                            "PendingOutcomeSlots", "MissingOutcomeSlots", "TotalTrades", "MeanDailyNetPnL", "MeanNetPnLPerTrade", "NetCI95Low", "NetCI95High",
                            "NetPositiveDatePct", "MeanSlotCoverage", "CIStatus",
                        ] if c in view.columns
                    ]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Horizon": st.column_config.NumberColumn("Holding sessions"),
                        "SignalDates": st.column_config.NumberColumn("Signal dates", format="%d"),
                        "ScorableDates": st.column_config.NumberColumn("Scorable dates", format="%d"),
                        "PendingDates": st.column_config.NumberColumn("Pending dates", format="%d"),
                        "UnscorableDates": st.column_config.NumberColumn("Unscorable dates", format="%d"),
                        "PendingOutcomeSlots": st.column_config.NumberColumn("Pending outcome slots", format="%d"),
                        "MissingOutcomeSlots": st.column_config.NumberColumn("Missing outcome slots", format="%d"),
                        "TotalTrades": st.column_config.NumberColumn("Completed events", format="%d"),
                        "MeanDailyNetPnL": st.column_config.NumberColumn("Portfolio net return / date", format="percent", help="Return is normalized to the target Top 10 slots; unused slots are treated as cash at 0% return."),
                        "MeanNetPnLPerTrade": st.column_config.NumberColumn("Mean net per filled trade", format="percent"),
                        "NetCI95Low": st.column_config.NumberColumn("95% CI low", format="percent"),
                        "NetCI95High": st.column_config.NumberColumn("95% CI high", format="percent"),
                        "NetPositiveDatePct": st.column_config.NumberColumn("Positive dates", format="percent"),
                        "MeanSlotCoverage": st.column_config.NumberColumn("Slot coverage", format="percent"),
                    },
                )
            else:
                st.info("No completed portfolio summaries are available yet.")
    else:
        st.caption("Portfolio summaries will appear once the tracker has completed its first run.")

    if shadow_comparison_path.exists():
        try:
            shadow_comparisons = pd.read_csv(shadow_comparison_path)
        except pd.errors.EmptyDataError:
            shadow_comparisons = pd.DataFrame()
        except Exception as exc:
            shadow_comparisons = pd.DataFrame()
            st.warning(f"Could not read shadow QC comparisons: {exc}")

        if not shadow_comparisons.empty:
            st.markdown("**Does a QC filter improve the Top 10 result? — paired comparison versus All**")
            comparison_horizon = st.selectbox(
                "Comparison holding period",
                options=[1, 3, 5, 10],
                index=2,
                format_func=lambda h: f"{h} sessions",
                key="shadow_qc_comparison_horizon",
            )
            compare = shadow_comparisons[
                (pd.to_numeric(shadow_comparisons.get("Horizon"), errors="coerce") == comparison_horizon)
                & (shadow_comparisons.get("CostScenario", pd.Series(index=shadow_comparisons.index, dtype=str)) == "Base")
            ].copy()
            if not compare.empty:
                for col in ["MeanNetPnLDifference", "DiffCI95Low", "DiffCI95High"]:
                    if col in compare.columns:
                        compare[col] = pd.to_numeric(compare[col], errors="coerce")
                st.dataframe(
                    compare[[
                        c for c in [
                            "Direction", "Comparison", "PairedSignalDates", "MeanNetPnLDifference",
                            "DiffCI95Low", "DiffCI95High", "DifferencePositiveSupported", "CIStatus",
                        ] if c in compare.columns
                    ]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "PairedSignalDates": st.column_config.NumberColumn("Paired dates", format="%d"),
                        "MeanNetPnLDifference": st.column_config.NumberColumn("Mean net difference", format="percent"),
                        "DiffCI95Low": st.column_config.NumberColumn("95% CI low", format="percent"),
                        "DiffCI95High": st.column_config.NumberColumn("95% CI high", format="percent"),
                        "DifferencePositiveSupported": st.column_config.CheckboxColumn("CI entirely above zero"),
                    },
                )
                st.caption(
                    "Returns are normalized to the target 10 slots, with unused slots treated as cash. "
                    "A positive QC-group-minus-All difference means that group did better; the 95% interval "
                    "must be entirely above zero to support a positive difference. A QC-flagged group doing better "
                    "would argue against excluding flagged names."
                )
            else:
                st.info("No paired comparison rows are available for this holding period.")
    else:
        st.caption("QC-filter comparison results will appear after the tracker processes matured outcomes.")

    if shadow_ledger_path.exists():
        try:
            shadow_ledger = pd.read_csv(shadow_ledger_path)
        except Exception as exc:
            shadow_ledger = pd.DataFrame()
            st.warning(f"Could not read shadow signal ledger: {exc}")

        if not shadow_ledger.empty:
            st.markdown("**Most recent shadow signals**")
            if "SignalDate" in shadow_ledger.columns:
                shadow_ledger["SignalDate"] = pd.to_datetime(shadow_ledger["SignalDate"], errors="coerce")
                shadow_ledger = shadow_ledger.sort_values(["SignalDate", "Direction", "SignalRank"], ascending=[False, True, True])
            ledger_columns = [
                "SignalDate", "Ticker", "Direction", "SignalRank", "SetupScore",
                "QCFlagged", "QCUnusualVolume", "QCCountertrend", "SignalBarStatus",
                "OutcomeStatus_1d", "OutcomeStatus_3d", "OutcomeStatus_5d", "OutcomeStatus_10d",
            ]
            st.dataframe(
                shadow_ledger[[c for c in ledger_columns if c in shadow_ledger.columns]].head(30),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "SignalDate": st.column_config.DateColumn("Signal date"),
                    "SetupScore": st.column_config.NumberColumn("Heuristic score", format="%.1f"),
                    "SignalRank": st.column_config.NumberColumn("Frozen rank", format="%d"),
                    "QCFlagged": st.column_config.CheckboxColumn("QC flagged"),
                    "QCUnusualVolume": st.column_config.CheckboxColumn("Unusual volume"),
                    "QCCountertrend": st.column_config.CheckboxColumn("Countertrend"),
                },
            )

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
