from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

WPLAY_URL = "https://www.wplay.co/api/sport/football/fixtures"
TIMEOUT = 15.0


def scrape_wplay() -> list[dict[str, Any]]:
    try:
        response = requests.get(
            WPLAY_URL,
            timeout=TIMEOUT,
            headers={"User-Agent": "arb-scanner/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        events = _parse_wplay_payload(payload)
        if events:
            logger.info("Wplay scrape returned %d events", len(events))
            return events
    except (requests.RequestException, ValueError, TypeError, KeyError):
        logger.exception("Wplay live scrape failed; using mock data")

    return _mock_wplay_events()


def _parse_wplay_payload(payload: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list):
        return events

    for item in raw_events:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("event")
        markets = item.get("markets") or []
        if not name or not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            if str(market.get("type", "")).upper() not in {"1X2", "MATCH_RESULT", "MR"}:
                continue
            odds = market.get("odds") or {}
            if not isinstance(odds, dict):
                continue
            home = odds.get("home") or odds.get("1")
            draw = odds.get("draw") or odds.get("X")
            away = odds.get("away") or odds.get("2")
            try:
                home_f, draw_f, away_f = float(home), float(draw), float(away)
            except (TypeError, ValueError):
                continue
            if min(home_f, draw_f, away_f) <= 1.0:
                continue
            events.append(
                {
                    "event": str(name),
                    "market": "1X2",
                    "odds": {"home": home_f, "draw": draw_f, "away": away_f},
                }
            )
    return events


def _mock_wplay_events() -> list[dict[str, Any]]:
    return [
        {
            "event": "EquipoA vs EquipoB",
            "market": "1X2",
            "odds": {"home": 1.9, "draw": 3.4, "away": 3.2},
        },
        {
            "event": "Colombia vs Brasil",
            "market": "1X2",
            "odds": {"home": 2.95, "draw": 3.35, "away": 2.45},
        },
    ]


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
