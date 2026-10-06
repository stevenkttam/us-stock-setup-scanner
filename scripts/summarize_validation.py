from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from scanner.config import ROOT

def main():
    p=ROOT/"data"/"training_signals.parquet"
    if not p.exists():
        raise FileNotFoundError(p)
    d=pd.read_parquet(p)
    if d.empty:
        raise ValueError("Training signal dataset is empty")

    d["PrimaryR"]=d["Outcome"].map({1:2.0,0:-1.0})
    summary={
        "signals":int(len(d)),
        "success_rate":float(d["Outcome"].mean()),
        "primary_binary_expectancy_R":float(d["PrimaryR"].mean()),
        "long_signals":int((d["Direction"]=="Long").sum()),
        "short_signals":int((d["Direction"]=="Short").sum()),
        "unique_tickers":int(d["Ticker"].nunique()),
        "signal_start":str(pd.to_datetime(d["SignalDate"]).min().date()),
        "signal_end":str(pd.to_datetime(d["SignalDate"]).max().date()),
    }

    direction=d.groupby("Direction").agg(
        signals=("Outcome","size"),
        success_rate=("Outcome","mean"),
        avg_setup_score=("SetupScore","mean"),
        avg_rvol=("RVOL20","mean"),
        avg_breakout_atr=("BreakoutATR","mean"),
    ).reset_index()
    direction["binary_expectancy_R"]=3*direction["success_rate"]-1

    setup=d.groupby(["Direction","Setup"]).agg(
        signals=("Outcome","size"),
        success_rate=("Outcome","mean"),
        avg_setup_score=("SetupScore","mean"),
        avg_rvol=("RVOL20","mean"),
        avg_breakout_atr=("BreakoutATR","mean"),
    ).reset_index()
    setup["binary_expectancy_R"]=3*setup["success_rate"]-1

    score_band=d.assign(
        ScoreBand=pd.cut(
            d["SetupScore"],
            bins=[-float("inf"),59.999,69.999,79.999,89.999,float("inf")],
            labels=["<60","60-69","70-79","80-89","90+"],
            right=True,
        )
    ).groupby(["Direction","ScoreBand"],observed=False).agg(
        signals=("Outcome","size"),
        success_rate=("Outcome","mean"),
    ).reset_index()

    out=ROOT/"artifacts"/"validation_250_2y"
    out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    direction.to_csv(out/"by_direction.csv",index=False)
    setup.to_csv(out/"by_setup.csv",index=False)
    score_band.to_csv(out/"by_score_band.csv",index=False)

    print(json.dumps(summary,indent=2))
    print("
BY DIRECTION
",direction.to_string(index=False))
    print("
BY SETUP
",setup.to_string(index=False))
    print("
BY SCORE BAND
",score_band.to_string(index=False))

if __name__=="__main__":
    main()
