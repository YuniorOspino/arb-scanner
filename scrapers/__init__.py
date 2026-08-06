"""Bookmaker scrapers. Colombian houses for the active pipeline."""

from __future__ import annotations

import logging

from scrapers.base import BaseScraper
from scrapers.betano import BetanoScraper
from scrapers.betplay import BetPlayScraper
from scrapers.codere import CodereScraper
from scrapers.rushbet import RushBetScraper
from scrapers.wplay import WplayScraper
from scrapers.zamba import ZambaScraper

logger = logging.getLogger(__name__)

SCRAPER_REGISTRY: dict[str, type] = {
    "betplay": BetPlayScraper,
    "wplay": WplayScraper,
    "betano": BetanoScraper,
    "rushbet": RushBetScraper,
    "zamba": ZambaScraper,
    "codere": CodereScraper,
}


def build_scrapers(active: tuple[str, ...] | list[str]) -> list:
    scrapers = []
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
    "BetPlayScraper",
    "WplayScraper",
    "BetanoScraper",
    "RushBetScraper",
    "ZambaScraper",
    "CodereScraper",
    "SCRAPER_REGISTRY",
    "build_scrapers",
]
