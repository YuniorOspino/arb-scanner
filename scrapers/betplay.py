from __future__ import annotations

from typing import Any

from scrapers.kambi import fetch_kambi_events


def scrape_betplay() -> list[dict[str, Any]]:
    return fetch_kambi_events(operator="betplay", book_label="BetPlay")


class BetPlayScraper:
    bookmaker_name = "betplay"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_betplay()

    def fetch_odds(self):
        from core.models import OddsQuote

        quotes = []
        for event in self.scrape():
            for outcome, odd in event["odds"].items():
                quotes.append(
                    OddsQuote(
                        bookmaker=self.bookmaker_name,
                        outcome=outcome,
                        odds=float(odd),
                        market_id=event.get("market", "1X2"),
                        event_name=event["event"],
                    )
                )
        return quotes
