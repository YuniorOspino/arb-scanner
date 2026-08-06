from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

CODERE_URL = "https://www.codere.com.co/api/sport/football/fixtures"
TIMEOUT = 10.0


def fetch_codere_quotes() -> dict[str, Any]:
    try:
        response = requests.get(
            CODERE_URL,
            timeout=TIMEOUT,
            headers={"User-Agent": "arb-scanner/1.0"},
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            logger.warning(
                "Codere devolvio respuesta vacia o invalida, se continua sin datos."
            )
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "Codere devolvio JSON no-objeto, se continua sin datos."
            )
            return {}
        return data
    except requests.RequestException as e:
        logger.warning("Codere API error: %s", e)
        return {}


def scrape_codere() -> list[dict[str, Any]]:
    data = fetch_codere_quotes()
    if not data:
        return []
    events = parse_codere_data(data)
    logger.info("Codere scrape returned %d events", len(events))
    return events


def parse_codere_data(payload: Any) -> list[dict[str, Any]]:
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


class CodereScraper:
    bookmaker_name = "codere"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_codere()

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
