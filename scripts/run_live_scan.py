from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from scanner.config import ROOT, load_config
from scanner.data import download_daily
from scanner.features import add_features, classify_regime
from scanner.scan import scan_symbol
from scanner.universe import fetch_us_universe
from scanner.market_cap import fetch_market_caps, market_cap_meets_threshold


def download_spy_with_retry(period: str = "1y", attempts: int = 4) -> pd.DataFrame:
    """Fetch a clean SPY benchmark before the larger stock download starts."""
    last_error = "unknown download error"
    required = {"Open", "High", "Low", "Close", "Volume"}

    for attempt in range(attempts):
        try:
            spy = yf.download(
                tickers="SPY",
                period=period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                multi_level_index=False,
            )
            if spy is None or spy.empty:
                raise RuntimeError("Yahoo Finance returned an empty SPY response.")

            if isinstance(spy.columns, pd.MultiIndex):
                found = False
                for level in range(spy.columns.nlevels):
                    if "SPY" in set(map(str, spy.columns.get_level_values(level))):
                        spy = spy.xs("SPY", axis=1, level=level)
                        found = True
                        break
                if not found:
                    raise RuntimeError("SPY was not present in the returned column levels.")
                if isinstance(spy.columns, pd.MultiIndex):
                    fields = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
                    spy.columns = [
                        next((str(part) for part in col if str(part) in fields), str(col[-1]))
                        for col in spy.columns
                    ]

            missing = required.difference(map(str, spy.columns))
            if missing:
                raise RuntimeError(f"SPY response is missing columns: {sorted(missing)}")

            spy = spy.dropna(subset=["Close"]).sort_index()
            if len(spy) < 200:
                raise RuntimeError(f"SPY response has only {len(spy)} valid rows; at least 200 are required.")
            return spy

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                delay = 10 * (2 ** attempt)
                print(
                    f"SPY download attempt {attempt + 1}/{attempts} failed ({last_error}); "
                    f"retrying in {delay}s.",
                    flush=True,
                )
                time.sleep(delay)

    raise SystemExit(
        "Live scan aborted safely: unable to obtain a valid SPY benchmark after "
        f"{attempts} attempts ({last_error}). No new scan was published."
    )



