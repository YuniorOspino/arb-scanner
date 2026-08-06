"""Normalize event names so the same match aligns across bookmakers."""

from __future__ import annotations

import re
import unicodedata

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
