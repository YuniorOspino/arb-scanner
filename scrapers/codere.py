from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Codere CO SPA: https://m.codere.com.co/deportesCol/
# Sports catalog endpoint used by the web app (csbgonline).
CODERE_SPORTS_URL = "https://m.codere.com.co/csbgonline/home/GetSports"
CODERE_SPORT_BY_HANDLE_URL = (
    "https://m.codere.com.co/csbgonline/home/GetSportBySportHandle"
)
TIMEOUT = 15.0
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
    "Referer": "https://m.codere.com.co/deportesCol/",
    "Origin": "https://m.codere.com.co",
    "X-Requested-With": "XMLHttpRequest",
}

_CODERE_JUSTIFICATION = (
    "Codere CO: endpoint real csbgonline/home/GetSports responde 200 con [] "
    "(catalogo vacio). GetSportBySportHandle responde 204. "
    "SportsMisc/api existe pero no expone controllers Game/Home (404). "
    "Quantum (madrid.pe.quantum-sports.net/dig-codere-*) responde 404. "
    "Sin feed publico de cuotas disponible en este momento."
)


def scrape_codere() -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    try:
        # Warm cookies like the SPA.
        session.get("https://m.codere.com.co/deportesCol/", timeout=TIMEOUT)
        response = session.get(CODERE_SPORTS_URL, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Codere scrape failed: %s | %s", exc, _CODERE_JUSTIFICATION)
        return []

    events = parse_codere_data(payload)
    if events:
        logger.info("Codere scrape returned %d events", len(events))
        return events

    # Confirm sport-by-handle is also empty/no-content.
    try:
        by_handle = session.get(
            CODERE_SPORT_BY_HANDLE_URL,
            params={"sportHandle": "soccer"},
            timeout=TIMEOUT,
        )
        logger.warning(
            "%s (GetSports=%r status_by_handle=%s)",
            _CODERE_JUSTIFICATION,
            payload,
            by_handle.status_code,
        )
    except requests.RequestException:
        logger.warning("%s", _CODERE_JUSTIFICATION)
    return []


def parse_codere_data(payload: Any) -> list[dict[str, Any]]:
    """Parse Codere sports payload if/when the catalog is non-empty."""
    events: list[dict[str, Any]] = []

    if isinstance(payload, list):
        raw_events = payload
    elif isinstance(payload, dict):
        raw_events = payload.get("events") or payload.get("Sports") or payload.get("sports") or []
    else:
        return events

    if not isinstance(raw_events, list):
        return events

    for item in raw_events:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("event") or item.get("EventName")
        markets = item.get("markets") or item.get("Markets") or []
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
