"""Discover-and-normalize bookmaker markets into the scanner contract.

Contract consumed by find_opportunities / OddsQuote:
  {"event": str, "market": str, "odds": {outcome: float}}
where market is a comparable market_id shared across books.
"""

from __future__ import annotations

import re
from typing import Any


_LINE_RE = re.compile(
    r"(?<![0-9])([+-]?\d+(?:[.,]\d+)?)(?![0-9])"
)
_SCORE_RE = re.compile(r"^\d+\s*[:\-]\s*\d+$")
_HTFT_RE = re.compile(r"^[12xX]\s*/\s*[12xX]$")


def fmt_line(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    text = f"{num:.3f}".rstrip("0").rstrip(".")
    return text


def extract_line(text: str) -> float | None:
    """Best-effort line extraction from market/selection labels."""
    if not text:
        return None
    # Prefer patterns like +/- 2.5, over 2.5, Handicap 0:1 -> 1 for away? use 0:1 as EH
    m = re.search(r"[+/]\s*/?\s*-\s*(\d+(?:[.,]\d+)?)", text)  # +/- 2.5
    if m:
        return _to_float(m.group(1))
    m = re.search(r"(?:over|under|más de|mas de|menos(?: de)?)\s*(\d+(?:[.,]\d+)?)", text, re.I)
    if m:
        return _to_float(m.group(1))
    m = re.search(r"([+-]\s*\d+(?:[.,]\d+)?)", text)
    if m:
        return _to_float(m.group(1).replace(" ", ""))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:goals?|goles|corners?|esquinas|tarjetas)?\s*$", text, re.I)
    if m:
        return _to_float(m.group(1))
    return None


def _to_float(raw: str) -> float | None:
    try:
        return round(float(str(raw).replace(",", ".")), 3)
    except (TypeError, ValueError):
        return None


