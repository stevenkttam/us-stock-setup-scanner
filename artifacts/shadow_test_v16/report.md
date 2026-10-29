# V16 Prospective QC Shadow Test

- Status: **success**
- Research only: **True**
- Latest market date: **2026-10-29**
- Signal events: **3,123** across **12** signal dates
- Long / Short: **1,664 / 1,459**
- New events added on this run: **320**

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 sessions | 2,802 | 321 | 0 |
| 3 sessions | 2,346 | 777 | 0 |
| 5 sessions | 1,962 | 1,161 | 0 |
| 10 sessions | 714 | 2,409 | 0 |

## Primary portfolio view

Top 10 candidates, 5-session horizon, Base cost assumption. Returns are normalized to 10 target slots; unused slots are treated as cash. Confidence intervals are withheld until there are at least 20 scorable signal dates.

| Direction | QC group | Signal dates | Scorable | Pending | Unscorable | Mean net portfolio return/date | 95% CI | Evidence gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| Long | All | 12 | 5 | 7 | 0 | 1.19% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | No Unusual Volume | 12 | 5 | 7 | 0 | 1.18% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Clear | 12 | 5 | 7 | 0 | 1.18% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Flagged | 12 | 7 | 5 | 0 | -0.37% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | Unusual Volume | 12 | 7 | 5 | 0 | -0.36% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | All | 12 | 7 | 5 | 0 | -1.16% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | No Unusual Volume | 12 | 7 | 5 | 0 | -1.32% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Clear | 12 | 10 | 2 | 0 | -0.44% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Flagged | 12 | 7 | 5 | 0 | -0.28% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | Unusual Volume | 12 | 8 | 4 | 0 | -0.29% | Not available yet | Collecting; need 200 scorable dates under both cost cases |

## QC filter comparison

Top 10 QC group minus All. A positive value favors the QC group; for a QC-clear filter, the 95% paired-difference interval must be entirely above zero under both cost assumptions and there must be at least 200 paired dates.

| Direction | Comparison | Paired dates | Mean difference | 95% CI | Evidence gate |
|---|---|---:|---:|---|---|
| Long | QC Clear minus All | 5 | -0.01% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Long | QC Flagged minus All | 5 | -1.43% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Clear minus All | 7 | 0.53% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Flagged minus All | 7 | 0.88% | Not available yet | Collecting; need 200 paired dates under both cost cases |

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
