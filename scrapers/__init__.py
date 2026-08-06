"""Bookmaker scrapers. Each module implements BaseScraper."""

from __future__ import annotations

import logging

from scrapers.base import BaseScraper
from scrapers.bet365 import Bet365Scraper
from scrapers.draftkings import DraftKingsScraper
from scrapers.fanduel import FanDuelScraper

logger = logging.getLogger(__name__)

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "bet365": Bet365Scraper,
    "draftkings": DraftKingsScraper,
    "fanduel": FanDuelScraper,
}


def build_scrapers(active: tuple[str, ...] | list[str]) -> list[BaseScraper]:
    """Instantiate scrapers listed in config.active_bookmakers."""
    scrapers: list[BaseScraper] = []
    for key in active:
        cls = SCRAPER_REGISTRY.get(key.lower())
        if cls is None:
            logger.error("Unknown bookmaker key: %s", key)
            continue
        scrapers.append(cls())
        logger.info("Enabled scraper: %s", key)
    return scrapers


__all__ = [
    "BaseScraper",
    "Bet365Scraper",
    "DraftKingsScraper",
    "FanDuelScraper",
    "SCRAPER_REGISTRY",
    "build_scrapers",
]
