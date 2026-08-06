"""Compatibility re-exports for value-betting helpers."""

from core.arbitrage import (
    calculate_kelly_stake,
    calculate_market_consensus,
    detect_value_bet,
)

__all__ = [
    "calculate_market_consensus",
    "detect_value_bet",
    "calculate_kelly_stake",
]
