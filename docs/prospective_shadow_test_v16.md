# V16 Prospective QC Shadow Test — Evaluation Protocol

## Purpose

This is a prospective, research-only shadow test for the current US EOD setup scanner. It is designed to assess whether the heuristic rankings have forward-looking value and whether QC warning groups improve or weaken the result. It does not place orders and does not change live setup scores.

## Frozen signal and outcome convention

- Signal: end-of-day candidate from the successfully scheduled Live Daily Scan.
- Entry reference: next SPY trading session's official daily Open.
- Exit reference: daily Close on session +1, +3, +5, or +10 relative to the signal date.
- Stock and SPY bars must exist on the exact entry and exit dates. The tracker must not shift missing bars to substitute dates.
- Directional gross return is positive for a profitable Long or Short position.
- Net return uses an assumed 10 basis-point round-trip cost and 3% annual short borrow for Base; Stress uses 25 basis points and 10% annual short borrow. These are sensitivity assumptions, not broker quotes.
- Portfolios are selected from the frozen signal cohort before outcomes are inspected. A pending outcome stays pending; a terminal missing or invalid selected outcome makes that portfolio-date unscorable rather than substituting a lower-ranked name.
- Portfolio returns are normalized by the target slots. Unused slots are treated as cash earning 0%. Mean return per filled trade is reported separately.
- Top 10 is the primary portfolio size for the QC comparison. Top 40 is descriptive/secondary.

## Predeclared evidence gates

The primary review is the Top 10, 5-session holding horizon, evaluated separately for Long and Short and with QC groups reported separately. The 1-, 3-, and 10-session results are secondary checks.

1. A confidence interval is not displayed until at least 20 scorable signal dates exist, allowing at least two 10-session bootstrap blocks.
2. The decision gate requires at least 200 scorable signal dates for the group under both cost assumptions.
3. A strategy group is considered a *promising shadow-test result* only if its mean net return is positive and the 95% circular moving-block bootstrap interval lower bound is above zero under both Base and Stress assumptions.
4. A QC-filter improvement is considered supported only if the paired Top 10 \`QC Clear minus All\` return difference has at least 200 paired scorable dates and the 95% interval lower bound is above zero under both cost assumptions.
5. A positive result only justifies independent replication and fuller execution-cost work. It does not approve real-money trading, establish a calibrated probability, or guarantee future results.

The 200-date threshold is a conservative operational gate, not a universal statistical standard. Overlapping holding periods can still induce dependence; a 10-session block bootstrap partly addresses, but cannot eliminate, that issue.

## Review and interpretation

- Inspect scorable, pending, and unscorable dates and slots alongside all return estimates.
- A QC-flagged group outperforming the full group is evidence against automatically excluding those flags.
- A filter group that appears better only under Base but not Stress costs has not passed the gate.
- Compare Long and Short independently. Do not combine them into a single headline that masks one side's weakness.
- Do not optimize QC thresholds or ranking weights on the prospective test period. Changes create a new research version and should be evaluated on fresh observations.
- Revisit data anomalies, splits, symbol mapping, borrow, transaction costs, slippage, spreads, and survivorship limitations before interpreting any apparent edge.

## Current status at protocol adoption

The first cohort comprised 80 signal events on 2026-10-12 (36 Long, 44 Short) with valid signal-date bars. Forward outcomes were not yet mature at the time of the initial run. No conclusion about performance or edge is supported by the first cohort alone.
