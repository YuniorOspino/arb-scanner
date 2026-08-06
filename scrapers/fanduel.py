"""FanDuel scraper (stub — replace with real HTTP/API integration)."""

from __future__ import annotations

import logging

from core.models import OddsQuote
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class FanDuelScraper(BaseScraper):
    bookmaker_name = "fanduel"

    def fetch_odds(self) -> list[OddsQuote]:
        """
        TODO: implement real scraping / API calls.

        Demo away-leg quote high enough to form an arb when combined
        with best home/draw from other books.
        """
        logger.debug("[%s] fetch_odds called (demo data)", self.bookmaker_name)
        return [
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="home",
                odds=2.05,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="draw",
                odds=3.30,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
            OddsQuote(
                bookmaker=self.bookmaker_name,
                outcome="away",
                odds=3.90,
                market_id="1X2",
                event_name="Team A vs Team B",
            ),
        ]
