"""Shared Kambi offering-api client used by Colombian sportsbooks.

Discovers all betOffers per event (not only listView 1X2) and normalizes
them to the scanner contract: {event, market, odds}.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from scrapers.event_names import normalize_event_name
from scrapers.market_normalize import build_event, classify_market, normalize_outcome_label

logger = logging.getLogger(__name__)

TIMEOUT = 20.0
MAX_WORKERS = 16
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

_OT_MAP = {
    "OT_ONE": "home",
    "OT_HOME": "home",
    "OT_CROSS": "draw",
    "OT_DRAW": "draw",
    "OT_TWO": "away",
    "OT_AWAY": "away",
    "OT_OVER": "over",
    "OT_UNDER": "under",
    "OT_YES": "yes",
    "OT_NO": "no",
    "OT_ONE_OR_CROSS": "1x",
    "OT_ONE_OR_TWO": "12",
    "OT_CROSS_OR_TWO": "x2",
}


def kambi_listview_url(operator: str, sport: str = "football") -> str:
    return (
        f"https://us.offering-api.kambicdn.com/offering/v2018/{operator}/"
        f"listView/{sport}.json"
        f"?lang=es_ES&market=CO&client_id=2&channel_id=1"
    )


def kambi_event_url(operator: str, event_id: int | str) -> str:
    return (
        f"https://us.offering-api.kambicdn.com/offering/v2018/{operator}/"
        f"betoffer/event/{event_id}.json?lang=es_ES&market=CO"
    )


def fetch_kambi_events(operator: str, book_label: str) -> list[dict[str, Any]]:
    url = kambi_listview_url(operator)
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s Kambi listView failed: %s", book_label, exc)
        return []

    meta = _list_event_meta(payload)
    if not meta:
        logger.warning("%s Kambi listView empty", book_label)
        return []

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_event_offers, operator, item): item for item in meta
        }
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                offers = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s event %s failed: %s", book_label, item.get("id"), exc)
                continue
            rows.extend(_parse_offers(item["name"], offers, home=item.get("home"), away=item.get("away")))

    logger.info(
        "%s Kambi scrape returned %d market-rows from %d events",
        book_label,
        len(rows),
        len(meta),
    )
    return rows


def _list_event_meta(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list):
        return out
    seen: set[Any] = set()
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        event = item.get("event")
        if not isinstance(event, dict):
            continue
        eid = event.get("id")
        if eid is None or eid in seen:
            continue
        home = event.get("homeName")
        away = event.get("awayName")
        if home and away:
            name = f"{home} vs {away}"
        else:
            name = event.get("name") or event.get("englishName")
        name = normalize_event_name(str(name or ""))
        if not name:
            continue
        seen.add(eid)
        out.append({"id": eid, "name": name, "home": home, "away": away})
    return out


def _fetch_event_offers(operator: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    url = kambi_event_url(operator, item["id"])
    response = requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    offers = payload.get("betOffers") if isinstance(payload, dict) else None
    return offers if isinstance(offers, list) else []


def _parse_offers(
    event_name: str,
    offers: list[dict[str, Any]],
    *,
    home: str | None = None,
    away: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        row = _offer_to_row(event_name, offer, home=home, away=away)
        if row:
            rows.append(row)
    return rows


def _offer_to_row(
    event_name: str,
    offer: dict[str, Any],
    *,
    home: str | None,
    away: str | None,
) -> dict[str, Any] | None:
    bot = offer.get("betOfferType") or {}
    crit = offer.get("criterion") or {}
    type_name = str(bot.get("name") or "")
    english_type = str(bot.get("englishName") or "")
    label = str(crit.get("label") or crit.get("englishLabel") or "")
    type_id = bot.get("id")

    line = _offer_line(offer)
    market_id = classify_market(
        type_name=type_name,
        type_id=type_id,
        label=label,
        line=line,
        english_type=english_type,
        home=home,
        away=away,
    )
    if not market_id:
        return None

    # Refine team totals using home/away names from the event.
    if market_id.startswith("TT_") or market_id == "TT":
        side = _team_side_from_label(label, home, away)
        if side and line is not None:
            from scrapers.market_normalize import fmt_line

            market_id = f"TT_{side}_{fmt_line(line)}"
        elif side:
            market_id = f"TT_{side}"

    # Asian handicap: pin line to home perspective when available.
    if market_id.startswith("AH_") or market_id == "AH":
        home_line = _asian_home_line(offer, home)
        if home_line is not None:
            from scrapers.market_normalize import fmt_line

            market_id = f"AH_{fmt_line(home_line)}"

    if market_id.startswith("EH_") or market_id == "EH":
        home_line = _european_home_line(offer)
        if home_line is not None:
            from scrapers.market_normalize import fmt_line

            market_id = f"EH_{fmt_line(home_line)}"

    odds = _outcomes_to_odds(offer.get("outcomes") or [], market_id=market_id)
    return build_event(event_name, market_id, odds or {})


def _offer_line(offer: dict[str, Any]) -> float | None:
    # Prefer outcome line (common for OU/AH), else offer-level.
    for outcome in offer.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        raw = outcome.get("line")
        if raw is None:
            continue
        return _kambi_line_to_float(raw)
    return _kambi_line_to_float(offer.get("line"))


def _kambi_line_to_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Kambi lines are thousandths: 2500 -> 2.5, -1500 -> -1.5
    if abs(value) >= 50:
        value = value / 1000.0
    return round(value, 3)


def _asian_home_line(offer: dict[str, Any], home: str | None) -> float | None:
    for outcome in offer.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        otype = str(outcome.get("type") or "").upper()
        label = str(outcome.get("label") or "")
        if otype in {"OT_ONE", "OT_HOME"}:
            return _kambi_line_to_float(outcome.get("line"))
        if home and label and label.lower() == str(home).lower():
            return _kambi_line_to_float(outcome.get("line"))
    return _offer_line(offer)


def _european_home_line(offer: dict[str, Any]) -> float | None:
    for outcome in offer.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        if str(outcome.get("type") or "").upper() in {"OT_ONE", "OT_HOME"}:
            return _kambi_line_to_float(outcome.get("line"))
    return _offer_line(offer)


def _team_side_from_label(label: str, home: str | None, away: str | None) -> str | None:
    text = label.lower()
    if home and str(home).lower() in text:
        return "HOME"
    if away and str(away).lower() in text:
        return "AWAY"
    if "local" in text or "home" in text:
        return "HOME"
    if "visitante" in text or "away" in text:
        return "AWAY"
    return None


def _outcomes_to_odds(outcomes: Any, *, market_id: str) -> dict[str, float] | None:
    if not isinstance(outcomes, list):
        return None
    mapped: dict[str, float] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        decimal = _kambi_odds_to_decimal(outcome.get("odds"))
        if decimal is None:
            continue
        key = _outcome_key(outcome, market_id=market_id)
        if not key:
            continue
        mapped[key] = decimal
    return mapped or None


def _outcome_key(outcome: dict[str, Any], *, market_id: str) -> str | None:
    otype = str(outcome.get("type") or "").upper()
    if otype in _OT_MAP:
        return _OT_MAP[otype]
    label = str(outcome.get("label") or "").strip()
    if market_id == "CS":
        return normalize_outcome_label(label)
    if market_id == "HTFT":
        return normalize_outcome_label(label)
    if market_id in {"1X2", "HT_1X2", "HT2_1X2", "DNB", "EH"} or market_id.startswith("EH_"):
        norm = normalize_outcome_label(label)
        if norm in {"home", "draw", "away", "1x", "12", "x2"}:
            return norm
    if market_id in {"DC"}:
        norm = normalize_outcome_label(label)
        if norm in {"1x", "12", "x2"}:
            return norm
    if market_id.startswith("OU") or market_id.startswith("AOU") or market_id.startswith("CORNERS") or market_id.startswith("CARDS") or market_id.startswith("TT_"):
        norm = normalize_outcome_label(label)
        if norm in {"over", "under"}:
            return norm
        # Spanish "Más de 2.5" etc.
        low = label.lower()
        if "más" in low or "mas" in low or low.startswith("over"):
            return "over"
        if "menos" in low or low.startswith("under"):
            return "under"
    if market_id == "BTTS":
        return normalize_outcome_label(label)
    if market_id.startswith("AH"):
        # home/away only
        if otype in {"OT_ONE", "OT_HOME"}:
            return "home"
        if otype in {"OT_TWO", "OT_AWAY"}:
            return "away"
        return normalize_outcome_label(label) if normalize_outcome_label(label) in {"home", "away"} else None
    # fallback discovered label
    return normalize_outcome_label(label) or None


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


# Kept for any residual callers / tests.
def parse_kambi_payload(payload: Any) -> list[dict[str, Any]]:
    """Parse listView-only 1X2 (legacy). Prefer fetch_kambi_events for full markets."""
    events: list[dict[str, Any]] = []
    for item in _list_event_meta(payload):
        # listView items are not re-fetched here; use empty.
        _ = item
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
        name = f"{home} vs {away}" if home and away else (event.get("name") or "")
        name = normalize_event_name(str(name))
        if not name:
            continue
        events.extend(_parse_offers(name, item.get("betOffers") or [], home=home, away=away))
    return events
