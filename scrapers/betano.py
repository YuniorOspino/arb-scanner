from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from scrapers.event_names import normalize_event_name
from scrapers.market_normalize import (
    build_event,
    classify_market,
    fmt_line,
    normalize_outcome_label,
    quotes_from_events,
    split_ou_selections,
)

logger = logging.getLogger(__name__)

BETANO_URLS = (
    "https://www.betano.co/api/sport/futbol/proximas-12-horas",
    "https://www.betano.co/api/sport/futbol/proximas-24-horas",
    "https://www.betano.co/api/sport/futbol/proximas-48-horas",
    "https://www.betano.co/api/sport/futbol/hoy",
)
TIMEOUT = 20.0
MAX_WORKERS = 12
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
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    try:
        session.get("https://www.betano.co/sport/futbol/", timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Betano warm-up failed: %s", exc)
        return []

    event_paths = _discover_event_paths(session)
    if not event_paths:
        logger.warning("Betano scrape produced no event paths")
        return []

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_event_markets, session, path): path for path in event_paths
        }
        for fut in as_completed(futures):
            try:
                batch = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Betano event failed: %s", exc)
                continue
            for row in batch:
                key = (row["event"], row["market"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

    logger.debug("Betano scrape returned %d market-rows from %d events", len(rows), len(event_paths))
    return rows


def _discover_event_paths(session: requests.Session) -> list[str]:
    urls = list(BETANO_URLS)
    try:
        root = session.get("https://www.betano.co/api/sport/futbol", timeout=TIMEOUT)
        if root.ok and "json" in root.headers.get("content-type", ""):
            data = root.json().get("data") if isinstance(root.json(), dict) else {}
            for league in (data or {}).get("topLeagues") or []:
                if not isinstance(league, dict):
                    continue
                path = league.get("url")
                if isinstance(path, str) and path.startswith("/"):
                    urls.append("https://www.betano.co/api" + path)
    except (requests.RequestException, ValueError, TypeError):
        pass

    paths: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code != 200:
                continue
            if "json" not in response.headers.get("content-type", ""):
                continue
            for path in _event_paths_from_payload(response.json()):
                if path in seen:
                    continue
                seen.add(path)
                paths.append(path)
        except (requests.RequestException, ValueError, TypeError, KeyError):
            continue
    return paths


def _event_paths_from_payload(payload: Any) -> list[str]:
    out: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return out

    def consider(item: Any) -> None:
        if not isinstance(item, dict):
            return
        url = item.get("url")
        if isinstance(url, str) and url.startswith("/cuotas-de-partido/"):
            out.append(url)

    blocks = data.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for item in block.get("events") or []:
                consider(item)
    for key in ("events", "fixtures"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                consider(item)
    return out


def _fetch_event_markets(session: requests.Session, path: str) -> list[dict[str, Any]]:
    url = "https://www.betano.co/api" + path
    # tab query expands full market set on Kaizen
    response = session.get(url, params={"tab": "all"}, timeout=TIMEOUT)
    if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
        response = session.get(url, timeout=TIMEOUT)
    if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
        return []
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    event = data.get("event") if isinstance(data.get("event"), dict) else None
    if not event:
        return []
    name = event.get("name") or event.get("shortName")
    if not name:
        return []
    event_name = normalize_event_name(str(name).replace(" - ", " vs "))
    if not event_name:
        return []
    home, away = _split_teams(str(name))
    markets = event.get("markets")
    if not isinstance(markets, list):
        return []
    rows: list[dict[str, Any]] = []
    for market in markets:
        rows.extend(_parse_market(event_name, market, home=home, away=away))
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
    mtype = str(market.get("type") or "")
    mname = str(market.get("name") or "")
    selections = market.get("selections")
    if not isinstance(selections, list) or len(selections) < 2:
        return []

    # Expand multi-line totals into one market per line.
    if _looks_like_totals(mtype, mname):
        return _expand_totals(event_name, mtype, mname, selections, home=home, away=away)

    line = market.get("handicap")
    try:
        line_f = float(line) if line not in (None, "", 0, 0.0) else None
    except (TypeError, ValueError):
        line_f = None

    market_id = classify_market(
        type_name=mname,
        type_id=mtype,
        label=mname,
        line=line_f,
        english_type=mtype,
        home=home,
        away=away,
    )
    if not market_id:
        return []

    # Team totals by type codes
    if mtype == "OUHG" and line_f is not None:
        market_id = f"TT_HOME_{fmt_line(line_f)}"
    elif mtype == "OUAG" and line_f is not None:
        market_id = f"TT_AWAY_{fmt_line(line_f)}"

    odds = _selections_to_odds(selections, market_id=market_id, home=home, away=away)
    row = build_event(event_name, market_id, odds or {})
    return [row] if row else []


def _looks_like_totals(mtype: str, mname: str) -> bool:
    blob = f"{mtype} {mname}".lower()
    return any(
        x in blob
        for x in (
            "hctg",
            "ouh1",
            "cnou",
            "tcou",
            "cou1",
            "1cou",
            "ouhg",
            "ouag",
            "más/menos",
            "mas/menos",
            "menos/más",
            "over/under",
            "goles totales",
        )
    )


def _expand_totals(
    event_name: str,
    mtype: str,
    mname: str,
    selections: list[dict[str, Any]],
    *,
    home: str | None,
    away: str | None,
) -> list[dict[str, Any]]:
    buckets = split_ou_selections(selections)
    rows: list[dict[str, Any]] = []
    base = classify_market(
        type_name=mname,
        type_id=mtype,
        label=mname,
        line=None,
        english_type=mtype,
        home=home,
        away=away,
    )
    # Force team totals by code
    if mtype == "OUHG":
        prefix = "TT_HOME_"
    elif mtype == "OUAG":
        prefix = "TT_AWAY_"
    elif mtype in {"CNOU", "COU1"} or "esquina" in mname.lower():
        prefix = "CORNERS_OU_"
    elif mtype in {"TCOU", "1COU"} or "tarjeta" in mname.lower():
        prefix = "CARDS_OU_"
    elif mtype == "OUH1" or "primer tiempo" in mname.lower():
        prefix = "OU_HT_"
    else:
        # strip trailing _ from classify without line
        prefix = "OU_"
        if base and base.startswith("OU_HT"):
            prefix = "OU_HT_"
        elif base and base.startswith("TT_"):
            prefix = base.rstrip("_0123456789.") + "_" if not base.endswith("_") else base
            if not prefix.endswith("_"):
                prefix = base.rsplit("_", 1)[0] + "_" if "_" in base else base + "_"

    for line_s, odds in buckets.items():
        market_id = f"{prefix}{line_s}"
        row = build_event(event_name, market_id, odds)
        if row:
            rows.append(row)
    return rows


def _selections_to_odds(
    selections: Any,
    *,
    market_id: str,
    home: str | None,
    away: str | None,
) -> dict[str, float] | None:
    if not isinstance(selections, list):
        return None
    mapped: dict[str, float] = {}
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        try:
            price = float(sel.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        label = str(sel.get("name") or "").strip()
        key = _map_selection(label, market_id=market_id, home=home, away=away)
        if key:
            mapped[key] = price
    return mapped or None


def _map_selection(
    label: str,
    *,
    market_id: str,
    home: str | None,
    away: str | None,
) -> str | None:
    norm = normalize_outcome_label(label)
    if market_id in {"1X2", "HT_1X2", "HT2_1X2"}:
        if norm in {"home", "draw", "away"}:
            return norm
        if home and label.lower() == home.lower():
            return "home"
        if away and label.lower() == away.lower():
            return "away"
        if "empate" in label.lower():
            return "draw"
        return None
    if market_id == "DC" or market_id.startswith("DC"):
        if norm in {"1x", "12", "x2"}:
            return norm
        low = label.lower()
        if home and away:
            if home.lower() in low and "empate" in low:
                return "1x"
            if away.lower() in low and "empate" in low:
                return "x2"
            if home.lower() in low and away.lower() in low:
                return "12"
        if label.upper() in {"1X", "X1"}:
            return "1x"
        if label.upper() in {"X2", "2X"}:
            return "x2"
        if label.upper() == "12":
            return "12"
        return None
    if market_id == "DNB" or market_id.startswith("AH") or market_id.startswith("EH"):
        if home and label.lower() == home.lower():
            return "home"
        if away and label.lower() == away.lower():
            return "away"
        if norm in {"home", "away", "draw"}:
            return norm
        return None
    if market_id in {"BTTS", "BTTS_HT", "BTTS_HT2"}:
        return norm if norm in {"yes", "no"} else None
    if market_id in {"CS", "CS_HT", "HTFT"}:
        return norm
    if market_id.startswith(("OU", "AOU", "CORNERS", "CARDS", "TT_")):
        return norm if norm in {"over", "under"} else None
    return norm or None


class BetanoScraper:
    bookmaker_name = "betano"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_betano()

    def fetch_odds(self):
        return quotes_from_events(self.bookmaker_name, self.scrape())
