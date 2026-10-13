import unittest

import numpy as np
import pandas as pd

from scripts.update_shadow_test_v16 import (
    add_new_signal_rows,
    calculate_outcome,
    date_text,
    horizon_dates,
    net_directional_pnl,
    make_daily_portfolios,
    summarize_daily_portfolios,
    update_outcomes,
    normalize_dates,
    quality_flags,
    signal_key,
)


class ShadowTrackerTests(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.DatetimeIndex(pd.to_datetime([
            "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
            "2026-01-09", "2026-01-12", "2026-01-13",
        ]))

    def test_horizon_uses_next_open_and_h_session_close_dates(self):
        entry_1, exit_1, status_1 = horizon_dates(self.calendar, "2026-01-05", 1)
        entry_3, exit_3, status_3 = horizon_dates(self.calendar, "2026-01-05", 3)
        self.assertEqual(date_text(entry_1), "2026-01-06")
        self.assertEqual(date_text(exit_1), "2026-01-06")
        self.assertEqual(date_text(entry_3), "2026-01-06")
        self.assertEqual(date_text(exit_3), "2026-01-08")
        self.assertEqual(status_1, "ready")
        self.assertEqual(status_3, "ready")

    def test_future_exit_remains_pending(self):
        entry, exit_date, status = horizon_dates(self.calendar, "2026-01-12", 5)
        self.assertEqual(date_text(entry), "2026-01-13")
        self.assertIsNone(exit_date)
        self.assertEqual(status, "pending_future_exit")

    def test_signal_key_is_date_ticker_direction(self):
        key = signal_key({"SignalDate": "2026-01-05 00:00:00", "Ticker": "ABC", "Direction": "Long"})
        self.assertEqual(key, ("2026-01-05", "ABC", "Long"))

    def test_normalize_dates_removes_duplicates(self):
        frame = pd.DataFrame(
            {"Open": [10.0, 11.0, 12.0], "Close": [10.5, 11.5, 12.5]},
            index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-06"]),
        )
        normalized = normalize_dates(frame)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(float(normalized.loc[pd.Timestamp("2026-01-06"), "Open"]), 12.0)

    def test_matched_stock_and_spy_return_calculation(self):
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        stock = pd.DataFrame(
            {"Open": [100.0, 101.0, 103.0], "Close": [100.0, 102.0, 106.0]},
            index=dates,
        )
        spy = pd.DataFrame(
            {"Open": [500.0, 500.0, 505.0], "Close": [500.0, 505.0, 515.0]},
            index=dates,
        )
        result = calculate_outcome(
            stock, spy, "2026-01-06", "2026-01-06", "Long", 1
        )
        self.assertAlmostEqual(result["StockReturn_1d"], 102.0 / 101.0 - 1.0, places=10)
        self.assertAlmostEqual(result["SPYReturn_1d"], 505.0 / 500.0 - 1.0, places=10)
        self.assertAlmostEqual(result["NetBase_1d"], (102.0 / 101.0 - 1.0) - 0.001, places=10)

    def test_short_net_return_includes_borrow_and_transaction_cost(self):
        value = net_directional_pnl("Short", 0.05, 5, "Base")
        expected = -0.05 - 0.001 - (0.03 * 5 / 252.0)
        self.assertAlmostEqual(value, expected, places=12)

    def test_stale_snapshot_is_not_seeded(self):
        current = pd.DataFrame([{
            "SignalDate": "2026-01-13",
            "Ticker": "ABC",
            "Direction": "Long",
            "SetupScore": 80.0,
            "SignalClose": 10.0,
            "QualityFlags": "No automatic QC flags",
        }])
        ledger, added, note = add_new_signal_rows(
            current, pd.DataFrame(), {}, self.calendar, True, "2026-01-12"
        )
        self.assertEqual(added, 0)
        self.assertTrue(ledger.empty)
        self.assertIn("scheduled run date", note)

    def test_matching_scheduled_snapshot_is_seeded_once(self):
        current = pd.DataFrame([{
            "SignalDate": "2026-01-13",
            "Ticker": "ABC",
            "Direction": "Long",
            "SetupScore": 80.0,
            "SignalClose": 10.0,
            "QualityFlags": "No automatic QC flags",
        }])
        ledger, added, note = add_new_signal_rows(
            current, pd.DataFrame(), {}, self.calendar, True, "2026-01-13"
        )
        self.assertEqual(added, 1)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger.iloc[0]["YahooSymbol"], "ABC")
        self.assertEqual(ledger.iloc[0]["SignalRank"], 1)

    def test_filtered_portfolio_returns_use_fixed_target_slot_denominator(self):
        ledger = pd.DataFrame([
            {
                "SignalDate": "2026-01-05",
                "Ticker": ticker,
                "Direction": "Long",
                "SignalRank": rank,
                "SetupScore": score,
                "QCFlagged": flagged,
                "QCUnusualVolume": False,
                "SignalBarStatus": "valid",
                "OutcomeStatus_1d": "complete",
                "OutcomeStatus_3d": "pending",
                "OutcomeStatus_5d": "pending",
                "OutcomeStatus_10d": "pending",
                "DirectionalGrossPnL_1d": gross,
                "DirectionalExcessVsSPY_1d": gross,
                "NetBase_1d": net,
                "NetStress_1d": net - 0.0015,
            }
            for ticker, rank, score, flagged, gross, net in [
                ("AAA", 1, 90, False, 0.10, 0.099),
                ("BBB", 2, 80, False, 0.10, 0.099),
                ("CCC", 3, 70, True, -0.01, -0.011),
                ("DDD", 4, 60, True, -0.01, -0.011),
            ]
        ])

        daily = make_daily_portfolios(ledger)
        base = daily[
            (daily["QCGroup"] == "All")
            & (daily["TopK"] == 10)
            & (daily["Horizon"] == 1)
            & (daily["CostScenario"] == "Base")
        ].iloc[0]
        clear = daily[
            (daily["QCGroup"] == "QC Clear")
            & (daily["TopK"] == 10)
            & (daily["Horizon"] == 1)
            & (daily["CostScenario"] == "Base")
        ].iloc[0]
        flagged = daily[
            (daily["QCGroup"] == "QC Flagged")
            & (daily["TopK"] == 10)
            & (daily["Horizon"] == 1)
            & (daily["CostScenario"] == "Base")
        ].iloc[0]

        self.assertAlmostEqual(float(base["SlotCoverage"]), 0.4, places=12)
        self.assertAlmostEqual(float(base["MeanNetPnL"]), (0.099 + 0.099 - 0.011 - 0.011) / 10, places=12)
        self.assertAlmostEqual(float(clear["SlotCoverage"]), 0.2, places=12)
        self.assertAlmostEqual(float(clear["MeanNetPnL"]), 0.099 * 2 / 10, places=12)
        self.assertAlmostEqual(float(clear["MeanNetPnLPerTrade"]), 0.099, places=12)
        self.assertAlmostEqual(float(flagged["MeanNetPnL"]), -0.011 * 2 / 10, places=12)

    def test_pending_high_ranked_signal_is_not_replaced_by_lower_rank(self):
        ledger = pd.DataFrame([
            {
                "SignalDate": "2026-01-05",
                "Ticker": ticker,
                "Direction": "Long",
                "SetupScore": score,
                "QCFlagged": False,
                "QCUnusualVolume": False,
                "SignalBarStatus": "valid",
                "OutcomeStatus_1d": status,
                "OutcomeStatus_3d": "pending",
                "OutcomeStatus_5d": "pending",
                "OutcomeStatus_10d": "pending",
                "DirectionalGrossPnL_1d": 0.05,
                "DirectionalExcessVsSPY_1d": 0.04,
                "NetBase_1d": 0.049,
                "NetStress_1d": 0.0475,
            }
            for ticker, score, status in [
                ("AAA", 90, "pending"),
                ("BBB", 80, "complete"),
                ("CCC", 70, "complete"),
            ]
        ])

        daily = make_daily_portfolios(ledger)
        all_portfolio = daily[
            (daily["QCGroup"] == "All")
            & (daily["TopK"] == 10)
            & (daily["Horizon"] == 1)
            & (daily["CostScenario"] == "Base")
        ].iloc[0]
        self.assertEqual(all_portfolio["PortfolioStatus"], "pending_outcomes")
        self.assertEqual(int(all_portfolio["PendingOutcomeSlots"]), 1)
        self.assertTrue(pd.isna(all_portfolio["MeanNetPnL"]))

    def test_update_outcomes_completes_only_matured_horizons(self):
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
        stock = pd.DataFrame(
            {"Open": [100.0, 101.0, 102.0, 103.0], "Close": [100.0, 102.0, 103.0, 104.0]},
            index=dates,
        )
        spy = pd.DataFrame(
            {"Open": [500.0, 500.0, 501.0, 502.0], "Close": [500.0, 501.0, 502.0, 504.0]},
            index=dates,
        )
        row = {
            "SignalDate": "2026-01-05",
            "Ticker": "ABC",
            "YahooSymbol": "ABC",
            "Direction": "Long",
            "SignalClose": 100.0,
            "SignalBarStatus": "pending",
            "TrackingStatus": "pending",
        }
        for horizon in (1, 3, 5, 10):
            row[f"OutcomeStatus_{horizon}d"] = "pending"
            for col in [
                f"ExitDate_{horizon}d", f"ExitPrice_{horizon}d",
                f"StockReturn_{horizon}d", f"SPYReturn_{horizon}d",
                f"DirectionalGrossPnL_{horizon}d", f"DirectionalExcessVsSPY_{horizon}d",
                f"NetBase_{horizon}d", f"NetStress_{horizon}d",
            ]:
                row[col] = np.nan
        ledger, counts = update_outcomes(pd.DataFrame([row]), {"ABC": stock}, spy)
        self.assertEqual(ledger.iloc[0]["SignalBarStatus"], "valid")
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_1d"], "complete")
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_3d"], "complete")
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_5d"], "pending")
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_10d"], "pending")
        self.assertEqual(ledger.iloc[0]["EntryDate"], "2026-01-06")
        self.assertEqual(ledger.iloc[0]["ExitDate_3d"], "2026-01-08")
        self.assertEqual(counts["complete"], 2)

    def test_update_outcomes_flags_missing_expected_bar_instead_of_shifting(self):
        all_dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
        stock_dates = pd.to_datetime(["2026-01-05", "2026-01-07", "2026-01-08"])
        stock = pd.DataFrame(
            {"Open": [100.0, 102.0, 103.0], "Close": [100.0, 103.0, 104.0]},
            index=stock_dates,
        )
        spy = pd.DataFrame(
            {"Open": [500.0, 500.0, 501.0, 502.0], "Close": [500.0, 501.0, 502.0, 504.0]},
            index=all_dates,
        )
        row = {
            "SignalDate": "2026-01-05", "Ticker": "ABC", "YahooSymbol": "ABC",
            "Direction": "Long", "SignalClose": 100.0, "SignalBarStatus": "valid",
            "TrackingStatus": "tracking",
        }
        for horizon in (1, 3, 5, 10):
            row[f"OutcomeStatus_{horizon}d"] = "pending"
        ledger, counts = update_outcomes(pd.DataFrame([row]), {"ABC": stock}, spy)
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_1d"], "invalid_missing_stock_bar")
        self.assertEqual(ledger.iloc[0]["OutcomeStatus_3d"], "invalid_missing_stock_bar")
        self.assertEqual(ledger.iloc[0]["EntryDate"] if "EntryDate" in ledger.columns else "", "")
        self.assertGreaterEqual(counts["invalid"], 0)

    def test_pending_portfolio_is_counted_but_not_scored(self):
        ledger = pd.DataFrame([{
            "SignalDate": "2026-01-05", "Ticker": "AAA", "Direction": "Long",
            "SetupScore": 90, "QCFlagged": False, "QCUnusualVolume": False,
            "SignalBarStatus": "valid", "OutcomeStatus_1d": "pending",
            "OutcomeStatus_3d": "pending", "OutcomeStatus_5d": "pending", "OutcomeStatus_10d": "pending",
            "DirectionalGrossPnL_1d": np.nan, "DirectionalExcessVsSPY_1d": np.nan,
            "NetBase_1d": np.nan, "NetStress_1d": np.nan,
        }])
        daily = make_daily_portfolios(ledger)
        summary = summarize_daily_portfolios(daily)
        all_row = summary[
            (summary["QCGroup"] == "All")
            & (summary["TopK"] == 10)
            & (summary["Horizon"] == 1)
            & (summary["CostScenario"] == "Base")
        ].iloc[0]
        self.assertEqual(int(all_row["SignalDates"]), 1)
        self.assertEqual(int(all_row["PendingDates"]), 1)
        self.assertEqual(int(all_row["ScorableDates"]), 0)
        self.assertTrue(pd.isna(all_row["MeanDailyNetPnL"]))

    def test_qc_flags_are_preserved_as_group_labels(self):
        result = quality_flags({
            "QualityFlags": "EXTREME VOLUME ACCELERATION (>=10x prior day); COUNTERTREND VS SPY REGIME"
        })
        self.assertTrue(result["QCFlagged"])
        self.assertTrue(result["QCUnusualVolume"])
        self.assertTrue(result["QCCountertrend"])

    def test_no_qc_flags_remains_clear(self):
        result = quality_flags({"QualityFlags": "No automatic QC flags"})
        self.assertFalse(result["QCFlagged"])
        self.assertFalse(result["QCUnusualVolume"])
        self.assertFalse(result["QCCountertrend"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
