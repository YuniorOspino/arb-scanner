from __future__ import annotations

import logging
import re
from typing import Any

import requests

from scrapers.event_names import normalize_event_name
from scrapers.market_normalize import (
    build_event,
    classify_market,
    extract_line,
    fmt_line,
    normalize_outcome_label,
    quotes_from_events,
)

logger = logging.getLogger(__name__)

NIOBE_BASE = "https://online-nio3-sportsbook-zamba.orenes.tech"
GQL_URL = f"{NIOBE_BASE}/offermanager/graphql"
API_KEY = "h640tsLa4fUxEucHUBr3v88mEd"
TENANT_ID = "031a9bbf-eaa5-4ae3-9668-8a01db9464a3"
TIMEOUT = 40.0
PAGE_SIZE = 100
MAX_PAGES = 8

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
          marketName
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
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
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
            for row in _parse_payload(payload):
                key = (row["event"], row["market"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

            offer = ((payload.get("data") or {}).get("currentOffer")) or {}
            page_info = offer.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("Zamba live scrape failed: %s", exc)
        return rows

    if rows:
        logger.debug("Zamba scrape returned %d market-rows", len(rows))
    else:
        logger.warning("Zamba scrape produced no parseable events")
    return rows


def _parse_payload(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return rows
    offer = data.get("currentOffer") or {}
    nodes = offer.get("nodes")
    if not isinstance(nodes, list):
        return rows

    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("eventName")
        if not name:
            continue
        event_name = normalize_event_name(str(name).replace(" - ", " vs "))
        if not event_name:
            continue
        home, away = _split_teams(str(name))
        for market in node.get("markets") or []:
            parsed = _parse_market(event_name, market, home=home, away=away)
            rows.extend(parsed)
    return rows


def _split_teams(name: str) -> tuple[str | None, str | None]:
    for sep in (" - ", " vs ", " v "):
        if sep in name:
            a, b = name.split(sep, 1)
            return a.strip(), b.strip()
    return None, None


def _parse_market(
    event_name: str,
    market: Any,
    *,
    home: str | None,
    away: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(market, dict):
        return []
    mname = str(market.get("marketName") or "")
    selections = market.get("selections")
    if not mname or not isinstance(selections, list) or len(selections) < 2:
        return []

    line = extract_line(mname)
    # For "+/- 0.5 Total Goals" style, also peek selection names
    if line is None:
        for sel in selections:
            if isinstance(sel, dict):
                line = extract_line(str(sel.get("selectionName") or ""))
                if line is not None:
                    line = abs(line)
                    break

    market_id = classify_market(
        type_name=mname,
        type_id=market.get("marketKey"),
        label=mname,
        line=abs(line) if line is not None else None,
        english_type=mname,
        home=home,
        away=away,
    )
    if not market_id:
        return []

    # Refine team totals when market name embeds team
    if home and home.lower() in mname.lower() and ("+/-" in mname or "goals" in mname.lower()):
        if line is not None:
            market_id = f"TT_HOME_{fmt_line(abs(line))}"
    elif away and away.lower() in mname.lower() and ("+/-" in mname or "goals" in mname.lower()):
        if line is not None:
            market_id = f"TT_AWAY_{fmt_line(abs(line))}"

    # Corners from name
    if "corner" in mname.lower() and line is not None:
        market_id = f"CORNERS_OU_{fmt_line(abs(line))}"

    # European handicap Handicap 0:1
    m = re.search(r"handicap\s+(\d+)\s*:\s*(\d+)", mname.lower())
    if m:
        eh = int(m.group(1)) - int(m.group(2))
        market_id = f"EH_{fmt_line(eh)}"

    odds = _selections_to_odds(selections, market_id=market_id, home=home, away=away)
    row = build_event(event_name, market_id, odds or {})
    return [row] if row else []


def _selections_to_odds(
    selections: list[Any],
    *,
    market_id: str,
    home: str | None,
    away: str | None,
) -> dict[str, float] | None:
    mapped: dict[str, float] = {}
    ordered: list[tuple[str, float]] = []
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        try:
            price = float(sel.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        label = str(sel.get("selectionName") or "").strip()
        key = _map_selection(label, market_id=market_id, home=home, away=away, sk=sel.get("selectionKey"))
        if key:
            mapped[key] = price
            ordered.append((key, price))
    # Fallback positional for 1X2 if names are team names
    if market_id == "1X2" and not ({"home", "draw", "away"} <= mapped.keys()):
        prices = [p for _, p in ordered] if ordered else []
        # rebuild from selection order home/draw/away convention
        vals = []
        for sel in selections:
            if not isinstance(sel, dict):
                continue
            try:
                price = float(sel.get("price"))
            except (TypeError, ValueError):
                continue
            if price > 1.0:
                vals.append(price)
        if len(vals) >= 3:
            return {"home": vals[0], "draw": vals[1], "away": vals[2]}
    return mapped or None


def _map_selection(
    label: str,
    *,
    market_id: str,
    home: str | None,
    away: str | None,
    sk: Any,
) -> str | None:
    # Known Niobe keys for match winner
    if sk == 20:
        return "home"
    if sk == 21:
        return "draw"
    if sk == 22:
        return "away"
    if sk == 27:
        return "1x"
    if sk == 28:
        return "12"
    if sk == 29:
        return "x2"

    norm = normalize_outcome_label(label)
    low = label.lower().strip()

    if market_id in {"1X2", "HT_1X2", "HT2_1X2", "DNB"} or market_id.startswith("EH_"):
        if home and low == home.lower():
            return "home"
        if away and low == away.lower():
            return "away"
        if norm in {"home", "draw", "away"}:
            return norm
        if low in {"x", "draw", "empate"}:
            return "draw"
        return None

    if market_id in {"DC", "DC_HT"}:
        return norm if norm in {"1x", "12", "x2"} else None

    if market_id.startswith(("OU", "AOU", "CORNERS", "CARDS", "TT_")):
        if low.startswith("+") or norm == "over":
            return "over"
        if low.startswith("-") or norm == "under":
            return "under"
        return None

    if market_id.startswith("AH"):
        if home and low == home.lower():
            return "home"
        if away and low == away.lower():
            return "away"
        return None

    if market_id.startswith("BTTS"):
        return norm if norm in {"yes", "no"} else None

    if market_id in {"CS", "CS_HT", "HTFT"}:
        return norm

    return norm or None


class ZambaScraper:
    bookmaker_name = "zamba"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_zamba()

    def fetch_odds(self):
        return quotes_from_events(self.bookmaker_name, self.scrape())
