from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BETPLAY_URL = "https://www.betplay.com.co/api/sport/football/fixtures"
TIMEOUT = 15.0


def scrape_betplay() -> list[dict[str, Any]]:
    try:
        response = requests.get(
            BETPLAY_URL,
            timeout=TIMEOUT,
            headers={"User-Agent": "arb-scanner/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        events = _parse_betplay_payload(payload)
        if events:
            logger.info("BetPlay scrape returned %d events", len(events))
            return events
    except (requests.RequestException, ValueError, TypeError, KeyError):
        logger.exception("BetPlay live scrape failed; using mock data")

    return _mock_betplay_events()


def _parse_betplay_payload(payload: Any) -> list[dict[str, Any]]:
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


def _mock_betplay_events() -> list[dict[str, Any]]:
    return [
        {
            "event": "EquipoA vs EquipoB",
            "market": "1X2",
            "odds": {"home": 2.0, "draw": 3.1, "away": 3.5},
        },
        {
            "event": "Colombia vs Brasil",
            "market": "1X2",
            "odds": {"home": 2.90, "draw": 3.30, "away": 2.50},
        },
    ]
