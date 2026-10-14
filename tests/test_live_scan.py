import unittest

from scanner.market_cap import (
    extract_market_cap_usd,
    fetch_market_caps,
    get_market_cap_usd,
    market_cap_meets_threshold,
)


class FakeTicker:
    def __init__(self, fast_info=None, info=None, fast_error=False, info_error=False):
        self._fast_info = fast_info or {}
        self._info = info or {}
        self.fast_error = fast_error
        self.info_error = info_error
        self.info_calls = 0

    @property
    def fast_info(self):
        if self.fast_error:
            raise RuntimeError("simulated fast metadata error")
        return self._fast_info

    def get_info(self):
        self.info_calls += 1
        if self.info_error:
            raise RuntimeError("simulated standard metadata error")
        return self._info


class MarketCapLookupTests(unittest.TestCase):
    def test_extracts_snake_case_market_cap_from_fast_info(self):
        ticker = FakeTicker(fast_info={"market_cap": 750_000_000})
        self.assertEqual(extract_market_cap_usd(ticker), 750_000_000)
        self.assertEqual(ticker.info_calls, 0)

    def test_extracts_camel_case_market_cap_from_fast_info(self):
        ticker = FakeTicker(fast_info={"marketCap": 650_000_000})
        self.assertEqual(extract_market_cap_usd(ticker), 650_000_000)

    def test_falls_back_to_standard_info_when_fast_info_is_missing(self):
        ticker = FakeTicker(fast_info={}, info={"marketCap": 600_000_000})
        self.assertEqual(extract_market_cap_usd(ticker), 600_000_000)
        self.assertEqual(ticker.info_calls, 1)

    def test_falls_back_when_fast_info_raises(self):
        ticker = FakeTicker(info={"marketCap": 900_000_000}, fast_error=True)
        self.assertEqual(extract_market_cap_usd(ticker), 900_000_000)

    def test_unknown_or_nonpositive_market_cap_is_not_fabricated(self):
        for ticker in (
            FakeTicker(fast_info={}, info={}),
            FakeTicker(fast_info={"market_cap": 0}, info={"marketCap": None}),
            FakeTicker(fast_info={"market_cap": "not-a-number"}, info_error=True),
        ):
            self.assertIsNone(extract_market_cap_usd(ticker))

    def test_get_market_cap_uses_injected_ticker_factory(self):
        result = get_market_cap_usd(
            "ABC",
            ticker_factory=lambda symbol: FakeTicker(fast_info={"market_cap": 800_000_000}),
        )
        self.assertEqual(result, 800_000_000)

    def test_batch_lookup_returns_none_for_unavailable_symbols(self):
        def factory(symbol):
            if symbol == "MISSING":
                raise RuntimeError("not found")
            return FakeTicker(fast_info={"market_cap": 700_000_000})

        result = fetch_market_caps(["AAA", "MISSING", "AAA"], max_workers=2, ticker_factory=factory)
        self.assertEqual(set(result), {"AAA", "MISSING"})
        self.assertEqual(result["AAA"], 700_000_000)
        self.assertIsNone(result["MISSING"])

    def test_market_cap_filter_requires_known_cap_at_or_above_threshold(self):
        minimum = 500_000_000
        self.assertTrue(market_cap_meets_threshold(500_000_000, minimum))
        self.assertTrue(market_cap_meets_threshold(500_000_001, minimum))
        self.assertFalse(market_cap_meets_threshold(499_999_999, minimum))
        self.assertFalse(market_cap_meets_threshold(None, minimum))
        self.assertFalse(market_cap_meets_threshold(float("nan"), minimum))


if __name__ == "__main__":
    unittest.main(verbosity=2)
