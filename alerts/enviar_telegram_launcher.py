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
from alerts.daily_plan import (
    TIPO_ARBITRAJE,
    TIPO_COMBINADA,
    TIPO_CONSERVADORA,
    progress_telegram_lines,
)
from alerts.quality_score import (
    enrich_alerta_quality,
    other_legs,
    pick_recommended_leg,
    why_good_bullets,
)

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


def _append_daily_progress(lineas: list[str], alerta: dict) -> None:
    lineas.append("")
    lineas.append("📅 Plan diario")
    for ln in progress_telegram_lines(alerta):
        if ln:
            lineas.append(ln)


def _append_legs_block(
    lineas: list[str],
    alerta: dict,
    *,
    orden_stake: bool = True,
) -> None:
    mercado = alerta.get("mercado") or ""
    buscar = alerta.get("buscar") or _search_label(str(alerta.get("partido") or ""))
    casas = list(alerta.get("casas") or [])
    if orden_stake:
        casas = sorted(casas, key=lambda c: float(c.get("stake") or 0), reverse=True)
    for i, c in enumerate(casas, start=1):
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
        lineas.append(f"   ✅ Selección: {seleccion}")
        lineas.append(f"   💰 Stake: {stake}  |  Cuota: {cuota}")
        lineas.append("")


def generar_texto_arbitraje(alerta: dict) -> str:
    enrich_alerta_quality(alerta)
    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))
    deporte = alerta.get("deporte") or "Fútbol"
    partido = alerta.get("partido") or ""
    buscar = alerta.get("buscar") or _search_label(str(partido))
    mercado = alerta.get("mercado") or ""

    lineas = [
        f"⚡ ARBITRAJE #{ejecucion}",
        "━━━━━━━━━━━━━━━━",
        f"ROI {roi}%  ·  Beneficio ~{beneficio}",
        f"🏟 {deporte}",
    ]
    if partido:
        lineas.append(f"⚽ {partido}")
    if buscar:
        lineas.append(f"🔎 Buscar: {buscar}")
    if mercado:
        lineas.append(f"📊 Mercado: {mercado}")
    lineas.append("")
    lineas.append("📌 Por qué")
    lineas.append("• Arbitraje ejecutable: margen bloqueado si salen ambas piernas")
    lineas.append("• Prioridad 1 del plan diario (ingreso controlado)")
    _append_legs_block(lineas, alerta)
    lineas.append("⏱ Pasos")
    lineas.append("1) Abrí launcher → casa mayor stake primero")
    lineas.append("2) Copiá stake → pegá → confirmá")
    lineas.append("3) Segunda casa sin demora")
    lineas.append("4) No cambies mercado/selección")
    _append_daily_progress(lineas, alerta)
    lineas.append("")
    lineas.append("👇 Ejecutar:")
    return "\n".join(lineas).rstrip() + "\n"


def generar_texto_conservadora(alerta: dict) -> str:
    """Apuesta conservadora de buena proyección (pierna principal)."""
    enrich_alerta_quality(alerta)
    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))
    score = alerta.get("quality_score")
    try:
        score_txt = f"{float(score):.0f}"
    except (TypeError, ValueError):
        score_txt = "—"

    partido = str(alerta.get("partido") or "")
    buscar = str(alerta.get("buscar") or _search_label(partido))
    mercado_alert = str(alerta.get("mercado") or "")
    primary = alerta.get("recomendacion") or pick_recommended_leg(alerta)
    rest = other_legs(alerta, primary)

    lineas = [
        f"🛡 CONSERVADORA #{ejecucion}",
        "━━━━━━━━━━━━━━━━",
        f"Viabilidad {score_txt}/100  ·  Edge/ROI {roi}%  ·  ~{beneficio}",
    ]
    if partido:
        lineas.append(f"⚽ {partido}")

    lineas.append("")
    lineas.append("🎯 Apuesta (prioridad)")
    if primary:
        lineas.append(f"Casa: {primary.get('nombre') or primary.get('bookmaker')}")
        lineas.append(f"Buscar: {primary.get('buscar') or buscar}")
        lineas.append(f"Mercado: {primary.get('mercado') or mercado_alert}")
        lineas.append(f"Selección: {primary.get('seleccion') or primary.get('outcome')}")
        lineas.append(f"Stake: {_fmt_money_cop(primary.get('stake'))}")
        lineas.append(f"Cuota: {_fmt_cuota(primary.get('cuota'))}")

    lineas.append("")
    lineas.append("📌 Por qué se recomienda")
    for b in why_good_bullets(alerta):
        lineas.append(f"• {b}")
    lineas.append("• Perfil conservador: cuota moderada + mercado estable")

    if rest:
        lineas.append("")
        lineas.append("🛡 Cobertura opcional (si querés cerrar arb)")
        for c in rest[:2]:
            lineas.append(
                f"• {c.get('nombre')}: {c.get('seleccion')} · "
                f"{_fmt_money_cop(c.get('stake'))} @ {_fmt_cuota(c.get('cuota'))}"
            )

    casa = (primary or {}).get("nombre") or "la casa"
    lineas.append("")
    lineas.append("⏱ Pasos")
    lineas.append("1) Abrí launcher")
    lineas.append(f"2) En {casa}: copiar stake → confirmar selección")
    lineas.append("3) Solo si la cuota sigue igual o mejor")
    _append_daily_progress(lineas, alerta)
    lineas.append("")
    lineas.append("👇 Ejecutar:")
    return "\n".join(lineas).rstrip() + "\n"


