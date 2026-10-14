# Universe Filter Correction and Shadow-Cohort Restart — 2026-10-14

## What changed

The live scanner now enforces all three configured universe thresholds:

- Minimum closing price: **US$5**
- Minimum market capitalization: **US$500 million**
- Minimum 20-session average daily dollar volume: **US$20 million**

The earlier implementation enforced the price and dollar-volume thresholds but did not enforce minimum market capitalization. The corrected scanner retrieves market capitalization from Yahoo Finance `fast_info`, falls back to the standard ticker info response, and excludes a symbol when market capitalization remains unavailable. The live scan status reports how many liquid symbols passed, failed, or had an unavailable market-cap lookup.

## Why the prospective cohort was restarted

The shadow ledger contained signals dated **2026-10-12** and **2026-10-13** before the market-cap check was enforced. Those observations do not represent the full configured universe definition and must not be mixed into the primary post-fix performance evaluation.

The pre-fix cohort has been retained, without deletion, under:

`data/archive/v16_pre_market_cap_filter_2026-10-14/`

Its status files and human-readable report are under:

`artifacts/archive/v16_pre_market_cap_filter_2026-10-14/`

The active shadow ledger and summaries have been reset to headers only. The new active cohort will start with the first successful scheduled after-close scan made with the corrected filter. Archived pre-fix performance must not be used to pass the new cohort's evidence gates.

## Example that prompted the data-quality review

WF Holding (WFF) is recorded by independent historical-price sources as closing around US$2.10–US$2.12 on 2026-10-13 after a highly volatile session, versus a US$17.28 close on 2026-10-12. See [StockAnalysis historical prices](https://stockanalysis.com/stocks/wff/history/) and [FinancialContent quote history](https://markets.financialcontent.com/stocks/quote?Language=english%2F1000&Symbol=NQ%3AWFF). The movement was real in the referenced daily data, not merely an obviously mistyped close. WFF is not consistent with the intended US$500 million minimum market-cap universe and therefore should not be present in future active-cohort signals.

## Research safeguards

- The scanner remains research-only and submits no orders.
- Market-cap lookup failures are visible in the scan status and fail closed.
- Ranking scores and QC thresholds were not changed by this correction.
- Use Base and Stress costs, the pre-declared bootstrap procedure, and the minimum 200 scorable-date evidence gate in the active prospective test.
- Treat this archive as a pre-fix audit trail, not as part of the active post-fix sample.
