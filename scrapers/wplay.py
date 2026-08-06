from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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

WPLAY_URL = "https://apuestas.wplay.co/es/s/FOOT/Fútbol"
TIMEOUT = 25.0
MAX_EVENTS = 80
MAX_WORKERS = 10
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Referer": "https://www.wplay.co/",
}


def scrape_wplay() -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    try:
        response = session.get(WPLAY_URL, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Wplay list failed: %s", exc)
        return []

    event_urls = _event_urls(response.text)
    if not event_urls:
        # Fallback: parse list page 1X2-only style blocks
        rows = _parse_list_page(response.text)
        logger.info("Wplay list-only scrape returned %d market-rows", len(rows))
        return rows

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_event, session, url): url for url in event_urls[:MAX_EVENTS]
        }
        for fut in as_completed(futures):
            try:
                batch = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Wplay event failed: %s", exc)
                continue
            for row in batch:
                key = (row["event"], row["market"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

    logger.info(
        "Wplay scrape returned %d market-rows from %d events",
        len(rows),
        min(len(event_urls), MAX_EVENTS),
    )
    return rows


def _event_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/es/e/']"):
        href = a.get("href") or ""
        if not href or href in seen:
            continue
        seen.add(href)
        urls.append(urljoin("https://apuestas.wplay.co", href))
    return urls


def _fetch_event(session: requests.Session, url: str) -> list[dict[str, Any]]:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return _parse_event_html(response.text)


def _parse_event_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    event_name, home, away = _event_identity(soup)
    if not event_name:
        return []

    rows: list[dict[str, Any]] = []
    for name_el in soup.select(".mkt-name"):
        mname = name_el.get_text(" ", strip=True)
        if not mname:
            continue
        container = _market_container(name_el)
        if container is None:
            continue
        selections = _read_selections(container)
        if len(selections) < 2:
            continue
        rows.extend(
            _rows_from_market(event_name, mname, selections, home=home, away=away)
        )
    return rows


def _event_identity(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    title = None
    for sel in ("h1", ".ev-name", ".event-name", "title"):
        el = soup.select_one(sel)
        if el:
            title = el.get_text(" ", strip=True)
            if title:
                break
    if not title:
        return None, None, None
    title = re.sub(r"\s+\|.+$", "", title)
    title = re.sub(r"\s+v(?:s)?\s+", " vs ", title, flags=re.I)
    title = title.replace(" - ", " vs ")
    event_name = normalize_event_name(title)
    home = away = None
    if event_name and " vs " in event_name:
        home, away = event_name.split(" vs ", 1)
    return event_name, home, away


def _market_container(name_el):
    container = name_el
    for _ in range(10):
        if container is None:
            return None
        classes = " ".join(container.get("class") or [])
        if "mkt" in classes and container.select("span.price.dec"):
            return container
        container = container.parent
    return None


def _read_selections(container) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cell in container.select(".seln"):
        price_el = cell.select_one("span.price.dec")
        if price_el is None:
            continue
        try:
            price = float(price_el.get_text(strip=True))
        except ValueError:
            continue
        if price <= 1.0:
            continue
        name_el = cell.select_one(".seln-name")
        hcap_el = cell.select_one(".seln-hcap")
        btn = cell.select_one("button[title]")
        label = name_el.get_text(" ", strip=True) if name_el else ""
        hcap = hcap_el.get_text(" ", strip=True) if hcap_el else ""
        title = btn.get("title") if btn else ""
        classes = " ".join(cell.get("class") or [])
        out.append(
            {
                "name": label,
                "hcap": hcap,
                "title": title or "",
                "price": price,
                "classes": classes,
            }
        )
    return out


def _rows_from_market(
    event_name: str,
    mname: str,
    selections: list[dict[str, Any]],
    *,
    home: str | None,
    away: str | None,
) -> list[dict[str, Any]]:
    blob = mname.lower()
    if any(
        x in blob
        for x in (
            "anotador",
            "jugador",
            "asistenc",
            "atajad",
            "faltas",
            "tiros a puerta",
            "disparos",
            "fueras de juego",
            "entradas del jugador",
        )
    ):
        return []

    # Multi-line totals: group by hcap
    if any(
        x in blob
        for x in (
            "más/menos",
            "mas/menos",
            "total goles",
            "total de goles",
            "total goles asiatico",
            "total de tarjetas",
            "tiros de esquina",
        )
    ) and any(s.get("hcap") for s in selections):
        return _expand_ou(event_name, mname, selections, home=home, away=away)

    line = None
    for s in selections:
        if s.get("hcap"):
            # take first numeric token; asian may be "-0.5 / -1"
            m = re.search(r"[+-]?\d+(?:[.,]\d+)?", s["hcap"])
            if m:
                try:
                    line = float(m.group(0).replace(",", "."))
                except ValueError:
                    line = None
                break
    if line is None:
        line = extract_line(mname)

    market_id = classify_market(
        type_name=mname,
        label=mname,
        line=abs(line) if line is not None and ("más" in blob or "mas" in blob or "total" in blob) else line,
        english_type=mname,
        home=home,
        away=away,
    )
    if not market_id:
        return []

    # Explicit renames for Wplay Spanish titles
    if "empate no accion" in blob or "empate no acción" in blob:
        market_id = "DNB"
    if "resultado tiempo completo" in blob or mname.lower() == "1x2":
        market_id = "1X2"
    if "1ra mitad/tiempo completo" in blob or "1ra mitad / tiempo completo" in blob:
        market_id = "HTFT"
    if "1ra mitad resultado" in blob:
        market_id = "HT_1X2"
    if "marcador correcto" in blob:
        market_id = "CS"
    if "handicap asi" in blob and line is not None:
        market_id = f"AH_{fmt_line(line)}"
    if "handicap resultado" in blob and line is not None:
        market_id = f"EH_{fmt_line(line)}"

    odds = _map_odds(selections, market_id=market_id, home=home, away=away)
    row = build_event(event_name, market_id, odds or {})
    return [row] if row else []


def _expand_ou(
    event_name: str,
    mname: str,
    selections: list[dict[str, Any]],
    *,
    home: str | None,
    away: str | None,
) -> list[dict[str, Any]]:
    blob = mname.lower()
    if "tarjeta" in blob:
        prefix = "CARDS_OU_"
    elif "esquina" in blob:
        # 3-way corners include Exacto — keep only over/under pairs
        prefix = "CORNERS_OU_"
    elif "1ra mitad" in blob or "primer" in blob:
        prefix = "OU_HT_"
    elif home and home.lower() in blob:
        prefix = "TT_HOME_"
    elif away and away.lower() in blob:
        prefix = "TT_AWAY_"
    elif "asiatico" in blob or "asiático" in blob:
        prefix = "AOU_"
    else:
        prefix = "OU_"

    buckets: dict[str, dict[str, float]] = {}
    for sel in selections:
        hcap = str(sel.get("hcap") or "")
        m = re.search(r"\d+(?:[.,]\d+)?", hcap)
        if not m:
            continue
        line_s = fmt_line(float(m.group(0).replace(",", ".")))
        name = str(sel.get("name") or "").lower()
        title = str(sel.get("title") or "").lower()
        if "más" in name or "mas" in name or "más" in title or name.startswith("over"):
            outcome = "over"
        elif "menos" in name or "menos" in title or name.startswith("under"):
            outcome = "under"
        else:
            continue
        buckets.setdefault(line_s, {})[outcome] = float(sel["price"])

    rows: list[dict[str, Any]] = []
    for line_s, odds in buckets.items():
        row = build_event(event_name, f"{prefix}{line_s}", odds)
        if row:
            rows.append(row)
    return rows


def _map_odds(
    selections: list[dict[str, Any]],
    *,
    market_id: str,
    home: str | None,
    away: str | None,
) -> dict[str, float] | None:
    mapped: dict[str, float] = {}
    for sel in selections:
        label = str(sel.get("name") or "")
        title = str(sel.get("title") or "")
        classes = str(sel.get("classes") or "")
        price = float(sel["price"])
        key = None
        if "seln_sort-D" in classes:
            key = "draw"
        else:
            norm = normalize_outcome_label(label)
            if market_id in {"1X2", "HT_1X2", "HT2_1X2"}:
                if home and label.lower() == home.lower():
                    key = "home"
                elif away and label.lower() == away.lower():
                    key = "away"
                elif norm in {"home", "draw", "away"}:
                    key = norm
                elif "empate" in label.lower():
                    key = "draw"
            elif market_id == "DC":
                key = norm if norm in {"1x", "12", "x2"} else None
                if key is None:
                    low = (title or label).lower()
                    if home and away:
                        if home.lower() in low and "empate" in low:
                            key = "1x"
                        elif away.lower() in low and "empate" in low:
                            key = "x2"
                        elif home.lower() in low and away.lower() in low:
                            key = "12"
            elif market_id == "DNB" or market_id.startswith("AH") or market_id.startswith("EH"):
                if home and (label.lower() == home.lower() or home.lower() in title.lower()):
                    key = "home"
                elif away and (label.lower() == away.lower() or away.lower() in title.lower()):
                    key = "away"
                elif norm in {"home", "away", "draw"}:
                    key = norm
            elif market_id.startswith("BTTS"):
                key = norm if norm in {"yes", "no"} else None
                if key is None and label.lower() in {"si", "sí"}:
                    key = "yes"
            elif market_id in {"CS", "HTFT"}:
                key = normalize_outcome_label(title or label)
            else:
                key = norm
        if key:
            mapped[key] = price
    return mapped or None


def _parse_list_page(html: str) -> list[dict[str, Any]]:
    """Legacy list-page parser (mostly 1X2)."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mkt in soup.select(".mkt.mkt_content, .mkt_content"):
        cells = mkt.select("td.seln, div.seln, span.seln")
        if len(cells) < 3:
            continue
        home_name = ""
        away_name = ""
        home = draw = away = None
        for cell in cells:
            classes = " ".join(cell.get("class") or [])
            price_el = cell.select_one("span.price.dec")
            if price_el is None:
                continue
            try:
                price = float(price_el.get_text(strip=True))
            except ValueError:
                continue
            if price <= 1.0:
                continue
            name_el = cell.select_one(".seln-name")
            label = name_el.get_text(strip=True) if name_el else ""
            if "seln_sort-D" in classes:
                draw = price
                continue
            if home is None:
                home = price
                home_name = label
            elif away is None:
                away = price
                away_name = label
        if home is None or draw is None or away is None:
            continue
        if not home_name or not away_name:
            continue
        event_name = normalize_event_name(f"{home_name} vs {away_name}")
        if not event_name or event_name in seen:
            continue
        seen.add(event_name)
        row = build_event(event_name, "1X2", {"home": home, "draw": draw, "away": away})
        if row:
            events.append(row)
    return events


class WplayScraper:
    bookmaker_name = "wplay"

    def scrape(self) -> list[dict[str, Any]]:
        return scrape_wplay()

    def fetch_odds(self):
        return quotes_from_events(self.bookmaker_name, self.scrape())
