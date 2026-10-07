# US Stock Setup Scanner — V1

A free-first, no-code-from-the-user research system for screening the whole US stock market every trading day.

## What V1 is designed to do

- Broad current US universe
- Long and short setup families
- Daily close scan
- Volume/relative-volume analysis
- Consolidation → breakout/breakdown logic
- ATR/volatility analysis
- Trend and market relative-strength features
- Primary trade definition: +2R before -1R within 10 trading sessions
- Separate long/short probability models
- Probability calibration and out-of-sample validation
- Streamlit dashboard
- GitHub Actions daily refresh
- 15m confirmation + 5m trigger layer for shortlisted names
- Sector-relative-strength hook reserved for the next data-enrichment pass; V1 probability training uses market relative strength to avoid slow per-ticker fundamental calls on the free stack

## Important status

This package contains the V1 rulebook, dashboard, scanner modules, and automation skeleton. The dashboard launches in SAMPLE mode so its layout can be reviewed without pretending that placeholder probabilities are live market data.

The live whole-universe pipeline should be completed in this order:

1. Build the historical signal dataset.
2. Run the walk-forward backtest.
3. Train/calibrate the separate long and short probability models.
4. Validate calibration and expected R out-of-sample.
5. Enable daily live scanning.
6. Add recent 15m/5m confirmation for shortlisted names.

## Free-stack deployment

1. Put this project in a public GitHub repository.
2. Deploy `app.py` through Streamlit Community Cloud.
3. Enable the GitHub Actions workflow for the daily refresh.
4. Keep the repository public if you want the free GitHub Actions standard runner allowance and the simplest Community Cloud workflow.

## Data caveats

- The current universe is a survivorship-biased list if reused unchanged for historical backtesting.
- Daily historical data quality must be checked around splits, delistings, corporate actions and missing observations.
- The free Yahoo Finance/yfinance path is suitable for a research prototype but is not the same as a professional market-data feed.
- Intraday 5m/15m history has substantially shorter availability than daily history, so it should be used as a recent execution-confirmation layer rather than a 10-year whole-market backtest source.

## Configuration

All primary thresholds are in `config.yaml`. Initial thresholds are deliberately exposed so the backtest can determine the eventual optimal ranges.


## Current research direction

The original daily technical framework failed the V9 independent confirmation test. The next research stage is V2 Context Enrichment, implemented in scripts/context_research_v10.py. It adds research-sample market breadth, SPY/QQQ/IWM market context, VIX regime, sector ETF breadth/dispersion, an inferred sector-relative-strength proxy, and next-open gap diagnostics. This workflow is research-only and does not enable live probabilities.

The V2 test uses a fresh 1,000-stock partition after the deterministic V8 and V9 research samples, with a chronological validation/holdout comparison against the existing V2 feature set. Current-universe survivorship bias remains.