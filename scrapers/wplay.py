from __future__ import annotations

import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from scrapers.event_names import normalize_event_name

logger = logging.getLogger(__name__)

WPLAY_URL = "https://apuestas.wplay.co/es/s/FOOT/Fútbol"
TIMEOUT = 25.0
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Referer": "https://www.wplay.co/",
}


def scrape_wplay() -> list[dict[str, Any]]:
    try:
        response = requests.get(WPLAY_URL, timeout=TIMEOUT, headers=BROWSER_HEADERS)
        response.raise_for_status()
        events = _parse_wplay_html(response.text)
        if events:
            logger.info("Wplay scrape returned %d events", len(events))
            return events
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("Wplay live scrape failed: %s", exc)
        return []
    logger.warning("Wplay scrape produced no parseable events")
    return []


def _parse_wplay_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for mkt in soup.select(".mkt, [class*='mkt']"):
        cells = mkt.select("td.seln, div.seln")
        if len(cells) < 3:
            continue

        home_name = ""
        away_name = ""
        home = draw = away = None
        for cell in cells:
            classes = " ".join(cell.get("class") or [])
            price_el = cell.select_one("span.price.dec")
            if price_el is None:
                continue
            try:
                price = float(price_el.get_text(strip=True))
            except ValueError:
                continue
            if price <= 1.0:
                continue

            name_el = cell.select_one(".seln-name")
            label = name_el.get_text(strip=True) if name_el else ""

            if "seln_sort-D" in classes or "seln_sort-H" in classes:
                # Playtech uses seln_sort-D for draw on 1X2
                if "seln_sort-D" in classes and draw is None:
                    draw = price
                continue

            # home/away: first named selection -> home, second -> away
            if home is None:
                home = price
                home_name = label
            elif away is None:
                away = price
                away_name = label

        if home is None or draw is None or away is None:
            continue
        # Keep only realistic 1X2 prices (filters wrong market types).
        if max(home, draw, away) > 40 or min(home, draw, away) < 1.01:
            continue
        if not home_name or not away_name:
            # try event anchor
            event_a = mkt.find_previous("a", href=re.compile(r"/es/e/"))
            if event_a:
                title = event_a.get_text(" ", strip=True)
                if " vs " in title.lower() or " v " in title.lower():
                    event_name = re.sub(r"\s+v(?:s)?\s+", " vs ", title, flags=re.I)
                else:
                    continue
            else:
                continue
        else:
            event_name = f"{home_name} vs {away_name}"

        event_name = normalize_event_name(event_name)
        if not event_name or event_name in seen:
            continue
        seen.add(event_name)
        events.append(
            {
                "event": event_name,
                "market": "1X2",
                "odds": {"home": home, "draw": draw, "away": away},
            }
        )
    return events


class WplayScraper:
    bookmaker_name = "wplay"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_wplay()

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
