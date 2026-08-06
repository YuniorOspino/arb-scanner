from __future__ import annotations

from typing import Any

from scrapers.kambi import fetch_kambi_events
from scrapers.market_normalize import quotes_from_events


def scrape_betplay() -> list[dict[str, Any]]:
    return fetch_kambi_events(operator="betplay", book_label="BetPlay")


class BetPlayScraper:
    bookmaker_name = "betplay"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_betplay()

    def fetch_odds(self):
        return quotes_from_events(self.bookmaker_name, self.scrape())