def add_quality_control_flags(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add transparent review flags without changing the heuristic ranking."""
    out = candidates.copy()
    flag_list = []
    statuses = []
    contexts = []

    def number(row, key):
        try:
            value = float(row.get(key))
            return value if pd.notna(value) else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    for _, row in out.iterrows():
        flags = []
        rvol = number(row, "RVOL20")
        acceleration = number(row, "VolumeAcceleration")
        trigger_atr = number(row, "BreakoutATR")
        atr_pct = number(row, "ATR20Pct")
        risk_atr = number(row, "RiskATR")
        direction = str(row.get("Direction", ""))
        regime_text = str(row.get("MarketRegime", ""))

        # These are warnings for review, not automatic trade-rejection rules.
        if pd.notna(acceleration) and acceleration >= 10:
            flags.append("EXTREME VOLUME ACCELERATION (>=10x prior day)")
        elif pd.notna(acceleration) and acceleration >= 5:
            flags.append("HIGH VOLUME ACCELERATION (>=5x prior day)")
        if pd.notna(rvol) and rvol >= 10:
            flags.append("EXTREME RVOL (>=10x 20-day average)")
        if pd.notna(trigger_atr) and trigger_atr >= 4:
            flags.append("EXTENDED TRIGGER (>=4 ATR)")
        if pd.notna(atr_pct) and atr_pct >= 0.08:
            flags.append("HIGH VOLATILITY (ATR20 >=8% of price)")
        if pd.notna(risk_atr) and risk_atr < 0.5:
            flags.append("TIGHT REFERENCE STOP (<0.5 ATR)")

        countertrend = (
            (regime_text.startswith("Bullish") and direction == "Short")
            or (regime_text.startswith("Bearish") and direction == "Long")
        )
        if countertrend:
            flags.append("COUNTERTREND VS SPY REGIME")

        severe_data_warning = (
            (pd.notna(acceleration) and acceleration >= 10)
            or (pd.notna(rvol) and rvol >= 10)
        )
        risk_warning = (
            (pd.notna(trigger_atr) and trigger_atr >= 4)
            or (pd.notna(atr_pct) and atr_pct >= 0.08)
            or (pd.notna(risk_atr) and risk_atr < 0.5)
        )

        if severe_data_warning:
            status = "CHECK — unusual volume; verify raw data"
        elif risk_warning:
            status = "CHECK — extension / volatility / stop distance"
        elif countertrend:
            status = "COUNTERTREND — verify market context"
        else:
            status = "STANDARD REVIEW — still research only"

        flag_list.append("; ".join(flags) if flags else "No automatic QC flags")
        statuses.append(status)
        contexts.append("Countertrend vs SPY" if countertrend else "Aligned / neutral vs SPY")

    out["QualityStatus"] = statuses
    out["QualityFlags"] = flag_list
    out["MarketContext"] = contexts
    return out


def main():
    cfg = load_config()
    data_dir = ROOT / "data"
    artifacts = ROOT / "artifacts"
    data_dir.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    # Fetch the benchmark first so stock-download rate limiting cannot leave
    # the scanner with no SPY reference series.
    spy = download_spy_with_retry()
    regime_series = classify_regime(spy).dropna()
    if regime_series.empty:
        raise SystemExit("Live scan aborted safely: SPY regime could not be calculated.")
    regime = str(regime_series.iloc[-1])

    universe = fetch_us_universe(data_dir / "universe.csv", refresh=True)
    if universe.empty:
        raise SystemExit("Live scan aborted safely: US stock universe is empty.")

    mapping = dict(zip(universe["YahooSymbol"], universe["Symbol"])) if "YahooSymbol" in universe.columns else {}
    symbols = (
        universe["YahooSymbol"].dropna().astype(str).tolist()
        if "YahooSymbol" in universe.columns
        else universe["Symbol"].dropna().astype(str).tolist()
    )
    # Smaller batches and a longer pause reduce, but cannot eliminate,
    # Yahoo Finance rate limits. Individual missing tickers are skipped.
    price_map = download_daily(symbols, period="1y", batch_size=50, sleep_seconds=1.25)
    print(f"Downloaded {len(price_map)} of {len(symbols)} universe symbols.", flush=True)

    # First apply the inexpensive historical-price and dollar-volume filters.
    # Only the reduced pool gets a per-symbol market-cap metadata lookup.
    liquid_symbols = []
    for yahoo_ticker, prices in price_map.items():
        try:
            if prices is None or prices.empty or not {"Close", "Volume"}.issubset(prices.columns):
                continue
            latest = prices.iloc[-1]
            avg_dollar = (prices["Close"] * prices["Volume"]).rolling(20, min_periods=20).mean().iloc[-1]
            if pd.isna(latest["Close"]) or pd.isna(avg_dollar):
                continue
            if (
                float(latest["Close"]) < cfg["universe"]["min_price"]
                or float(avg_dollar) < cfg["universe"]["min_avg_dollar_volume_20d"]
            ):
                continue
            liquid_symbols.append({
                "YahooSymbol": yahoo_ticker,
                "Prices": prices,
                "AvgDollarVolume20D": float(avg_dollar),
            })
        except Exception as exc:
            print(f"SKIP {yahoo_ticker}: {type(exc).__name__}: {exc}", flush=True)

    min_market_cap = float(cfg["universe"].get("min_market_cap_usd", 500_000_000))
    market_caps = fetch_market_caps(
        [row["YahooSymbol"] for row in liquid_symbols],
        max_workers=int(cfg["universe"].get("market_cap_lookup_workers", 4)),
    )
    market_cap_known = sum(value is not None for value in market_caps.values())
    market_cap_passed = sum(
        market_cap_meets_threshold(value, min_market_cap) for value in market_caps.values()
    )
    market_cap_rejected = sum(
        value is not None and not market_cap_meets_threshold(value, min_market_cap)
        for value in market_caps.values()
    )
    market_cap_unavailable = sum(value is None for value in market_caps.values())
    print(
        "Universe filters: "
        f"{len(liquid_symbols)} passed price/dollar-volume; "
        f"{market_cap_passed} passed market cap >= ${min_market_cap:,.0f}; "
        f"{market_cap_rejected} below threshold; "
        f"{market_cap_unavailable} with unavailable market cap (excluded).",
        flush=True,
    )

    rows = []
    for item in liquid_symbols:
        yahoo_ticker = item["YahooSymbol"]
        market_cap = market_caps.get(yahoo_ticker)
        if not market_cap_meets_threshold(market_cap, min_market_cap):
            continue
        try:
            featured = add_features(item["Prices"], spy=spy)
            longs, shorts = scan_symbol(featured, mapping.get(yahoo_ticker, yahoo_ticker), regime, cfg)
            for candidate in longs + shorts:
                candidate["MarketCapUSD"] = float(market_cap)
                candidate["AvgDollarVolume20D"] = float(item["AvgDollarVolume20D"])
                rows.append(candidate)
        except Exception as exc:
            print(f"SKIP {yahoo_ticker}: {type(exc).__name__}: {exc}", flush=True)

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise SystemExit(
            "No candidates generated; no new scan was published. "
            f"{len(liquid_symbols)} symbols passed price/dollar-volume filters; "
            f"{market_cap_passed} also passed market cap; "
            f"{market_cap_unavailable} market-cap lookups were unavailable and those symbols were excluded."
        )

    # Do not publish unvalidated historical-model probabilities, expected-R
    # estimates, or A/B/C tiers. Current setup scores are heuristic rankings only.
    candidates.drop(columns=["Probability", "ExpectedR", "Priority", "MarketRegimeNum"], errors="ignore", inplace=True)
    candidates.rename(columns={"Entry": "SignalClose"}, inplace=True)
    candidates["ResearchStatus"] = "RESEARCH ONLY — heuristic score; no validated probability"
    candidates["SignalDate"] = pd.Timestamp(spy.index[-1]).date().isoformat()
    candidates["UniverseSize"] = len(universe)
    candidates["MarketRegime"] = regime
    candidates = add_quality_control_flags(candidates)
    candidates = candidates.sort_values(
        ["Direction", "SetupScore"], ascending=[True, False], kind="mergesort"
    ).reset_index(drop=True)

    output_path = data_dir / "latest_scan.parquet"
    candidates.to_parquet(output_path, index=False)

    status = {
        "status": "success",
        "research_only": True,
        "production_ready": False,
        "probabilities_enabled": False,
        "ranking_method": "heuristic setup score; not a validated probability",
        "signal_date": pd.Timestamp(spy.index[-1]).date().isoformat(),
        "universe_size": int(len(universe)),
        "symbols_requested": int(len(symbols)),
        "symbols_downloaded": int(len(price_map)),
        "min_price_usd": float(cfg["universe"]["min_price"]),
        "min_market_cap_usd": min_market_cap,
        "min_avg_dollar_volume_20d_usd": float(cfg["universe"]["min_avg_dollar_volume_20d"]),
        "liquid_universe_candidates": int(len(liquid_symbols)),
        "market_cap_lookups_attempted": int(len(market_caps)),
        "market_cap_known": int(market_cap_known),
        "market_cap_qualified": int(market_cap_passed),
        "market_cap_rejected_below_min": int(market_cap_rejected),
        "market_cap_unavailable_excluded": int(market_cap_unavailable),
        "market_cap_source": "Yahoo Finance fast_info with standard info fallback; unknown values excluded",
        "candidates": int(len(candidates)),
        "longs": int((candidates["Direction"] == "Long").sum()),
        "shorts": int((candidates["Direction"] == "Short").sum()),
        "qc_flagged_candidates": int((candidates["QualityFlags"] != "No automatic QC flags").sum()),
        "qc_unusual_volume_candidates": int(candidates["QualityStatus"].str.startswith("CHECK — unusual volume").sum()),
        "qc_countertrend_candidates": int(candidates["MarketContext"].eq("Countertrend vs SPY").sum()),
        "regime": regime,
        "qc_note": "QC flags are review prompts only. They do not change the heuristic score or certify trade quality.",
        "execution_note": "SignalClose and stop/target are reference levels from the signal-date close. The planned entry is next-session open; recalculate levels after any opening gap.",
    }
    (artifacts / "live_scan_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2), flush=True)


if __name__ == "__main__":
    main()
