import unittest
import numpy as np
import pandas as pd
from scripts.eod_setup_backtest_v17 import block_bootstrap_ci, deterministic_sample, normalize_frame, simulate_trade


class V17BacktestTests(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.DatetimeIndex(pd.to_datetime([
            "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"
        ]))
        self.spy = pd.DataFrame({
            "Open": [100, 101, 102, 103, 104], "High": [101, 102, 103, 104, 105],
            "Low": [99, 100, 101, 102, 103], "Close": [100, 101, 102, 103, 104],
            "Volume": [1_000_000] * 5,
        }, index=self.calendar)
        self.prices = pd.DataFrame({
            "Open": [10, 10, 10, 10, 10], "High": [10.2, 10.3, 10.5, 10.1, 10.1],
            "Low": [9.8, 9.9, 9.7, 9.9, 9.9], "Close": [10, 10.1, 10.2, 10, 10],
            "Volume": [1_000_000] * 5,
        }, index=self.calendar)
        self.candidate = {
            "CandidateID": "2026-01-05|AAA|Long", "SignalDate": pd.Timestamp("2026-01-05"),
            "Ticker": "AAA", "Direction": "Long", "Setup": "Test", "SetupScore": 80.0,
            "SignalClose": 10.0, "Stop": 9.0, "Target2R": 12.0,
        }

    def test_deterministic_sample_is_stable_and_unique(self):
        a = deterministic_sample(["AAA", "BBB", "AAA", "CCC"], 2)
        b = deterministic_sample(["CCC", "BBB", "AAA"], 2)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2)
        self.assertEqual(len(set(a)), 2)

    def test_normalize_frame_strips_duplicate_dates(self):
        out = normalize_frame(pd.concat([self.prices, self.prices.iloc[[-1]]]))
        self.assertEqual(len(out), len(self.prices))
        self.assertFalse(out.index.has_duplicates)

    def test_stop_hit_exits_at_stop(self):
        prices = self.prices.copy()
        prices.loc[pd.Timestamp("2026-01-06"), ["Open", "High", "Low", "Close"]] = [10, 10.5, 8.8, 9.0]
        out = simulate_trade(self.candidate, prices, self.spy, self.calendar, 1)
        self.assertEqual(out["OutcomeStatus"], "complete")
        self.assertEqual(out["ExitReason"], "stop")
        self.assertAlmostEqual(out["ExitPrice"], 9.0)

    def test_target_hit_exits_at_target(self):
        prices = self.prices.copy()
        prices.loc[pd.Timestamp("2026-01-06"), ["Open", "High", "Low", "Close"]] = [10, 12.5, 9.8, 12]
        out = simulate_trade(self.candidate, prices, self.spy, self.calendar, 1)
        self.assertEqual(out["OutcomeStatus"], "complete")
        self.assertEqual(out["ExitReason"], "target")
        self.assertAlmostEqual(out["ExitPrice"], 12.0)

    def test_stop_first_when_both_hit_same_bar(self):
        prices = self.prices.copy()
        prices.loc[pd.Timestamp("2026-01-06"), ["Open", "High", "Low", "Close"]] = [10, 12.5, 8.8, 10]
        out = simulate_trade(self.candidate, prices, self.spy, self.calendar, 1)
        self.assertEqual(out["ExitReason"], "stop_and_target_same_bar_stop_first")
        self.assertAlmostEqual(out["ExitPrice"], 9.0)

    def test_stop_gap_fills_at_open(self):
        prices = self.prices.copy()
        # Entry is at 10 on Jan 6. The recalculated stop is 9, and the next
        # session opens below it, so the stop should fill at that day's open.
        prices.loc[pd.Timestamp("2026-01-07"), ["Open", "High", "Low", "Close"]] = [8.7, 9.0, 8.4, 8.8]
        out = simulate_trade(self.candidate, prices, self.spy, self.calendar, 2)
        self.assertEqual(out["ExitReason"], "stop_gap")
        self.assertAlmostEqual(out["ExitPrice"], 8.7)

    def test_missing_exact_entry_bar_is_invalid_not_shifted(self):
        prices = self.prices.drop(pd.Timestamp("2026-01-06"))
        out = simulate_trade(self.candidate, prices, self.spy, self.calendar, 1)
        self.assertEqual(out["OutcomeStatus"], "invalid_missing_stock_entry_bar")

    def test_bootstrap_cis_wait_for_two_full_blocks(self):
        mean, low, high = block_bootstrap_ci(np.linspace(-0.1, 0.1, 19))
        self.assertTrue(np.isfinite(mean))
        self.assertTrue(np.isnan(low))
        self.assertTrue(np.isnan(high))
        mean, low, high = block_bootstrap_ci(np.linspace(-0.1, 0.2, 30), reps=100)
        self.assertTrue(np.isfinite(mean))
        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))


if __name__ == "__main__":
    unittest.main(verbosity=2)
