# V16 Prospective QC Shadow Test

- Status: **success**
- Research only: **True**
- Latest market date: **2026-11-02**
- Signal events: **3,914** across **14** signal dates
- Long / Short: **2,075 / 1,839**
- New events added on this run: **387**

## Outcome maturity

| Holding horizon | Complete events | Pending events | Invalid/unusable events |
|---:|---:|---:|---:|
| 1 sessions | 3,522 | 392 | 0 |
| 3 sessions | 2,798 | 1,116 | 0 |
| 5 sessions | 2,343 | 1,571 | 0 |
| 10 sessions | 1,080 | 2,834 | 0 |

## Primary portfolio view

Top 10 candidates, 5-session horizon, Base cost assumption. Returns are normalized to 10 target slots; unused slots are treated as cash. Confidence intervals are withheld until there are at least 20 scorable signal dates.

| Direction | QC group | Signal dates | Scorable | Pending | Unscorable | Mean net portfolio return/date | 95% CI | Evidence gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| Long | All | 14 | 6 | 8 | 0 | 1.49% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | No Unusual Volume | 14 | 6 | 8 | 0 | 1.42% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Clear | 14 | 6 | 8 | 0 | 1.38% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | QC Flagged | 14 | 9 | 5 | 0 | -0.36% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Long | Unusual Volume | 14 | 9 | 5 | 0 | -0.24% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | All | 14 | 9 | 5 | 0 | -1.09% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | No Unusual Volume | 14 | 8 | 6 | 0 | -1.46% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Clear | 14 | 11 | 3 | 0 | -0.56% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | QC Flagged | 14 | 9 | 5 | 0 | -0.02% | Not available yet | Collecting; need 200 scorable dates under both cost cases |
| Short | Unusual Volume | 14 | 10 | 4 | 0 | -0.25% | Not available yet | Collecting; need 200 scorable dates under both cost cases |

## QC filter comparison

Top 10 QC group minus All. A positive value favors the QC group; for a QC-clear filter, the 95% paired-difference interval must be entirely above zero under both cost assumptions and there must be at least 200 paired dates.

| Direction | Comparison | Paired dates | Mean difference | 95% CI | Evidence gate |
|---|---|---:|---:|---|---|
| Long | QC Clear minus All | 6 | -0.11% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Long | QC Flagged minus All | 6 | -1.56% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Clear minus All | 8 | 0.54% | Not available yet | Collecting; need 200 paired dates under both cost cases |
| Short | QC Flagged minus All | 9 | 1.06% | Not available yet | Collecting; need 200 paired dates under both cost cases |

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
