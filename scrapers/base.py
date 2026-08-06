"""Abstract scraper interface for bookmaker modules."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from core.models import OddsQuote

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """One scraper module per bookmaker."""

    bookmaker_name: str = "unknown"

    @abstractmethod
    def fetch_odds(self) -> list[OddsQuote]:
        """Fetch current odds. Must return decimal odds > 1.0."""

    def safe_fetch(self) -> list[OddsQuote]:
        """Wrapper that logs and swallows unexpected errors."""
        try:
            return self.fetch_odds()
        except Exception:
            logger.exception("[%s] fetch_odds failed", self.bookmaker_name)
            return []