def generar_texto_combinada(alerta: dict) -> str:
    """Plan combinado 2–3 piernas de alta calidad (pocas al día)."""
    enrich_alerta_quality(alerta)
    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))
    score = alerta.get("quality_score")
    try:
        score_txt = f"{float(score):.0f}"
    except (TypeError, ValueError):
        score_txt = "—"
    partido = str(alerta.get("partido") or "")
    n = len(alerta.get("casas") or [])

    lineas = [
        f"🎯 COMBINADA #{ejecucion}",
        "━━━━━━━━━━━━━━━━",
        f"Alta calidad {score_txt}/100  ·  {n} piernas  ·  ROI {roi}%  ·  ~{beneficio}",
    ]
    if partido:
        lineas.append(f"⚽ {partido}")
    lineas.append("")
    lineas.append("📌 Por qué (alta exigencia)")
    for b in why_good_bullets(alerta)[:4]:
        lineas.append(f"• {b}")
    lineas.append("• Solo se envían pocas combinadas/día (calidad > cantidad)")
    lineas.append("")
    lineas.append("✅ Piernas (máx 3 — ejecutá en orden)")
    _append_legs_block(lineas, alerta)
    lineas.append("⏱ Pasos")
    lineas.append("1) Abrí launcher")
    lineas.append("2) Ejecutá pierna 1 (mayor stake)")
    lineas.append("3) Pierna 2 (y 3 si hay) sin demora")
    lineas.append("4) Si una cuota se movió en contra: abortá el resto")
    _append_daily_progress(lineas, alerta)
    lineas.append("")
    lineas.append("👇 Ejecutar:")
    return "\n".join(lineas).rstrip() + "\n"


# Compat nombre anterior
def generar_texto_proyeccion(alerta: dict) -> str:
    return generar_texto_conservadora(alerta)


def generar_texto_resumen(alerta: dict) -> str:
    enrich_alerta_quality(alerta)
    tipo = str(alerta.get("tipo_plan") or alerta.get("tipo") or TIPO_ARBITRAJE).lower()
    if tipo == TIPO_CONSERVADORA or tipo == "proyeccion":
        return generar_texto_conservadora(alerta)
    if tipo == TIPO_COMBINADA:
        return generar_texto_combinada(alerta)
    return generar_texto_arbitraje(alerta)


def enviar_alerta_con_launcher(bot_token: str, chat_id: str, alerta: dict) -> dict:
    """
    bot_token / chat_id desde variables de entorno — no hardcodear.
    """
    enrich_alerta_quality(alerta)
    base = resolve_base_url()
    ejecucion_id = alerta.get("ejecucion")
    url_launcher = f"{base}/alerta/{ejecucion_id}"

    texto = generar_texto_resumen(alerta)
    tipo = str(alerta.get("tipo_plan") or alerta.get("tipo") or TIPO_ARBITRAJE)
    if tipo == TIPO_CONSERVADORA:
        btn = "🛡 Abrir conservadora"
    elif tipo == TIPO_COMBINADA:
        btn = "🎯 Abrir combinada"
    else:
        btn = "⚡ Abrir arbitraje"

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
