# V16 Prospective QC Shadow Test — Restarted Cohort

- **Status:** Awaiting first successful scheduled scan with corrected universe filters
- **Research only:** Yes
- **Production ready:** No
- **Cohort reset date:** 2026-10-14
- **Active signal events:** 0
- **Active signal dates:** 0

## Why the cohort was restarted

The earlier live scanner applied the minimum price and average dollar-volume filters but did not enforce the configured **US$500 million minimum market capitalization**. Signals dated 2026-10-12 and 2026-10-13 have been preserved in the pre-fix archive and are excluded from the active prospective metrics so results use a consistent universe definition.

Archive: `data/archive/v16_pre_market_cap_filter_2026-10-14/`

Archive report: `artifacts/archive/v16_pre_market_cap_filter_2026-10-14/report.md`

## Active universe filters

| Filter | Requirement |
|---|---:|
| Closing price | At least US$5 |
| Market capitalization | At least US$500 million |
| 20-session average daily dollar volume | At least US$20 million |

Market cap is retrieved from Yahoo Finance `fast_info` with a standard-info fallback. Symbols with unavailable market-cap data are excluded and counted in `artifacts/live_scan_status.json`.

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 session | 0 | 0 | 0 |
| 3 sessions | 0 | 0 | 0 |
| 5 sessions | 0 | 0 | 0 |
| 10 sessions | 0 | 0 | 0 |

No performance estimate is available until the corrected scanner seeds its first eligible cohort and the specified outcome horizons mature.

## Evaluation guardrails

- Base costs: 10 bps round trip and 3% annualized short borrow.
- Stress costs: 25 bps round trip and 10% annualized short borrow.
- Confidence intervals require at least 20 scorable dates; the decision gate requires at least 200 scorable signal dates under both cost scenarios.
- QC-filter improvement requires at least 200 paired dates and a positive paired-difference confidence-interval lower bound under both cost scenarios.
- Results remain research-only; passing a statistical gate would justify further replication, not approval to trade.

See [the cohort correction note](../../docs/universe_filter_change_2026-10-14.md) and [the pre-fix archive](../../data/archive/v16_pre_market_cap_filter_2026-10-14/report.md).
