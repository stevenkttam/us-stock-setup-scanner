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

    rows = []
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

            featured = add_features(prices, spy=spy)
            longs, shorts = scan_symbol(featured, mapping.get(yahoo_ticker, yahoo_ticker), regime, cfg)
            rows.extend(longs + shorts)
        except Exception as exc:
            print(f"SKIP {yahoo_ticker}: {type(exc).__name__}: {exc}", flush=True)

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise SystemExit("No candidates generated; no new scan was published.")

    # Do not publish unvalidated historical-model probabilities, expected-R
    # estimates, or A/B/C tiers. Current setup scores are heuristic rankings only.
    candidates.drop(columns=["Probability", "ExpectedR", "Priority", "MarketRegimeNum"], errors="ignore", inplace=True)
    candidates.rename(columns={"Entry": "SignalClose"}, inplace=True)
    candidates["ResearchStatus"] = "RESEARCH ONLY — heuristic score; no validated probability"
    candidates["SignalDate"] = pd.Timestamp(spy.index[-1]).date().isoformat()
    candidates["UniverseSize"] = len(universe)
    candidates["MarketRegime"] = regime
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
        "candidates": int(len(candidates)),
        "longs": int((candidates["Direction"] == "Long").sum()),
        "shorts": int((candidates["Direction"] == "Short").sum()),
        "regime": regime,
        "execution_note": "SignalClose and stop/target are reference levels from the signal-date close. The planned entry is next-session open; recalculate levels after any opening gap.",
    }
    (artifacts / "live_scan_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2), flush=True)


if __name__ == "__main__":
    main()
