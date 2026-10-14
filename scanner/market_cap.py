"""Free Yahoo Finance market-cap lookups for the live universe filter.

Metadata lookups are only made after price and dollar-volume filters reduce the
universe. Missing or invalid market cap is treated as unknown and fails closed:
the symbol is excluded rather than silently passing the user's minimum-size rule.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import yfinance as yf


def _positive_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def extract_market_cap_usd(ticker) -> float | None:
    """Read market cap from fast_info first, then the standard info response."""
    try:
        fast_info = ticker.fast_info
        for key in ("market_cap", "marketCap"):
            try:
                cap = _positive_float(fast_info.get(key))
            except Exception:
                cap = None
            if cap is not None:
                return cap
    except Exception:
        pass

    try:
        get_info = getattr(ticker, "get_info", None)
        info = get_info() if callable(get_info) else ticker.info
        if isinstance(info, dict):
            for key in ("marketCap", "market_cap"):
                cap = _positive_float(info.get(key))
                if cap is not None:
                    return cap
    except Exception:
        pass
    return None


def get_market_cap_usd(symbol: str, ticker_factory: Callable | None = None) -> float | None:
    """Fetch one symbol's USD market-cap estimate; return None on source failures."""
    try:
        factory = ticker_factory or yf.Ticker
        return extract_market_cap_usd(factory(str(symbol)))
    except Exception:
        return None


def fetch_market_caps(
    symbols,
    max_workers: int = 4,
    ticker_factory: Callable | None = None,
) -> dict[str, float | None]:
    """Look up market caps concurrently with a conservative worker limit."""
    unique_symbols = list(dict.fromkeys(str(s) for s in symbols if str(s).strip()))
    if not unique_symbols:
        return {}
    workers = max(1, min(int(max_workers), len(unique_symbols)))
    results: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_symbols = {
            executor.submit(get_market_cap_usd, symbol, ticker_factory): symbol
            for symbol in unique_symbols
        }
        for future in as_completed(future_symbols):
            symbol = future_symbols[future]
            try:
                results[symbol] = future.result()
            except Exception:
                results[symbol] = None
    return results


def market_cap_meets_threshold(value, minimum_usd: float) -> bool:
    """Unknown market cap does not pass a minimum-size rule."""
    cap = _positive_float(value)
    try:
        minimum = float(minimum_usd)
    except (TypeError, ValueError, OverflowError):
        return False
    return cap is not None and math.isfinite(minimum) and minimum > 0 and cap >= minimum
