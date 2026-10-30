# V16 Prospective QC Shadow Test

- Status: **success**
- Research only: **True**
- Latest market date: **2026-10-30**
- Signal events: **3,527** across **13** signal dates
- Long / Short: **1,848 / 1,679**
- New events added on this run: **404**

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 sessions | 3,122 | 405 | 0 |
| 3 sessions | 2,542 | 985 | 0 |
| 5 sessions | 2,183 | 1,344 | 0 |
| 10 sessions | 992 | 2,535 | 0 |

## Primary portfolio view

Top 10 candidates, 5-session horizon, Base cost assumption. Returns are normalized to 10 target slots; unused slots are treated as cash. Confidence intervals are withheld until there are at least 20 scorable signal dates.

| Direction | QC group | Signal dates | Scorable | Pending | Unscorable | Mean net portfolio return/date | 95% CI | Evidence gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| Long | All | 13 | 6 | 7 | 0 | 1.49% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | No Unusual Volume | 13 | 6 | 7 | 0 | 1.42% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Clear | 13 | 6 | 7 | 0 | 1.38% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Flagged | 13 | 8 | 5 | 0 | -0.22% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | Unusual Volume | 13 | 8 | 5 | 0 | -0.26% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | All | 13 | 8 | 5 | 0 | -1.32% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | No Unusual Volume | 13 | 8 | 5 | 0 | -1.46% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Clear | 13 | 11 | 2 | 0 | -0.56% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Flagged | 13 | 8 | 5 | 0 | -0.35% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | Unusual Volume | 13 | 8 | 5 | 0 | -0.29% | Not available yet | Collecting; need 200 scorable dates under both cost cases |

## QC filter comparison

Top 10 QC group minus All. A positive value favors the QC group; for a QC-clear filter, the 95% paired-difference interval must be entirely above zero under both cost assumptions and there must be at least 200 paired dates.

| Direction | Comparison | Paired dates | Mean difference | 95% CI | Evidence gate |
|---|---|---:|---:|---|---|
| Long | QC Clear minus All | 6 | -0.11% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Long | QC Flagged minus All | 6 | -1.56% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Clear minus All | 8 | 0.54% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Flagged minus All | 8 | 0.97% | Not available yet | Collecting; need 200 paired dates under both cost cases |

## Cost assumptions

- Base: 10 bps round-trip; 3% annualized short borrow.
- Stress: 25 bps round-trip; 10% annualized short borrow.
- These are fixed sensitivity assumptions, not broker-specific costs or guaranteed execution prices.

## Interpretation

This is a prospective research-only shadow test. It does not submit orders, change ranking scores, establish calibrated probabilities, or approve a strategy for real-money trading. A promising result requires independent replication and further execution-cost review.

## Detailed files

- data/shadow/shadow_ledger.csv
- data/shadow/shadow_daily_portfolios.csv
- data/shadow/shadow_group_summary.csv
- data/shadow/shadow_qc_comparisons.csv
