"""Shared Kambi offering-api client used by Colombian sportsbooks."""

from __future__ import annotations

import logging
from typing import Any

import requests

from scrapers.event_names import normalize_event_name

logger = logging.getLogger(__name__)

TIMEOUT = 20.0
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}


def kambi_listview_url(operator: str, sport: str = "football") -> str:
    return (
        f"https://us.offering-api.kambicdn.com/offering/v2018/{operator}/"
        f"listView/{sport}.json"
        f"?lang=es_ES&market=CO&client_id=2&channel_id=1"
    )


def fetch_kambi_events(operator: str, book_label: str) -> list[dict[str, Any]]:
    url = kambi_listview_url(operator)
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s Kambi fetch failed: %s", book_label, exc)
        return []

    events = parse_kambi_payload(payload)
    logger.info("%s Kambi scrape returned %d events", book_label, len(events))
    return events


def parse_kambi_payload(payload: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list):
        return events

    for item in raw_events:
        if not isinstance(item, dict):
            continue
        event = item.get("event")
        if not isinstance(event, dict):
            continue
        home = event.get("homeName")
        away = event.get("awayName")
        name = None
        if home and away:
            name = f"{home} vs {away}"
        else:
            name = event.get("name") or event.get("englishName")
        if not name:
            continue
        name = normalize_event_name(str(name))
        if not name:
            continue

        odds = _extract_1x2(item.get("betOffers") or [])
        if not odds:
            continue
        events.append({"event": name, "market": "1X2", "odds": odds})
    return events


def _extract_1x2(bet_offers: Any) -> dict[str, float] | None:
    if not isinstance(bet_offers, list):
        return None

    for offer in bet_offers:
        if not isinstance(offer, dict):
            continue
        outcomes = offer.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) < 3:
            continue

        mapped: dict[str, float] = {}
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            otype = str(outcome.get("type") or "").upper()
            label = str(outcome.get("label") or "").upper()
            key = None
            if otype in {"OT_ONE", "OT_HOME"} or label in {"1", "HOME"}:
                key = "home"
            elif otype in {"OT_CROSS", "OT_DRAW"} or label in {"X", "DRAW"}:
                key = "draw"
            elif otype in {"OT_TWO", "OT_AWAY"} or label in {"2", "AWAY"}:
                key = "away"
            if key is None:
                continue
            decimal = _kambi_odds_to_decimal(outcome.get("odds"))
            if decimal is None:
                continue
            mapped[key] = decimal

        if {"home", "draw", "away"} <= mapped.keys():
            if min(mapped.values()) > 1.0:
                return mapped
    return None


def _kambi_odds_to_decimal(raw: Any) -> float | None:
    """Kambi serves odds as integer thousandths (1490 -> 1.49)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 50:
        value = value / 1000.0
    if value <= 1.0:
        return None
    return round(value, 3)
