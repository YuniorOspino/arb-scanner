from __future__ import annotations

from typing import Any

from scrapers.kambi import fetch_kambi_events
from scrapers.market_normalize import quotes_from_events


def scrape_rushbet() -> list[dict[str, Any]]:
    return fetch_kambi_events(operator="rsico", book_label="RushBet")


class RushBetScraper:
    bookmaker_name = "rushbet"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_rushbet()

    def fetch_odds(self):
        return quotes_from_events(self.bookmaker_name, self.scrape())
