from __future__ import annotations
import csv
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

def locate(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"Could not find {name} under {root}")
    return matches[0]

def block_bootstrap_mean_ci(values, reps=4000, block=5, seed=42):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return (float(np.mean(x)) if n else float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    blocks_needed = int(np.ceil(n / block))
    starts = np.arange(n)
    for i in range(reps):
        picks = rng.choice(starts, size=blocks_needed, replace=True)
        samp = np.concatenate([x[(p + np.arange(block)) % n] for p in picks])[:n]
        means[i] = samp.mean()
    return float(x.mean()), float(np.quantile(means, .025)), float(np.quantile(means, .975))

def daily_portfolio(df: pd.DataFrame, ranking: str, direction: str, top_k=40):
    rows=[]
    for date, g in df.groupby("SignalDate", sort=True):
        g=g.dropna(subset=[ranking, "ForwardRet5", "ForwardExcess5"]).sort_values(ranking, ascending=False).head(top_k)
        if g.empty:
            continue
        sign=1.0 if direction=="Long" else -1.0
        rows.append({
            "SignalDate": pd.Timestamp(date),
            "MeanTradeReturn5": float((sign*g["ForwardRet5"]).mean()),
            "MeanTradeExcess5": float((sign*g["ForwardExcess5"]).mean()),
            "HitRate5": float((g["ForwardRet5"]>0).mean() if direction=="Long" else (g["ForwardRet5"]<0).mean()),
            "N": int(len(g)),
        })
    return pd.DataFrame(rows).sort_values("SignalDate")

def pct(x):
    return "n/a" if pd.isna(x) else f"{100*x:.2f}%"

def num(x):
    return "n/a" if pd.isna(x) else f"{x:.4f}"

def main():
    root=Path(sys.argv[1] if len(sys.argv)>1 else "v12-artifact")
    sp=locate(root,"summary.json")
    summary=json.loads(sp.read_text(encoding="utf-8"))
    rankings=pd.read_csv(locate(root,"ranking_metrics.csv"))
    classes=pd.read_csv(locate(root,"classification_metrics.csv"))
    out=[]
    out += ["# V12 EOD Ranking — Artifact Analysis", ""]
    out += [f"- **Workflow experiment:** {summary.get('research_version','V12')}",
            f"- **Universe sample:** {summary.get('symbols_downloaded','?')} downloaded; {summary.get('unique_tickers','?')} tickers in eligible panel",
            f"- **Panel:** {summary.get('panel_rows','?')} eligible stock-date rows across {summary.get('unique_dates','?')} trading dates",
            f"- **Target:** {summary.get('primary_target','not stated')}",
            f"- **Validation design:** {summary.get('split','not stated')}",
            f"- **Caveat:** {summary.get('survivorship_note','')}",
            "", "## Main holdout comparison", "",
            "| Direction | Selected ranking (from validation) | Validation model excess / day | Validation momentum excess / day | Holdout selected excess / day | Holdout trade return / day | Holdout hit rate |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for direction in ("Long","Short"):
        m=summary["directions"][direction]
        out.append(f"| {direction} | {m['selected']} | {pct(m['validation_model_excess5d'])} | {pct(m['validation_momentum_excess5d'])} | {pct(m['holdout_top40_trade_excess5d'])} | {pct(m['holdout_top40_trade_return5d'])} | {pct(m['holdout_top40_hit5d'])} |")
    out += ["", "Signals are formed using data available at the close; the hypothetical entry is the next session open and the exit is the close h sessions after the signal. SPY is measured over the same open-to-close interval. Returns are gross and exclude commissions, slippage, borrow fees, taxes and market impact.", "",
            "## Holdout by horizon and Top-K", "",
            "| Direction | Ranking | Top-K | Days | Mean trade return | Mean excess vs SPY | Hit rate | N stock-date selections |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    hold=rankings[rankings["Partition"].eq("Holdout")].copy()
    for direction in ("Long","Short"):
        selected=summary["directions"][direction]["selected"]
        for _,r in hold[(hold.Direction==direction)&(hold.Ranking==selected)].sort_values(["TopK","Horizon"]).iterrows():
            out.append(f"| {direction} | {selected} | {int(r.TopK)} | {int(r.Horizon)} | {pct(r.MeanTradeReturn)} | {pct(r.MeanTradeExcess)} | {pct(r.HitRate)} | {int(r.N)} |")
    out += ["", "## Daily Top-40 stability check", "",
            "This uses the saved holdout rows to calculate one equal-weight Top-40 return per signal date, then applies a 5-session circular block bootstrap to the mean daily excess return. The interval accounts for some time dependence, but not all cross-sectional/data-source risks.", "",
            "| Direction | Ranking | Dates | Mean daily excess | 95% block-bootstrap CI | Positive lower bound? |",
            "|---|---|---:|---:|---:|---|"]
    daily_by_dir={}
    for direction in ("Long","Short"):
        m=summary["directions"][direction]
        file=locate(root,f"{direction.lower()}_holdout_ranked_rows.parquet")
        df=pd.read_parquet(file)
        df["SignalDate"]=pd.to_datetime(df["SignalDate"])
        for ranking in ("ModelProbability","Momentum20Score"):
            daily=daily_portfolio(df,ranking,direction)
            daily_by_dir[(direction,ranking)]=daily
            mean,lo,hi=block_bootstrap_mean_ci(daily["MeanTradeExcess5"].to_numpy(),seed=42 if direction=="Long" else 43)
            out.append(f"| {direction} | {ranking} | {len(daily)} | {pct(mean)} | [{pct(lo)}, {pct(hi)}] | {'Yes' if lo>0 else 'No'} |")
    out += ["", "## Model versus momentum: paired daily comparison", "",
            "| Direction | Selected ranking | Mean daily excess difference vs momentum | 95% block-bootstrap CI | Model beats momentum reliably? |",
            "|---|---|---:|---:|---|"]
    for direction in ("Long","Short"):
        chosen=summary["directions"][direction]["selected"]
        model_daily=daily_by_dir[(direction,chosen)].set_index("SignalDate")["MeanTradeExcess5"]
        baseline=daily_by_dir[(direction,"Momentum20Score")].set_index("SignalDate")["MeanTradeExcess5"]
        paired=pd.concat([model_daily.rename("m"),baseline.rename("b")],axis=1).dropna()
        mean,lo,hi=block_bootstrap_mean_ci((paired["m"]-paired["b"]).to_numpy(),seed=71 if direction=="Long" else 72)
        out.append(f"| {direction} | {chosen} | {pct(mean)} | [{pct(lo)}, {pct(hi)}] | {'Yes' if lo>0 else 'No'} |")
    out += ["", "## Classification diagnostics", "",
            "| Direction | Partition | Ranking | N | Base rate | AUC | Brier |",
            "|---|---|---|---:|---:|---:|---:|"]
    for _,r in classes.iterrows():
        out.append(f"| {r.Direction} | {r.Partition} | {r.Ranking} | {int(r.N)} | {pct(r.BaseRate)} | {num(r.AUC)} | {num(r.Brier)} |")
    out += ["", "## Decision rule", "",
            "Do **not** enable production probabilities or A-tier labels on this single experiment alone. Advance only if the selected ranking has positive holdout excess, a positive block-bootstrap lower confidence bound, and an incremental advantage over the fixed momentum baseline, followed by a separate replication across time periods or a fresh stock sample. If these conditions fail, retain the EOD dashboard as an uncalibrated ranking / research tool and run the next preregistered experiment rather than optimizing on this holdout.", ""]
    report="\n".join(out)
    dest=Path("v12-analysis-report.md")
    dest.write_text(report,encoding="utf-8")
    (Path("v12-analysis-metrics.json")).write_text(json.dumps({
        "experiment":summary,
        "holdout_top40":hold[(hold.TopK==40)&(hold.Horizon==5)].to_dict(orient="records"),
    },indent=2,default=str),encoding="utf-8")
    print(report)
if __name__=="__main__": main()
