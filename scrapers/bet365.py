"""Bet365 scraper (stub — replace with real HTTP/API integration)."""

from __future__ import annotations

import logging

from core.models import OddsQuote
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class Bet365Scraper(BaseScraper):
    bookmaker_name = "bet365"

    def fetch_odds(self) -> list[OddsQuote]:
        """
        TODO: implement real scraping / API calls.

        Returns demo quotes so the pipeline can be exercised end-to-end.
        """
        logger.debug("[%s] fetch_odds called (demo data)", self.bookmaker_name)
        return [
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="home",
                odds=2.10,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="draw",
                odds=3.40,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="away",
                odds=3.60,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
        ]
