"""
Envía la alerta ganadora a Telegram con botón que abre el launcher HTML.

BASE_URL debe ser el dominio público de Railway (env BASE_URL o RAILWAY_PUBLIC_DOMAIN).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from alerts.formatter import (
    _display_book,
    _event_link,
    _market_label,
    _selection_label,
    _split_teams,
)
from alerts.quality_score import enrich_alerta_quality, why_good_bullets

logger = logging.getLogger(__name__)


def _search_label(event_name: str) -> str:
    """Texto 'Home vs Away' para buscar en la casa (no depende de formatter privado)."""
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


def resolve_base_url() -> str:
    explicit = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip().rstrip("/")
    if domain:
        if domain.startswith("http://") or domain.startswith("https://"):
            return domain
        return f"https://{domain}"
    # Fallback local / Railway PORT service
    port = (os.getenv("PORT") or "8000").strip()
    return f"http://localhost:{port}"


BASE_URL = resolve_base_url()


def alerta_from_execution(execution: dict[str, Any]) -> dict[str, Any]:
    """
    Adapta el dict del Execution Manager / ArbitrageOpportunity al formato launcher.

    Campos scraper/EM → alerta launcher:
      id              → ejecucion
      event_name      → partido
      profit_percent  → roi
      expected_profit → beneficio_esperado
      market_type     → mercado
      legs[]          → casas[] (nombre, seleccion, stake, cuota, link, volatilidad)
    """
    event_name = str(execution.get("event_name") or "")
    market_raw = str(execution.get("market_type") or "")
    market = _market_label(market_raw) if market_raw else ""
    deporte = str(execution.get("deporte") or execution.get("sport") or "Fútbol").strip() or "Fútbol"
    buscar = _search_label(event_name)
    buscar_corto = _search_label_corto(event_name)
    casas = []
    for leg in execution.get("legs") or []:
        book = str(leg.get("bookmaker") or "")
        outcome = str(leg.get("outcome") or "")
        # Prefer per-leg market when present (cross-market arbs); else alert-level.
        leg_market_raw = str(
            leg.get("mercado")
            or leg.get("market")
            or leg.get("market_type")
            or market_raw
            or ""
        )
        leg_market = (
            _market_label(leg_market_raw) if leg_market_raw else market or "Mercado"
        )
        seleccion = _selection_label(
            outcome, event_name, market_type=leg_market_raw
        )
        casas.append(
            {
                "nombre": _display_book(book),
                "seleccion": seleccion,
                "stake": float(leg.get("stake") or 0),
                "cuota": float(leg.get("odds") or 0),
                "link": _event_link(book, event_name),
                "volatilidad": 0,
                "bookmaker": book,
                "outcome": outcome,
                "mercado": leg_market,
                "market_type": leg_market_raw,
                "deporte": deporte,
                "partido": event_name,
                "buscar": buscar,
                "buscar_corto": buscar_corto,
            }
        )

    detected = execution.get("detected_at")
    if hasattr(detected, "isoformat"):
        # Always persist timezone-aware UTC string for age filter.
        if getattr(detected, "tzinfo", None) is None:
            from datetime import timezone as _tz

            detected = detected.replace(tzinfo=_tz.utc)
        detected_at = detected.isoformat()
    else:
        detected_at = str(detected) if detected else None

    return {
        "ejecucion": int(execution["id"]),
        "partido": event_name,
        "deporte": deporte,
        "buscar": buscar,
        "buscar_corto": buscar_corto,
        "roi": float(execution.get("profit_percent") or 0),
        "beneficio_esperado": float(execution.get("expected_profit") or 0),
        "mercado": market,
        "market_type": market_raw,
        "score": float(execution.get("score") or 0),
        "casas": casas,
        "detected_at": detected_at,
        # extras útiles (no rompen launcher)
        "total_stake": float(execution.get("total_stake") or 0),
        "status": execution.get("status"),
        "tipo": "arbitraje",
    }


def _fmt_money_cop(value: object) -> str:
    """Format amounts for Telegram: $85.000 or $15.27 when fractional."""
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value or "0")
    if abs(n - round(n)) < 1e-9:
        return f"${int(round(n)):,}".replace(",", ".")
    # Keep up to 2 decimals for expected profit, Colombian-ish separators.
    whole, frac = f"{n:.2f}".split(".")
    whole_fmt = f"{int(whole):,}".replace(",", ".")
    return f"${whole_fmt},{frac}"


def _fmt_roi(value: object) -> str:
    try:
        return f"{float(value):.2f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value or "0")


def _fmt_cuota(value: object) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value or "")
    text = f"{n:.2f}".rstrip("0").rstrip(".")
    return text or "0"


_CIRCLED = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


def generar_texto_proyeccion(alerta: dict) -> str:
    """
    Formato diferenciado: proyección / alta calidad estadística aparente.
    Pensado para lectura rápida en celular.
    """
    enrich_alerta_quality(alerta)
    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))
    score = alerta.get("quality_score")
    try:
        score_txt = f"{float(score):.0f}"
    except (TypeError, ValueError):
        score_txt = "—"
    deporte = alerta.get("deporte") or "Fútbol"
    partido = alerta.get("partido") or ""
    buscar = alerta.get("buscar") or _search_label(str(partido))
    mercado = alerta.get("mercado") or ""

    lineas = [
        f"💎 PROYECCIÓN ALTA #{ejecucion}",
        "━━━━━━━━━━━━━━━━",
        f"Score {score_txt}/100  ·  ROI {roi}%  ·  Beneficio ~{beneficio}",
        f"🏟 {deporte}",
    ]
    if partido:
        lineas.append(f"⚽ {partido}")
    if buscar:
        lineas.append(f"🔎 Buscar: {buscar}")
    if mercado:
        lineas.append(f"📊 Mercado: {mercado}")

    lineas.append("")
    lineas.append("📌 Por qué es buena")
    for b in why_good_bullets(alerta):
        lineas.append(f"• {b}")

    lineas.append("")
    lineas.append("✅ Cómo ejecutar (orden = mayor stake primero)")
    casas = alerta.get("casas") or []
    casas_ordenadas = sorted(
        casas, key=lambda c: float(c.get("stake") or 0), reverse=True
    )
    for i, c in enumerate(casas_ordenadas, start=1):
        mark = _CIRCLED[i - 1] if i <= len(_CIRCLED) else f"{i}."
        nombre = c.get("nombre") or "Casa"
        seleccion = c.get("seleccion") or ""
        cuota = _fmt_cuota(c.get("cuota"))
        stake = _fmt_money_cop(c.get("stake"))
        leg_mercado = c.get("mercado") or mercado
        lineas.append(f"{mark} {nombre}")
        if leg_mercado:
            lineas.append(f"   Mercado: {leg_mercado}")
        lineas.append(f"   Selección: {seleccion}")
        lineas.append(f"   Stake: {stake}  |  Cuota: {cuota}")

    lineas.append("")
    lineas.append("⏱ Pasos rápidos")
    lineas.append("1) Abrí el launcher (botón abajo)")
    lineas.append("2) Casa #1: copiá stake → pegá → confirmá")
    lineas.append("3) Casa #2: igual, sin demora")
    lineas.append("4) No cambies selección ni mercado")
    lineas.append("")
    lineas.append("👇 Ejecutar ahora:")
    return "\n".join(lineas).rstrip() + "\n"


def generar_texto_resumen(alerta: dict) -> str:
    # Branch: proyección alta usa formato propio.
    if alerta.get("es_proyeccion_alta") or alerta.get("tipo") == "proyeccion":
        return generar_texto_proyeccion(alerta)

    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))
    deporte = alerta.get("deporte") or "Fútbol"
    partido = alerta.get("partido") or ""
    buscar = alerta.get("buscar") or _search_label(str(partido))
    mercado = alerta.get("mercado") or ""

    lineas = [
        f"⚡ ARBITRAJE #{ejecucion}",
        f"ROI: {roi}% | Beneficio aprox: {beneficio}",
        f"🏟 Deporte: {deporte}",
    ]
    if partido:
        lineas.append(f"⚽ Partido: {partido}")
    if buscar:
        lineas.append(f"🔎 Buscar: {buscar}")
    if mercado:
        lineas.append(f"📊 Mercado: {mercado}")
    lineas.append("")

    casas = alerta.get("casas") or []
    casas_ordenadas = sorted(
        casas, key=lambda c: float(c.get("stake") or 0), reverse=True
    )
    for i, c in enumerate(casas_ordenadas, start=1):
        mark = _CIRCLED[i - 1] if i <= len(_CIRCLED) else f"{i}."
        nombre = c.get("nombre") or "Casa"
        seleccion = c.get("seleccion") or ""
        cuota = _fmt_cuota(c.get("cuota"))
        stake = _fmt_money_cop(c.get("stake"))
        leg_mercado = c.get("mercado") or mercado
        lineas.append(f"{mark} {nombre}")
        lineas.append(f"   🔎 Buscar: {c.get('buscar') or buscar}")
        if leg_mercado:
            lineas.append(f"   📊 Mercado: {leg_mercado}")
        lineas.append(f"   ✅ Apostar: {seleccion}")
        lineas.append(f"   💰 Stake: {stake}  |  Cuota: {cuota}")
        lineas.append("")

    lineas.append("👇 Un tap abre búsqueda del partido en cada casa:")
    return "\n".join(lineas).rstrip() + "\n"


def enviar_alerta_con_launcher(bot_token: str, chat_id: str, alerta: dict) -> dict:
    """
    bot_token / chat_id desde variables de entorno — no hardcodear.
    """
    enrich_alerta_quality(alerta)
    base = resolve_base_url()
    ejecucion_id = alerta.get("ejecucion")
    url_launcher = f"{base}/alerta/{ejecucion_id}"

    texto = generar_texto_resumen(alerta)
    es_proy = bool(alerta.get("es_proyeccion_alta"))
    btn = (
        "💎 Abrir ejecución (proyección)"
        if es_proy
        else "🚀 Abrir búsqueda del partido"
    )

    payload = {
        "chat_id": chat_id,
        "text": texto,
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": btn, "url": url_launcher}]]
        },
    }

    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json=payload,
        timeout=10,
    )
    if not resp.ok:
        logger.error(
            "Telegram launcher send failed: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        resp.raise_for_status()
    logger.info(
        "Telegram launcher sent ejecucion=%s tipo=%s score=%s url=%s",
        ejecucion_id,
        alerta.get("tipo"),
        alerta.get("quality_score"),
        url_launcher,
    )
    return resp.json()
