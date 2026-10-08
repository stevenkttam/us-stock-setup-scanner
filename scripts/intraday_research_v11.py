from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from scanner.backtest import label_trade
from scanner.config import ROOT, load_config
from scanner.data import _normalize_yf_frame, download_daily
from scanner.historical import build_historical_signals
from scanner.universe import fetch_us_universe

RESEARCH_DAYS = int(os.getenv("INTRADAY_RESEARCH_DAYS", "60"))
STOCK_SAMPLE = int(os.getenv("SCANNER_MAX_SYMBOLS", "500"))
BATCH = int(os.getenv("SCANNER_BATCH_SIZE", "25"))
DAILY_PERIOD = os.getenv("DAILY_PERIOD", "2y")
INTRADAY_PERIOD = os.getenv("INTRADAY_PERIOD", "60d")
MIN_DAILY_SIGNALS = 1
MIN_RULE_N = 100
TARGETS = [1.0, 1.5, 2.0]
MAX_ENTRY_MINUTE = int(os.getenv("MAX_ENTRY_MINUTE", "180"))
CONFIRM_BARS = int(os.getenv("CONFIRM_15M_BARS", "2"))

RULES = {
    "ORB_VWAP": "After the first 30m, break the opening-range high/low with VWAP alignment and 5m time-of-day RVOL >= 1.2x.",
    "PULLBACK_VWAP": "After VWAP-aligned 15m confirmation, 5m breaks the prior 3-bar high/low with RVOL >= 1.2x.",
    "REVERSAL_VWAP": "After an opening 30m false break of the opening range, 5m reclaims/loses the opening-range midpoint with VWAP alignment.",
}


def f(x) -> float:
    try:
        return float(x)
    except Exception:
        return np.nan


def sample_symbols(all_symbols: list[str], n: int) -> list[str]:
    all_symbols = list(dict.fromkeys(all_symbols))
    if len(all_symbols) <= n:
        return all_symbols
    last = len(all_symbols) - 1
    idxs = [round(j * last / (n - 1)) for j in range(n)]
    return [all_symbols[i] for i in idxs]


def v8_sample(symbols: list[str], n: int = 500) -> list[str]:
    return sample_symbols(symbols, n)


def v9_sample(symbols: list[str], n: int = 1000) -> list[str]:
    base = set(v8_sample(symbols, 500))
    remaining = [s for s in symbols if s not in base]
    return sample_symbols(remaining, n)


def v10_sample(symbols: list[str], n: int = 1000) -> list[str]:
    v8 = set(v8_sample(symbols, 500))
    v9 = set(v9_sample(symbols, 1000))
    remaining = [s for s in symbols if s not in v8 and s not in v9]
    return sample_symbols(remaining, n)


def v11_sample(symbols: list[str], n: int) -> list[str]:
    v8 = set(v8_sample(symbols, 500))
    v9 = set(v9_sample(symbols, 1000))
    v10 = set(v10_sample(symbols, 1000))
    remaining = [s for s in symbols if s not in v8 and s not in v9 and s not in v10]
    return sample_symbols(remaining, n)


