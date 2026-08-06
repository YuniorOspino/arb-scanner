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
        from scrapers.event_names import normalize_event_name
        from scrapers.market_normalize import build_event, classify_market, normalize_outcome_label

        for market in markets:
            if not isinstance(market, dict):
                continue
            mtype = str(market.get("type") or market.get("Type") or "")
            mname = str(market.get("name") or market.get("Name") or mtype)
            odds_raw = market.get("odds") or market.get("Odds") or market.get("selections")
            mapped: dict[str, float] = {}
            if isinstance(odds_raw, dict):
                for k, v in odds_raw.items():
                    try:
                        price = float(v)
                    except (TypeError, ValueError):
                        continue
                    if price <= 1.0:
                        continue
                    mapped[normalize_outcome_label(str(k))] = price
            elif isinstance(odds_raw, list):
                for sel in odds_raw:
                    if not isinstance(sel, dict):
                        continue
                    try:
                        price = float(sel.get("price") or sel.get("odds") or sel.get("Odds"))
                    except (TypeError, ValueError):
                        continue
                    if price <= 1.0:
                        continue
                    label = sel.get("name") or sel.get("Name") or sel.get("outcome") or ""
                    mapped[normalize_outcome_label(str(label))] = price
            market_id = classify_market(type_name=mname, type_id=mtype, label=mname, english_type=mtype)
            if not market_id:
                continue
            event_name = normalize_event_name(str(name))
            row = build_event(event_name, market_id, mapped)
            if row:
                events.append(row)
    return events


class CodereScraper:
    bookmaker_name = "codere"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_codere()

    def fetch_odds(self):
        from scrapers.market_normalize import quotes_from_events

        return quotes_from_events(self.bookmaker_name, self.scrape())
