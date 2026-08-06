from __future__ import annotations

import logging
from typing import Any

import requests

from scrapers.event_names import normalize_event_name

logger = logging.getLogger(__name__)

BETANO_URLS = (
    "https://www.betano.co/api/sport/futbol/proximas-12-horas",
    "https://www.betano.co/api/sport/futbol/proximas-24-horas",
    "https://www.betano.co/api/sport/futbol/proximas-48-horas",
    "https://www.betano.co/api/sport/futbol/hoy",
)
TIMEOUT = 20.0
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Referer": "https://www.betano.co/sport/futbol/",
    "Origin": "https://www.betano.co",
}


def scrape_betano() -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        # Discover league pages from sport root, then pull period feeds.
        root = requests.get(
            "https://www.betano.co/api/sport/futbol",
            timeout=TIMEOUT,
            headers=BROWSER_HEADERS,
        )
        urls = list(BETANO_URLS)
        if root.ok:
            data = root.json().get("data") if isinstance(root.json(), dict) else {}
            for league in (data or {}).get("topLeagues") or []:
                if not isinstance(league, dict):
                    continue
                path = league.get("url")
                if isinstance(path, str) and path.startswith("/"):
                    urls.append("https://www.betano.co/api" + path)

        for url in urls:
            try:
                response = requests.get(url, timeout=TIMEOUT, headers=BROWSER_HEADERS)
                if response.status_code != 200:
                    continue
                if "json" not in response.headers.get("content-type", ""):
                    continue
                for event in _parse_betano_payload(response.json()):
                    key = event["event"]
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(event)
            except (requests.RequestException, ValueError, TypeError, KeyError):
                continue

        if merged:
            logger.info("Betano scrape returned %d events", len(merged))
            return merged
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("Betano live scrape failed: %s", exc)
        return []
    logger.warning("Betano scrape produced no parseable events")
    return []


def _parse_betano_payload(payload: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return events

    blocks = data.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            raw_events = block.get("events")
            if not isinstance(raw_events, list):
                continue
            for item in raw_events:
                parsed = _parse_event(item)
                if parsed:
                    events.append(parsed)
        return events

    # Some league endpoints nest events differently.
    for key in ("events", "fixtures"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                parsed = _parse_event(item)
                if parsed:
                    events.append(parsed)
    return events


def _parse_event(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("shortName")
    markets = item.get("markets")
    if not name or not isinstance(markets, list):
        return None

    for market in markets:
        if not isinstance(market, dict):
            continue
        mtype = str(market.get("type") or "").upper()
        mname = str(market.get("name") or "").lower()
        if mtype not in {"MRES", "1X2", "MR", "MATCH_RESULT"} and "resultado" not in mname:
            continue
        odds = _selections_to_odds(market.get("selections"))
        if odds:
            event_name = normalize_event_name(str(name).replace(" - ", " vs "))
            if not event_name:
                return None
            return {"event": event_name, "market": "1X2", "odds": odds}
    return None


def _selections_to_odds(selections: Any) -> dict[str, float] | None:
    if not isinstance(selections, list):
        return None
    mapped: dict[str, float] = {}
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        label = str(sel.get("name") or "").upper().strip()
        key = None
        if label in {"1", "HOME", "LOCAL"}:
            key = "home"
        elif label in {"X", "DRAW", "EMPATE"}:
            key = "draw"
        elif label in {"2", "AWAY", "VISITANTE"}:
            key = "away"
        if key is None:
            continue
        try:
            price = float(sel.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        mapped[key] = price
    if {"home", "draw", "away"} <= mapped.keys():
        return mapped
    return None


class BetanoScraper:
    bookmaker_name = "betano"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_betano()

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
