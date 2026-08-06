"""DraftKings scraper (stub — replace with real HTTP/API integration)."""

from __future__ import annotations

import logging

from core.models import OddsQuote
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class DraftKingsScraper(BaseScraper):
    bookmaker_name = "draftkings"

    def fetch_odds(self) -> list[OddsQuote]:
        """
        TODO: implement real scraping / API calls.

        Demo quotes biased to create a detectable arb with other stubs.
        """
        logger.debug("[%s] fetch_odds called (demo data)", self.bookmaker_name)
        return [
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="home",
                odds=2.25,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="draw",
                odds=3.50,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="away",
                odds=3.40,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
        ]
