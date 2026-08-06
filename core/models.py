"""Domain models for odds and arbitrage opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class OddsQuote:
    """Single outcome quote from one bookmaker."""

    bookmaker: str
    outcome: str
    odds: float
    market_id: str = ""
    event_name: str = ""
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.odds <= 1.0:
            raise ValueError(f"Odds must be > 1.0, got {self.odds}")


@dataclass
class MarketOdds:
    """All quotes for a single market across bookmakers."""

    event_name: str
    market_type: str  # e.g. "1X2", "moneyline", "totals"
    quotes: list[OddsQuote] = field(default_factory=list)

    def quotes_by_outcome(self) -> dict[str, list[OddsQuote]]:
        grouped: dict[str, list[OddsQuote]] = {}
        for q in self.quotes:
            grouped.setdefault(q.outcome, []).append(q)
        return grouped


@dataclass(frozen=True)
class ArbitrageOpportunity:
    """A detected arbitrage opportunity with stake allocation."""

    event_name: str
    market_type: str
    profit_percent: float
    total_stake: float
    legs: tuple[tuple[str, str, float, float], ...]
    # each leg: (bookmaker, outcome, odds, stake)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expected_profit(self) -> float:
        return self.total_stake * (self.profit_percent / 100.0)

    def summary(self) -> str:
        lines = [
            f"ARB {self.profit_percent:.2f}% | {self.event_name}",
            f"Market: {self.market_type} | Stake: {self.total_stake:.2f}",
            f"Expected profit: {self.expected_profit:.2f}",
        ]
        for bookmaker, outcome, odds, stake in self.legs:
            lines.append(
                f"  - {bookmaker}: {outcome} @ {odds:.3f} -> stake {stake:.2f}"
            )
        return "\n".join(lines)
