# V16 Prospective QC Shadow Test

- Status: **success**
- Research only: **True**
- Latest market date: **2026-10-19**
- Signal events: **1,086** across **4** signal dates
- Long / Short: **490 / 596**
- New events added on this run: **92**

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 sessions | 994 | 92 | 0 |
| 3 sessions | 254 | 831 | 1 |
| 5 sessions | 0 | 1,086 | 0 |
| 10 sessions | 0 | 1,086 | 0 |

## Primary portfolio view

Top 10 candidates, 5-session horizon, Base cost assumption. Returns are normalized to 10 target slots; unused slots are treated as cash. Confidence intervals are withheld until there are at least 20 scorable signal dates.

| Direction | QC group | Signal dates | Scorable | Pending | Unscorable | Mean net portfolio return/date | 95% CI | Evidence gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| Long | All | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | No Unusual Volume | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Clear | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Flagged | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | Unusual Volume | 4 | 2 | 2 | 0 | 0.00% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | All | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | No Unusual Volume | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Clear | 4 | 1 | 3 | 0 | 0.00% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Flagged | 4 | 0 | 4 | 0 | — | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | Unusual Volume | 4 | 2 | 2 | 0 | 0.00% | Not available yet | Collecting; need 200 scorable dates under both cost cases |

## QC filter comparison

Top 10 QC group minus All. A positive value favors the QC group; for a QC-clear filter, the 95% paired-difference interval must be entirely above zero under both cost assumptions and there must be at least 200 paired dates.

| Direction | Comparison | Paired dates | Mean difference | 95% CI | Evidence gate |
|---|---|---:|---:|---|---|
| Long | QC Clear minus All | 0 | — | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Long | QC Flagged minus All | 0 | — | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Clear minus All | 0 | — | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Flagged minus All | 0 | — | Not available yet | Collecting; need 200 paired dates under both cost cases |

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
