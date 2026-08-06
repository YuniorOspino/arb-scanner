from __future__ import annotations

import logging
from typing import Any

import requests

from scrapers.event_names import normalize_event_name

logger = logging.getLogger(__name__)

NIOBE_BASE = "https://online-nio3-sportsbook-zamba.orenes.tech"
GQL_URL = f"{NIOBE_BASE}/offermanager/graphql"
API_KEY = "h640tsLa4fUxEucHUBr3v88mEd"
TENANT_ID = "031a9bbf-eaa5-4ae3-9668-8a01db9464a3"
TIMEOUT = 40.0
PAGE_SIZE = 100
MAX_PAGES = 8
MATCH_WINNER_KEY = 1
HOME_KEY = 20
DRAW_KEY = 21
AWAY_KEY = 22

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-api-key": API_KEY,
    "Origin": NIOBE_BASE,
    "Referer": f"{NIOBE_BASE}/",
}

CURRENT_OFFER_QUERY = """
query currentOffer($tenantId: Uuid!, $first: Int!, $after: String) {
  currentOffer(
    filter: {
      tenantId: $tenantId
      types: [Fixture]
      sportKeys: [1]
      status: Prematch
    }
    first: $first
    after: $after
  ) {
    totalCount
    pageInfo {
      endCursor
      hasNextPage
    }
    nodes {
      ... on Fixture {
        eventId
        eventName
        markets {
          marketKey
          selections {
            selectionKey
            selectionName
            price
          }
        }
      }
    }
  }
}
"""


def scrape_zamba() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = None
    try:
        for _ in range(MAX_PAGES):
            variables: dict[str, Any] = {
                "tenantId": TENANT_ID,
                "first": PAGE_SIZE,
            }
            if cursor:
                variables["after"] = cursor
            response = requests.post(
                GQL_URL,
                json={"query": CURRENT_OFFER_QUERY, "variables": variables},
                headers=BROWSER_HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                logger.warning("Zamba GraphQL errors: %s", payload["errors"][:2])
                break
            page_events = _parse_payload(payload)
            for event in page_events:
                key = event["event"]
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)

            offer = ((payload.get("data") or {}).get("currentOffer")) or {}
            page_info = offer.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("Zamba live scrape failed: %s", exc)
        return events

    if events:
        logger.info("Zamba scrape returned %d events", len(events))
    else:
        logger.warning("Zamba scrape produced no parseable events")
    return events


def _parse_payload(payload: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return events
    offer = data.get("currentOffer") or {}
    nodes = offer.get("nodes")
    if not isinstance(nodes, list):
        return events

    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("eventName")
        if not name:
            continue
        odds = _extract_1x2(node.get("markets") or [])
        if not odds:
            continue
        event_name = normalize_event_name(str(name).replace(" - ", " vs "))
        if not event_name:
            continue
        events.append({"event": event_name, "market": "1X2", "odds": odds})
    return events


def _extract_1x2(markets: Any) -> dict[str, float] | None:
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("marketKey") != MATCH_WINNER_KEY:
            continue
        selections = market.get("selections")
        if not isinstance(selections, list) or len(selections) < 3:
            continue
        mapped: dict[str, float] = {}
        ordered: list[float] = []
        for sel in selections:
            if not isinstance(sel, dict):
                continue
            try:
                price = float(sel.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 1.0:
                continue
            ordered.append(price)
            key = _selection_outcome(sel)
            if key is not None:
                mapped[key] = price
        if {"home", "draw", "away"} <= mapped.keys():
            return mapped
        if len(ordered) >= 3:
            return {"home": ordered[0], "draw": ordered[1], "away": ordered[2]}
    return None


def _selection_outcome(sel: dict[str, Any]) -> str | None:
    sk = sel.get("selectionKey")
    if sk == HOME_KEY:
        return "home"
    if sk == DRAW_KEY:
        return "draw"
    if sk == AWAY_KEY:
        return "away"
    name = str(sel.get("selectionName") or "").strip().lower()
    if name in {"draw", "empate", "x"}:
        return "draw"
    return None


class ZambaScraper:
    bookmaker_name = "zamba"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_zamba()

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
