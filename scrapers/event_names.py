"""Normalize event names so the same match aligns across bookmakers."""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_SUFFIXES = (
    "fc",
    "cf",
    "sc",
    "ac",
    "afc",
    "sp",
    "rs",
    "cd",
    "ud",
    "rc",
    "fk",
    "nk",
    "bk",
    "if",
    "ff",
    "club",
    "deportivo",
    "de",
    "la",
    "el",
    "los",
    "las",
)

# Virtual / eSoccer / simulated football — not comparable across books like real matches.
_VIRTUAL_KEYWORDS = (
    "virtual",
    "esoccer",
    "e-soccer",
    "e soccer",
    "efootball",
    "e-football",
    "e football",
    "fifa ",
    " fifa",
    "ea fc",
    "ea sports",
    "cyber live",
    "cyber arena",
    "esport",
    "e-sport",
    "e sport",
    "simulated",
    "simulacion",
    "simulación",
    "simulada",
    "vfl ",
    " vfl",
    "vfwc",
    "betradar",
    "h2h gg",
    "gt league",
    "gt leagues",
    "adriatic league",
    "esoccer battle",
    "futbol virtual",
    "fútbol virtual",
    "football virtual",
    "penaltis virtual",
    "penalty virtual",
    "kiron",
    "leap gaming",
)

# Youth / reserve markers that legitimately contain digits.
_ALLOWED_DIGIT_TOKENS = re.compile(
    r"^(u\d{2}|sub-?\d{2}|\d{2}s?)$",
    re.IGNORECASE,
)
# Gamertag / lobby codes: "drksd3", "player99", "xXx1"
_GAMERTAG_TOKEN = re.compile(r"^(?=.*\d)[a-z0-9]{3,12}$", re.IGNORECASE)

# Common real second/third words in club names (not player tags).
_COMMON_TEAM_WORDS = frozenset(
    {
        "united",
        "city",
        "town",
        "hotspur",
        "rovers",
        "athletic",
        "atletico",
        "atleticos",
        "madrid",
        "barcelona",
        "munich",
        "milan",
        "inter",
        "forest",
        "villa",
        "wanderers",
        "county",
        "albion",
        "wednesday",
        "juniors",
        "youth",
        "women",
        "ladies",
        "femenino",
        "femenina",
        "reserves",
        "reserva",
        "paris",
        "saint",
        "germain",
        "sporting",
        "racing",
        "independiente",
        "nacional",
        "america",
        "americas",
        "internacional",
        "palace",
        "bromwich",
        "hamburg",
        "frankfurt",
        "leverkusen",
        "dortmund",
        "glasgow",
        "edinburgh",
        "lisbon",
        "lisboa",
        "porto",
        "benfica",
        "ajax",
        "eindhoven",
        "brugge",
        "andersen",
        "caldas",
        "plate",
        "boca",
        "river",
        "santos",
        "palmeiras",
        "corinthians",
        "flamengo",
        "fluminense",
        "gremio",
        "botafogo",
        "mineiro",
        "paranaense",
        "cruziero",
        "cruzeiro",
        "spartak",
        "dynamo",
        "dinamo",
        "cska",
        "zenit",
        "shakhtar",
        "olympiacos",
        "olympique",
        "marseille",
        "lyon",
        "monaco",
        "rennes",
        "lille",
        "nice",
        "nantes",
        "bordeaux",
        "sevilla",
        "valencia",
        "villarreal",
        "sociedad",
        "bilbao",
        "betis",
        "espanol",
        "espanyol",
        "getafe",
        "osasuna",
        "alaves",
        "celta",
        "levante",
        "girona",
        "mallorca",
        "cadiz",
        "elche",
        "granada",
        "almeria",
        "leganes",
        "eibar",
        "wolves",
        "wolverhampton",
        "brighton",
        "southampton",
        "leicester",
        "everton",
        "liverpool",
        "chelsea",
        "arsenal",
        "tottenham",
        "newcastle",
        "fulham",
        "brentford",
        "bournemouth",
        "nottingham",
        "ipswich",
        "luton",
        "burnley",
        "sheffield",
        "leeds",
        "west",
        "ham",
        "north",
        "end",
        "real",
        "club",
        "deportivo",
        "sport",
        "sports",
        "team",
        "fc",
        "cf",
        "sc",
        "ac",
        "afc",
    }
)


def is_virtual_or_esport_event(name: str) -> bool:
    """
    True for virtual football / eSoccer / simulated matches.

    Examples blocked: "arsenal luxiqq vs chelsea drksd3", "Virtual Football League…".
    """
    raw = str(name or "").strip().lower()
    if not raw:
        return False

    collapsed = unicodedata.normalize("NFKD", raw)
    collapsed = "".join(ch for ch in collapsed if not unicodedata.combining(ch))
    collapsed = re.sub(r"\s+", " ", collapsed).strip()

    for kw in _VIRTUAL_KEYWORDS:
        if kw in collapsed:
            logger.debug("Virtual/eSport keyword hit (%r) in %r", kw, name)
            return True

    # Work on normalized "a vs b" form when possible.
    norm = normalize_event_name(name)
    sides = [s.strip() for s in norm.split(" vs ") if s.strip()] if " vs " in norm else [norm]
    if len(sides) >= 2:
        left, right = sides[0], sides[1]
        if _side_looks_virtual(left) or _side_looks_virtual(right):
            logger.debug("Virtual/eSport side tag in %r", name)
            return True
        # Both sides carry an extra non-club token (classic eSoccer lobby tags).
        if _side_has_extra_tag(left) and _side_has_extra_tag(right):
            logger.debug("Virtual/eSport dual tags in %r", name)
            return True
    else:
        if _side_looks_virtual(norm):
            return True

    return False


def _side_looks_virtual(side: str) -> bool:
    for tok in side.split():
        if _ALLOWED_DIGIT_TOKENS.match(tok):
            continue
        if _GAMERTAG_TOKEN.match(tok):
            return True
        if re.search(r"\d", tok):
            return True
    return False


def _side_has_extra_tag(side: str) -> bool:
    """True when side is like 'arsenal luxiqq' (club + non-club token)."""
    tokens = [t for t in side.split() if t]
    if len(tokens) < 2:
        return False
    last = tokens[-1]
    if last in _COMMON_TEAM_WORDS:
        return False
    if _ALLOWED_DIGIT_TOKENS.match(last):
        return False
    # Extra short/medium alphanumeric tag that is not a normal club word.
    if re.fullmatch(r"[a-z]{3,12}", last) and last not in _COMMON_TEAM_WORDS:
        # Prefer tagging when the first token looks like a known club root
        # or when the last token is an unusual cluster (q/x/z heavy / repeated).
        if re.search(r"(.)\1", last) or re.search(r"[qxz]{2}|[qxz].*[qxz]", last):
            return True
        if tokens[0] in _COMMON_TEAM_WORDS and last not in _COMMON_TEAM_WORDS:
            return True
    return False


def normalize_event_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+[-–—]\s+", " vs ", text)
    text = re.sub(r"\s+v\.?\s+", " vs ", text, flags=re.IGNORECASE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    for suffix in _SUFFIXES:
        text = re.sub(rf"\b{suffix}\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    # Drop leftover single-letter tokens (e.g. women's "F" / "W" markers).
    text = re.sub(r"\b[a-z]\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if " vs " in text:
        parts = [p.strip() for p in text.split(" vs ") if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]} vs {parts[1]}"
    return text