def normalize_outcome_label(raw: str) -> str:
    text = str(raw or "").strip().lower()
    text = (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    compact = re.sub(r"\s+", " ", text).strip()

    # Double chance variants
    if compact in {"1x", "1 o x", "1 o empate", "home or draw"}:
        return "1x"
    if compact in {"x2", "2x", "x o 2", "2 o empate", "away or draw", "empate o visitante"}:
        return "x2"
    if compact in {"12", "1 o 2", "home or away", "local o visitante"}:
        return "12"

    if compact in {"1", "home", "local", "h"}:
        return "home"
    if compact in {"x", "draw", "empate"}:
        return "draw"
    if compact in {"2", "away", "visitante", "a"}:
        return "away"

    if compact in {"yes", "si", "sí"} or compact == "si":
        return "yes"
    if compact == "no":
        return "no"

    # Over/under including +/- style
    if compact.startswith("+") or "mas de" in compact or compact.startswith("over") or compact.startswith("+"):
        if re.search(r"[+]?\s*\d", compact) or "mas" in compact or "over" in compact:
            if "menos" not in compact:
                return "over"
    if compact.startswith("-") or "menos" in compact or compact.startswith("under"):
        return "under"
    if re.fullmatch(r"\+\s*\d+(?:[.,]\d+)?", compact):
        return "over"
    if re.fullmatch(r"-\s*\d+(?:[.,]\d+)?", compact):
        return "under"
    if "mas de" in compact or compact.startswith("over"):
        return "over"
    if "menos" in compact or compact.startswith("under"):
        return "under"

    # correct score / htft
    compact2 = re.sub(r"\s+", "", compact).replace(":", "-")
    if _SCORE_RE.match(compact.replace(" ", "")) or _SCORE_RE.match(compact2.replace("-", ":")):
        parts = re.split(r"[:\-]", re.sub(r"\s+", "", compact))
        if len(parts) == 2:
            return f"{parts[0]}-{parts[1]}"
    if _HTFT_RE.match(compact.replace(" ", "")) or re.fullmatch(r"[12x]/[12x]", compact2):
        a, b = compact2.split("/")
        return f"{a.lower()}/{b.lower()}"

    return compact2 or compact


def classify_market(
    *,
    type_name: str = "",
    type_id: Any = None,
    label: str = "",
    line: float | None = None,
    english_type: str = "",
    home: str | None = None,
    away: str | None = None,
) -> str | None:
    """Return normalized market_id from discovered type/label/line, or None to skip."""
    blob = f"{english_type} {type_name} {label}".lower()
    blob = (
        blob.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )

    if re.search(r"\d{2}:\d{2}", blob) and "correct score" not in blob and "resultado correcto" not in blob:
        return None
    if any(
        x in blob
        for x in (
            "player",
            "goalscorer",
            "anotara",
            "goleador",
            "scorer",
            "marca al menos",
            "siguiente gol",
            "proximo gol",
            "next goal",
            "occurrence",
            "anytime",
            "first scorer",
            "last scorer",
            "jugadores",
        )
    ):
        return None

    if line is None:
        line = extract_line(f"{label} {type_name} {english_type}")
    line_s = fmt_line(line)

    # BTTS
    if "ambos equipos" in blob or "both teams" in blob or "btts" in blob or "will both teams score" in blob:
        if "1st half" in blob or "primer tiempo" in blob or "1a parte" in blob or "1ª parte" in blob:
            return "BTTS_HT"
        if "2nd half" in blob or "segundo tiempo" in blob or "2a parte" in blob:
            return "BTTS_HT2"
        return "BTTS"

    # Double chance
    if "doble oportunidad" in blob or "double chance" in blob:
        if "1st" in blob or "primer" in blob:
            return "DC_HT"
        return "DC"

    # Draw no bet
    if "sin empate" in blob or "draw no bet" in blob or "apuesta sin empate" in blob:
        return "DNB"

    # Correct score
    if "resultado correcto" in blob or "correct score" in blob:
        if "1st" in blob or "primer" in blob:
            return "CS_HT"
        return "CS"

    # HT/FT
    if (
        "half time/full time" in blob
        or "half time / full time" in blob
        or "ht/ft" in blob
        or "descanso/final" in blob
        or "htft" in blob
    ):
        return "HTFT"

    # Half time / 2nd half result
    if "half time result" in blob or "resultado primer tiempo" in blob or "1er tiempo" in blob:
        return "HT_1X2"
    if "winner (2nd" in blob or "resultado segundo tiempo" in blob or "2.a parte" in blob or "2ª parte" in blob:
        if "total" not in blob and "handicap" not in blob and "+/-" not in blob:
            return "HT2_1X2"
    if ("1.a parte" in blob or "1ª parte" in blob or "first half" in blob) and (
        "resultado" in blob or type_id in {2, "H1RS"}
    ):
        return "HT_1X2"

    # Corners
    if "esquina" in blob or "corner" in blob:
        if any(x in blob for x in ("total", "+/-", "over", "under", "mas/menos", "más/menos", "menos/mas")):
            return f"CORNERS_OU_{line_s}" if line_s else "CORNERS_OU"
        return None

    # Cards
    if "tarjeta" in blob or " card" in blob or "cards" in blob or "amonest" in blob:
        if any(x in blob for x in ("total", "+/-", "over", "under", "mas/menos", "más/menos")):
            return f"CARDS_OU_{line_s}" if line_s else "CARDS_OU"
        return None

    # Team totals (name contains a team)
    side = _team_total_side(label or type_name, home, away)
    is_totalish = any(
        x in blob
        for x in (
            "total",
            "+/-",
            "over/under",
            "mas/menos",
            "más/menos",
            "goles totales",
            "goals",
        )
    )
    if side and is_totalish and ("handicap" not in blob or "+/-" in blob):
        if "1st half" in blob or "primer" in blob:
            base = f"TT_{side}_HT"
        else:
            base = f"TT_{side}"
        return f"{base}_{line_s}" if line_s else base

    # European / Asian handicap
    if "3-way handicap" in blob or "handicap 3" in blob or re.search(r"handicap\s+\d+\s*:\s*\d+", blob):
        eh_line = _eh_line_from_label(label or type_name, line)
        return f"EH_{fmt_line(eh_line)}" if eh_line is not None else (f"EH_{line_s}" if line_s else "EH")
    if "points_spread" in blob or "asian handicap" in blob or (
        "handicap" in blob and "3-way" not in blob and "over" not in blob and "+/-" not in blob
    ):
        if "hándicap" in blob or "handicap" in blob or "points_spread" in blob:
            return f"AH_{line_s}" if line_s else "AH"

    # Match totals / OU
    if (
        "over/under" in blob
        or "asian over/under" in blob
        or "+/-" in blob
        or "mas/menos" in blob
        or "más/menos" in blob
        or "goles totales" in blob
        or ("total" in blob and "gol" in blob)
        or type_id in {6, 21, "HCTG", "OUH1"}
    ):
        if "1st half" in blob or "primer tiempo" in blob or type_id in {"OUH1"}:
            return f"OU_HT_{line_s}" if line_s else "OU_HT"
        if "2nd half" in blob or "segundo tiempo" in blob:
            return f"OU_HT2_{line_s}" if line_s else "OU_HT2"
        if "asian" in blob:
            return f"AOU_{line_s}" if line_s else "AOU"
        return f"OU_{line_s}" if line_s else "OU"

    # 1X2 / Match winner
    if (
        "resultado del partido" in blob
        or "resultado final" in blob
        or "match winner" in blob
        or "match result" in blob
        or "full time" in blob
        or type_id in {2, "MRES", "MR", "1X2", "MATCH_RESULT"}
        or blob.strip() in {"match", "partido", "mres", "1x2"}
    ):
        if "sin empate" in blob or "draw no bet" in blob:
            return "DNB"
        if "parte" in blob or "half" in blob:
            return None
        return "1X2"

    # Fallback discovered slug
    slug = re.sub(r"[^a-z0-9]+", "_", blob).strip("_")
    if not slug:
        return None
    if line_s:
        slug = f"{slug}_{line_s}"
    if len(slug) > 80:
        slug = slug[:80]
    return slug.upper()


def _eh_line_from_label(label: str, fallback: float | None) -> float | None:
    """Handicap 0:1 -> home -1 (or away +1). Use home line negative of away goals."""
    m = re.search(r"(\d+)\s*:\s*(\d+)", label or "")
    if m:
        h, a = int(m.group(1)), int(m.group(2))
        # European handicap shown as home:away goals start; home line = h - a
        return float(h - a)
    return fallback


def _team_total_side(label: str, home: str | None, away: str | None) -> str | None:
    text = str(label or "").lower()
    if home and str(home).lower() in text:
        return "HOME"
    if away and str(away).lower() in text:
        return "AWAY"
    if "local" in text or "home team" in text or "equipo local" in text:
        return "HOME"
    if "visitante" in text or "away team" in text or "equipo visitante" in text:
        return "AWAY"
    # Betano type codes
    if "ouhg" in text or "home" in text:
        return "HOME"
    if "ouag" in text or "away" in text:
        return "AWAY"
    return None


def build_event(
    event_name: str,
    market_id: str,
    odds: dict[str, float],
) -> dict[str, Any] | None:
    if not event_name or not market_id or not isinstance(odds, dict):
        return None
    from scrapers.event_names import is_virtual_or_esport_event

    if is_virtual_or_esport_event(event_name):
        return None
    clean = {str(k): float(v) for k, v in odds.items() if float(v) > 1.0}
    if len(clean) < 2:
        return None
    return {"event": event_name, "market": market_id, "odds": clean}


def quotes_from_events(bookmaker: str, events: list[dict[str, Any]]):
    from core.models import OddsQuote
    from scrapers.event_names import is_virtual_or_esport_event

    quotes = []
    for event in events:
        event_name = str(event.get("event") or "")
        if is_virtual_or_esport_event(event_name):
            continue
        for outcome, odd in (event.get("odds") or {}).items():
            quotes.append(
                OddsQuote(
                    bookmaker=bookmaker,
                    outcome=str(outcome),
                    odds=float(odd),
                    market_id=str(event.get("market") or ""),
                    event_name=event_name,
                )
            )
    return quotes


def split_ou_selections(
    selections: list[dict[str, Any]],
    *,
    name_key: str = "name",
    price_key: str = "price",
    line_key: str = "handicap",
) -> dict[str, dict[str, float]]:
    """Group over/under selections by line -> {line_str: {over/under: price}}."""
    buckets: dict[str, dict[str, float]] = {}
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        try:
            price = float(sel.get(price_key))
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        label = str(sel.get(name_key) or "")
        line = sel.get(line_key)
        if line in (None, "", 0, 0.0):
            line = extract_line(label)
        else:
            try:
                line = float(line)
            except (TypeError, ValueError):
                line = extract_line(label)
        if line is None:
            continue
        outcome = normalize_outcome_label(label)
        if outcome not in {"over", "under"}:
            # +/- style labels "+0.5" / "-0.5"
            if label.strip().startswith("+"):
                outcome = "over"
            elif label.strip().startswith("-"):
                outcome = "under"
            else:
                continue
        key = fmt_line(abs(float(line)) if outcome in {"over", "under"} and float(line) < 0 else float(line))
        # For +/- labels, magnitude is the total line
        if label.strip()[:1] in {"+", "-"}:
            key = fmt_line(abs(float(line)))
        buckets.setdefault(key, {})[outcome] = price
    return buckets
