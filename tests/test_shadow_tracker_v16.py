import unittest

import numpy as np
import pandas as pd

from scripts.update_shadow_test_v16 import (
    calculate_outcome,
    date_text,
    horizon_dates,
    net_directional_pnl,
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
