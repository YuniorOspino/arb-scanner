"""Genera la página launcher HTML para una alerta (tap → abrir pestañas)."""

from __future__ import annotations

import json
from pathlib import Path

from alerts.formatter import _market_label

PLANTILLA_PATH = Path(__file__).parent / "plantilla_launcher.html"


def _as_market_label(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Mercado"
    # Codes like CORNERS_OU_8 / TT_AWAY_9 → human label.
    if "_" in text and " " not in text:
        return _market_label(text)
    return text


def _normalize_casas(alerta: dict) -> list[dict]:
    """Ensure each casa has a human-readable `mercado` (critical for speed)."""
    fallback = _as_market_label(
        str(alerta.get("mercado") or alerta.get("market_type") or "")
    )
    out: list[dict] = []
    for casa in alerta.get("casas") or []:
        c = dict(casa)
        raw = str(c.get("mercado") or c.get("market") or c.get("market_type") or "")
        c["mercado"] = _as_market_label(raw) if raw else fallback
        out.append(c)
    return out


def generar_html(alerta: dict) -> str:
    plantilla = PLANTILLA_PATH.read_text(encoding="utf-8")

    casas = _normalize_casas(alerta)
    casas_ordenadas = sorted(
        casas, key=lambda c: c.get("volatilidad", 0), reverse=True
    )

    html = plantilla
    html = html.replace("{{EJECUCION}}", str(alerta.get("ejecucion", "")))
    html = html.replace("{{ROI}}", str(alerta.get("roi", "")))
    html = html.replace("{{BENEFICIO}}", str(alerta.get("beneficio_esperado", "")))
    html = html.replace("{{PARTIDO}}", str(alerta.get("partido", "")))
    html = html.replace(
        "{{CASAS_JSON}}", json.dumps(casas_ordenadas, ensure_ascii=False)
    )

    return html

