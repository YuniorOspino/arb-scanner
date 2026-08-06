"""Compatibility re-exports for arbitrage scanning helpers."""

from core.arbitrage import calculate_arbitrage_stakes, scan_multi_book_arbitrage

__all__ = ["scan_multi_book_arbitrage", "calculate_arbitrage_stakes"]