def normalize_intraday(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        # Single-ticker downloads may still return one ticker level.
        for level in range(raw.columns.nlevels):
            vals = set(map(str, raw.columns.get_level_values(level)))
            if vals.intersection({"Open", "High", "Low", "Close", "Volume"}):
                try:
                    raw = raw.copy()
                    raw.columns = [str(c[-1]) if str(c[-1]) in {"Open", "High", "Low", "Close", "Volume"} else str(c[0]) for c in raw.columns]
                except Exception:
                    pass
                break
    cols = {str(c): str(c) for c in raw.columns}
    need = {"Open", "High", "Low", "Close", "Volume"}
    if not need.issubset(cols):
        return pd.DataFrame()
    d = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    idx = pd.to_datetime(d.index, errors="coerce")
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    idx = idx.tz_convert("America/New_York")
    d.index = idx
    d = d.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_index()
    return d


def download_intraday_5m(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for start in range(0, len(symbols), BATCH):
        chunk = symbols[start:start + BATCH]
        try:
            raw = yf.download(
                tickers=chunk,
                period=INTRADAY_PERIOD,
                interval="5m",
                auto_adjust=False,
                group_by="ticker",
                threads=True,
                progress=False,
                prepost=False,
                multi_level_index=True,
            )
        except Exception as exc:
            print(f"INTRADAY DOWNLOAD ERROR {start}:{start + len(chunk)}: {exc}", flush=True)
            continue
        if raw is None or raw.empty:
            print(f"INTRADAY EMPTY {start}:{start + len(chunk)}", flush=True)
            continue
        for ticker in chunk:
            try:
                d = _normalize_yf_frame(raw, ticker)
                d = normalize_intraday(d)
                if not d.empty:
                    out[ticker] = d
            except Exception as exc:
                print(f"INTRADAY SKIP {ticker}: {exc}", flush=True)
        print(f"INTRADAY BATCH {start}:{start + len(chunk)} -> {len(out)} symbols", flush=True)
    return out


def prepare_5m(d: pd.DataFrame) -> pd.DataFrame:
    x = d.copy()
    x["SessionDate"] = x.index.date
    x["MinuteOfDay"] = ((x.index.hour * 60 + x.index.minute) - (9 * 60 + 30)).astype(int)
    x = x[(x["MinuteOfDay"] >= 0) & (x["MinuteOfDay"] < 390)].copy()
    x["TPV"] = ((x["High"] + x["Low"] + x["Close"]) / 3.0) * x["Volume"]
    x["CumTPV"] = x.groupby("SessionDate")["TPV"].cumsum()
    x["CumVol"] = x.groupby("SessionDate")["Volume"].cumsum()
    x["VWAP"] = x["CumTPV"] / x["CumVol"].replace(0, np.nan)
    x["TR"] = pd.concat(
        [
            x["High"] - x["Low"],
            (x["High"] - x["Close"].shift(1)).abs(),
            (x["Low"] - x["Close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["ATR14_5m"] = x.groupby("SessionDate")["TR"].transform(lambda s: s.rolling(14, min_periods=5).mean())
    # Time-of-day RVOL: today's bar volume divided by the historical mean
    # for the same 5-minute slot, using only prior sessions.
    x = x.sort_index()
    x["TODBaseVol"] = (
        x.groupby("MinuteOfDay", group_keys=False)["Volume"]
        .apply(lambda s: s.shift(1).rolling(20, min_periods=5).mean())
        .reset_index(level=0, drop=True)
    )
    x["TODRVOL"] = x["Volume"] / x["TODBaseVol"].replace(0, np.nan)
    return x


def resample_15m(x5: pd.DataFrame) -> pd.DataFrame:
    agg = {
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
        "Volume": "sum", "VWAP": "last",
    }
    x = x5[["Open", "High", "Low", "Close", "Volume", "VWAP"]].resample("15min", origin="start_day", offset="30min").agg(agg)
    x = x.dropna(subset=["Open", "High", "Low", "Close"])
    return x


def session_bars(x5: pd.DataFrame, date) -> pd.DataFrame:
    return x5[x5["SessionDate"] == date].copy()


def trigger_trade(x5: pd.DataFrame, signal_date: pd.Timestamp, direction: str, rule_name: str) -> dict | None:
    trade_day = (signal_date + pd.Timedelta(days=1)).date()
    # Signal date can be Friday/holiday; find the first available later session.
    sessions = sorted(pd.to_datetime(x5["SessionDate"].drop_duplicates()).date)
    future_sessions = [d for d in sessions if d > signal_date.date()]
    if not future_sessions:
        return None
    trade_day = future_sessions[0]
    day = session_bars(x5, trade_day)
    if len(day) < 12:
        return None

    bars15 = resample_15m(day)
    if len(bars15) < CONFIRM_BARS:
        return None

    first30 = day.head(6)
    or_high = f(first30["High"].max())
    or_low = f(first30["Low"].min())
    or_mid = (or_high + or_low) / 2.0
    if not (np.isfinite(or_high) and np.isfinite(or_low) and or_high > or_low):
        return None

    confirmation_time = bars15.index[CONFIRM_BARS - 1]
    confirm = bars15.loc[bars15.index <= confirmation_time].iloc[-1]
    confirmed = False
    if direction == "Long":
        confirmed = bool(confirm["Close"] > confirm["VWAP"] and confirm["Close"] > confirm["Open"])
    else:
        confirmed = bool(confirm["Close"] < confirm["VWAP"] and confirm["Close"] < confirm["Open"])
    if not confirmed:
        return None

    candidates = day[day.index > confirmation_time].copy()
    if candidates.empty:
        return None
    candidates["Prev3High"] = candidates["High"].shift(1).rolling(3, min_periods=3).max()
    candidates["Prev3Low"] = candidates["Low"].shift(1).rolling(3, min_periods=3).min()
    candidates["ATR5"] = candidates["ATR14_5m"]
    entry_cutoff = (candidates.index - pd.Timestamp(f"{trade_day} 09:30", tz="America/New_York")).total_seconds() / 60.0
    candidates = candidates[entry_cutoff <= MAX_ENTRY_MINUTE].copy()
    if candidates.empty:
        return None

    trigger = None
    for ts, bar in candidates.iterrows():
        if not np.isfinite(bar["TODRVOL"]) or bar["TODRVOL"] < 1.2:
            continue
        if direction == "Long":
            if rule_name == "ORB_VWAP":
                cond = bar["Close"] > or_high and bar["Close"] > bar["VWAP"]
            elif rule_name == "PULLBACK_VWAP":
                cond = np.isfinite(bar["Prev3High"]) and bar["Close"] > bar["Prev3High"] and bar["Close"] > bar["VWAP"]
            else:
                prior = day.loc[day.index < ts]
                if len(prior) < 6:
                    continue
                recent = prior.tail(6)
                false_break = recent["Low"].min() < or_low and recent["Close"].iloc[-1] > or_mid
                cond = bool(false_break and bar["Close"] > or_mid and bar["Close"] > bar["VWAP"])
        else:
            if rule_name == "ORB_VWAP":
                cond = bar["Close"] < or_low and bar["Close"] < bar["VWAP"]
            elif rule_name == "PULLBACK_VWAP":
                cond = np.isfinite(bar["Prev3Low"]) and bar["Close"] < bar["Prev3Low"] and bar["Close"] < bar["VWAP"]
            else:
                prior = day.loc[day.index < ts]
                if len(prior) < 6:
                    continue
                recent = prior.tail(6)
                false_break = recent["High"].max() > or_high and recent["Close"].iloc[-1] < or_mid
                cond = bool(false_break and bar["Close"] < or_mid and bar["Close"] < bar["VWAP"])
        if cond:
            trigger = (ts, bar)
            break
    if trigger is None:
        return None

    ts, bar = trigger
    entry = f(bar["Close"])
    atr5 = f(bar["ATR5"])
    if not (np.isfinite(entry) and np.isfinite(atr5) and atr5 > 0):
        return None

    prior = day.loc[day.index < ts]
    if len(prior) < 3:
        return None
    if direction == "Long":
        swing = f(prior.tail(3)["Low"].min())
        stop = swing - 0.10 * atr5
        if stop >= entry:
            return None
        risk = entry - stop
    else:
        swing = f(prior.tail(3)["High"].max())
        stop = swing + 0.10 * atr5
        if stop <= entry:
            return None
        risk = stop - entry
    if risk <= 0 or risk > 2.0 * atr5:
        return None

    return {
        "TradeDate": pd.Timestamp(trade_day),
        "TriggerTimestamp": ts,
        "Entry": entry,
        "Stop": stop,
        "Risk": risk,
        "ORHigh": or_high,
        "ORLow": or_low,
        "ConfirmationTimestamp": confirmation_time,
        "TriggerRVOL": f(bar["TODRVOL"]),
    }


def evaluate_same_day(x5: pd.DataFrame, trigger: dict, direction: str, target_r: float) -> tuple[int, str]:
    day = session_bars(x5, trigger["TradeDate"].date())
    future = day[day.index >= trigger["TriggerTimestamp"]]
    entry = trigger["Entry"]
    risk = trigger["Risk"]
    stop = entry - risk if direction == "Long" else entry + risk
    target = entry + target_r * risk if direction == "Long" else entry - target_r * risk
    for _, bar in future.iterrows():
        high, low = f(bar["High"]), f(bar["Low"])
        if direction == "Long":
            stop_hit, target_hit = low <= stop, high >= target
        else:
            stop_hit, target_hit = high >= stop, low <= target
        if stop_hit and target_hit:
            return 0, "same_bar_ambiguous"
        if target_hit:
            return 1, "target"
        if stop_hit:
            return 0, "stop"
    return 0, "eod_timeout"


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    out_dir = ROOT / "artifacts" / "intraday_v11"
    out_dir.mkdir(parents=True, exist_ok=True)

    universe = fetch_us_universe(data_dir / "universe.csv")
    symbols_all = universe["YahooSymbol"].dropna().astype(str).tolist()
    symbols = v11_sample(symbols_all, STOCK_SAMPLE)
    print(f"Selected fresh V11 sample: {len(symbols)}", flush=True)

    daily = download_daily(
        symbols, period=DAILY_PERIOD, batch_size=50, sleep_seconds=0.5
    )
    spy = download_daily(["SPY"], period=DAILY_PERIOD, batch_size=1, sleep_seconds=0.1).get("SPY")
    if spy is None or spy.empty:
        raise RuntimeError("SPY data unavailable")

    signal_parts = []
    for ticker, px in daily.items():
        try:
            s = build_historical_signals({ticker: px}, spy, cfg)
            if not s.empty:
                signal_parts.append(s)
        except Exception as exc:
            print(f"DAILY SIGNAL SKIP {ticker}: {exc}", flush=True)
    signals = pd.concat(signal_parts, ignore_index=True) if signal_parts else pd.DataFrame()
    if signals.empty:
        raise RuntimeError("No daily signals")
    signals["SignalDate"] = pd.to_datetime(signals["SignalDate"], errors="coerce")
    cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=RESEARCH_DAYS)
    signals = signals[signals["SignalDate"] >= cutoff].copy()
    print(f"Recent daily candidate rows: {len(signals)}", flush=True)

    candidate_symbols = sorted(signals["Ticker"].astype(str).unique().tolist())
    intraday = download_intraday_5m(candidate_symbols)
    print(f"Intraday symbols downloaded: {len(intraday)}", flush=True)

    rows = []
    for _, s in signals.iterrows():
        ticker = str(s["Ticker"])
        x5 = intraday.get(ticker)
        if x5 is None or x5.empty:
            continue
        try:
            prepared = prepare_5m(x5)
            for rule in RULES:
                tr = trigger_trade(prepared, pd.Timestamp(s["SignalDate"]), str(s["Direction"]), rule)
                if tr is None:
                    continue
                for target_r in TARGETS:
                    outcome, reason = evaluate_same_day(
                        prepared, tr, str(s["Direction"]), target_r
                    )
                    rows.append({
                        **s.to_dict(),
                        "Rule": rule,
                        "RuleDescription": RULES[rule],
                        "TargetR": target_r,
                        "Outcome": outcome,
                        "OutcomeReason": reason,
                        **tr,
                    })
        except Exception as exc:
            print(f"TRIGGER SKIP {ticker} {s.get('SignalDate')}: {exc}", flush=True)

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("No intraday trigger/outcome rows")

    d["SignalDate"] = pd.to_datetime(d["SignalDate"])
    unique_dates = pd.Series(d["SignalDate"].drop_duplicates().sort_values().tolist())
    train_end = unique_dates.iloc[int(len(unique_dates) * 0.50)]
    valid_end = unique_dates.iloc[int(len(unique_dates) * 0.75)]
    d["Partition"] = np.where(
        d["SignalDate"] < train_end, "Train",
        np.where(d["SignalDate"] < valid_end, "Validation", "Holdout"),
    )

    def summarize(x: pd.DataFrame) -> pd.DataFrame:
        out = (
            x.groupby(["Direction", "Rule", "TargetR"], observed=True)
            .agg(N=("Outcome", "size"), SuccessRate=("Outcome", "mean"))
            .reset_index()
        )
        out["ExpectancyR"] = out["SuccessRate"] * (1.0 + out["TargetR"]) - 1.0
        out["BreakevenRate"] = 1.0 / (1.0 + out["TargetR"])
        return out.sort_values(["Direction", "ExpectancyR"], ascending=[True, False])

    validation = summarize(d[d["Partition"] == "Validation"])
    holdout = summarize(d[d["Partition"] == "Holdout"])
    selected = validation[validation["N"] >= MIN_RULE_N].copy()
    selected = selected.sort_values(["Direction", "ExpectancyR"], ascending=[True, False]).groupby("Direction", observed=True).head(6)
    selected_keys = selected[["Direction", "Rule", "TargetR"]].copy()
    confirmation = holdout.merge(
        selected_keys, on=["Direction", "Rule", "TargetR"], how="inner"
    )
    confirmation["Pass"] = (
        (confirmation["N"] >= MIN_RULE_N)
        & (confirmation["ExpectancyR"] > 0)
        & (confirmation["SuccessRate"] > confirmation["BreakevenRate"])
    )

    gap = (
        d.groupby(["Direction", "Rule", "TargetR"], observed=True)
        .agg(
            N=("Outcome", "size"),
            SuccessRate=("Outcome", "mean"),
            MedianAdverseGapR=("NextOpenAdverseGapR", "median"),
        )
        .reset_index()
        if "NextOpenAdverseGapR" in d.columns else pd.DataFrame()
    )

    summary = {
        "research_version": "V3 Intraday Confirmation / V11",
        "symbols_requested": len(symbols),
        "daily_symbols_downloaded": len(daily),
        "daily_candidate_rows": int(len(signals)),
        "candidate_symbols": len(candidate_symbols),
        "intraday_symbols_downloaded": len(intraday),
        "intraday_trade_rows": int(len(d)),
        "research_days": RESEARCH_DAYS,
        "target_grid": TARGETS,
        "rules": RULES,
        "entry_process": "Daily candidate after close -> next available session -> 15m confirmation after first 30m -> 5m trigger -> same-day target/stop evaluation",
        "selection": "Fixed trigger rules; validation used to rank, holdout untouched except for testing selected rules",
        "minimum_rule_N": MIN_RULE_N,
        "note": "Intraday 5m research uses the free Yahoo Finance history window and is recent-history only. Current-universe survivorship bias remains. Same-bar target/stop ambiguity is a failure. The V3 model does not produce live probabilities.",
        "split": {
            "train_end": str(train_end.date()),
            "validation_end": str(valid_end.date()),
            "holdout_start": str(valid_end.date()),
        },
    }
    pd.DataFrame([summary]).to_json(out_dir / "summary.json", orient="records", indent=2)
    d.to_parquet(out_dir / "intraday_rows.parquet", index=False)
    validation.to_csv(out_dir / "validation_summary.csv", index=False)
    holdout.to_csv(out_dir / "holdout_summary.csv", index=False)
    confirmation.to_csv(out_dir / "holdout_selected_confirmation.csv", index=False)

    print(json.dumps(summary, indent=2))
    print("\nVALIDATION")
    print(validation.to_string(index=False))
    print("\nHOLDOUT SELECTED CONFIRMATION")
    print(confirmation.to_string(index=False))


if __name__ == "__main__":
    main()
