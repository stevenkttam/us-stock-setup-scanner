# V16 Prospective QC Shadow Test

- Status: **success**
- Research only: **True**
- Latest market date: **2026-11-04**
- Signal events: **4,061** across **15** signal dates
- Long / Short: **2,150 / 1,911**
- New events added on this run: **0**

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 sessions | 3,910 | 4 | 147 |
| 3 sessions | 3,117 | 543 | 401 |
| 5 sessions | 2,539 | 1,264 | 258 |
| 10 sessions | 1,288 | 2,595 | 178 |

## Primary portfolio view

Top 10 candidates, 5-session horizon, Base cost assumption. Returns are normalized to 10 target slots; unused slots are treated as cash. Confidence intervals are withheld until there are at least 20 scorable signal dates.

| Direction | QC group | Signal dates | Scorable | Pending | Unscorable | Mean net portfolio return/date | 95% CI | Evidence gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| Long | All | 15 | 7 | 8 | 0 | 1.85% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | No Unusual Volume | 15 | 7 | 8 | 0 | 1.79% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Clear | 15 | 7 | 8 | 0 | 1.38% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Flagged | 15 | 10 | 4 | 1 | -0.50% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | Unusual Volume | 15 | 11 | 3 | 1 | -0.39% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | All | 15 | 10 | 4 | 1 | -1.47% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | No Unusual Volume | 15 | 9 | 4 | 2 | -1.99% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Clear | 15 | 11 | 3 | 1 | -0.56% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Flagged | 15 | 10 | 4 | 1 | -0.52% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | Unusual Volume | 15 | 12 | 2 | 1 | -0.21% | Not available yet | Collecting; need 200 scorable dates under both cost cases |

## QC filter comparison

Top 10 QC group minus All. A positive value favors the QC group; for a QC-clear filter, the 95% paired-difference interval must be entirely above zero under both cost assumptions and there must be at least 200 paired dates.

| Direction | Comparison | Paired dates | Mean difference | 95% CI | Evidence gate |
|---|---|---:|---:|---|---|
| Long | QC Clear minus All | 7 | -0.47% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Long | QC Flagged minus All | 7 | -2.16% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Clear minus All | 9 | 1.03% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Flagged minus All | 10 | 0.96% | Not available yet | Collecting; need 200 paired dates under both cost cases |

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
