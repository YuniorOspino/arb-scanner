"""Genera la página launcher HTML para una alerta (tap → abrir pestañas)."""

from __future__ import annotations

import json
from pathlib import Path

from alerts.formatter import _market_label, _split_teams
from alerts.quality_score import enrich_alerta_quality

PLANTILLA_PATH = Path(__file__).parent / "plantilla_launcher.html"


def _search_label(event_name: str) -> str:
    home, away = _split_teams(event_name)
    home = (home or "").strip()
    away = (away or "").strip()
    if home and away and home != "equipo local":
        return f"{home} vs {away}"
    return str(event_name or "").strip() or "—"


def _search_label_corto(event_name: str) -> str:
    home, away = _split_teams(event_name)
    home = (home or "").strip()
    if home and home != "equipo local":
        return home
    return _search_label(event_name)


def _as_market_label(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Mercado"
    # Codes like CORNERS_OU_8 / TT_AWAY_9 → human label.
    if "_" in text and " " not in text:
        return _market_label(text)
    return text


def _normalize_casas(alerta: dict) -> list[dict]:
    """Ensure each casa has mercado + texto de búsqueda listos para el launcher."""
    fallback = _as_market_label(
        str(alerta.get("mercado") or alerta.get("market_type") or "")
    )
    partido = str(alerta.get("partido") or "")
    deporte = str(alerta.get("deporte") or "Fútbol")
    buscar = str(alerta.get("buscar") or _search_label(partido))
    buscar_corto = str(alerta.get("buscar_corto") or _search_label_corto(partido))
    out: list[dict] = []
    for casa in alerta.get("casas") or []:
        c = dict(casa)
        raw = str(c.get("mercado") or c.get("market") or c.get("market_type") or "")
        c["mercado"] = _as_market_label(raw) if raw else fallback
        c["deporte"] = str(c.get("deporte") or deporte)
        c["partido"] = str(c.get("partido") or partido)
        c["buscar"] = str(c.get("buscar") or buscar)
        c["buscar_corto"] = str(c.get("buscar_corto") or buscar_corto)
        out.append(c)
    return out


def generar_html(alerta: dict) -> str:
    plantilla = PLANTILLA_PATH.read_text(encoding="utf-8")
    try:
        enrich_alerta_quality(alerta)
    except Exception:
        pass

    casas = _normalize_casas(alerta)
    # Mayor stake primero (misma prioridad en UI y al abrir pestañas).
    casas_ordenadas = sorted(
        casas,
        key=lambda c: float(c.get("stake") or 0),
        reverse=True,
    )

    partido = str(alerta.get("partido") or "")
    deporte = str(alerta.get("deporte") or "Fútbol")
    mercado = _as_market_label(
        str(alerta.get("mercado") or alerta.get("market_type") or "")
    )
    buscar = str(alerta.get("buscar") or _search_label(partido))

    ejecucion = alerta.get("ejecucion", "")
    tipo = str(alerta.get("tipo_plan") or alerta.get("tipo") or "arbitraje").lower()
    rec = alerta.get("recomendacion_resumen") or {}
    score = alerta.get("quality_score", "—")
    if tipo == "conservadora" or tipo == "proyeccion":
        titulo = "Conservadora"
        badge = f"🛡 Conservadora · viabilidad {score}/100"
        rec_line = (
            f"Priorizá {rec.get('casa')}: {rec.get('seleccion')} · stake {rec.get('stake')}"
            if rec
            else ""
        )
    elif tipo == "combinada":
        titulo = "Combinada"
        badge = f"🎯 Combinada · calidad {score}/100"
        rec_line = "Ejecutá piernas en orden (mayor stake primero)"
    else:
        titulo = "Arbitraje listo"
        badge = ""
        rec_line = ""

    html = plantilla
    html = html.replace("{{EJECUCION}}", str(ejecucion))
    html = html.replace("{{TITULO}}", titulo)
    html = html.replace("{{BADGE_PROYECCION}}", badge)
    html = html.replace("{{REC_LINE}}", rec_line)
    html = html.replace("{{ROI}}", str(alerta.get("roi", "")))
    html = html.replace("{{BENEFICIO}}", str(alerta.get("beneficio_esperado", "")))
    html = html.replace("{{DEPORTE}}", deporte)
    html = html.replace("{{PARTIDO}}", partido)
    html = html.replace("{{MERCADO}}", mercado)
    html = html.replace("{{BUSCAR}}", buscar)
    html = html.replace("{{BUSCAR_JSON}}", json.dumps(buscar, ensure_ascii=False))
    html = html.replace(
        "{{EJECUCION_JSON}}", json.dumps(ejecucion, ensure_ascii=False)
    )
    html = html.replace(
        "{{CASAS_JSON}}", json.dumps(casas_ordenadas, ensure_ascii=False)
    )

    return html
