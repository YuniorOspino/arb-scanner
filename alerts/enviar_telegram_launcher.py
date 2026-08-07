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
)

logger = logging.getLogger(__name__)


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
        casas.append(
            {
                "nombre": _display_book(book),
                "seleccion": _selection_label(
                    outcome, event_name, market_type=leg_market_raw
                ),
                "stake": float(leg.get("stake") or 0),
                "cuota": float(leg.get("odds") or 0),
                "link": _event_link(book, event_name),
                "volatilidad": 0,
                "bookmaker": book,
                "outcome": outcome,
                "mercado": leg_market,
                "market_type": leg_market_raw,
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
        "roi": float(execution.get("profit_percent") or 0),
        "beneficio_esperado": float(execution.get("expected_profit") or 0),
        "mercado": market,
        "score": float(execution.get("score") or 0),
        "casas": casas,
        "detected_at": detected_at,
        # extras útiles (no rompen launcher)
        "total_stake": float(execution.get("total_stake") or 0),
        "status": execution.get("status"),
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


def generar_texto_resumen(alerta: dict) -> str:
    ejecucion = alerta.get("ejecucion", "")
    roi = _fmt_roi(alerta.get("roi"))
    beneficio = _fmt_money_cop(alerta.get("beneficio_esperado"))

    lineas = [
        f"⚡ ARBITRAJE #{ejecucion}",
        f"ROI: {roi}% | Beneficio aprox: {beneficio}",
    ]
    if alerta.get("partido"):
        lineas.append(f"🏆 {alerta['partido']}")
    if alerta.get("mercado"):
        lineas.append(f"📊 Mercado: {alerta['mercado']}")
    lineas.append("")

    casas = alerta.get("casas") or []
    casas_ordenadas = sorted(
        casas, key=lambda c: c.get("volatilidad", 0), reverse=True
    )
    for i, c in enumerate(casas_ordenadas, start=1):
        mark = _CIRCLED[i - 1] if i <= len(_CIRCLED) else f"{i}."
        nombre = c.get("nombre") or "Casa"
        seleccion = c.get("seleccion") or ""
        cuota = _fmt_cuota(c.get("cuota"))
        stake = _fmt_money_cop(c.get("stake"))
        lineas.append(f"{mark} {nombre}")
        lineas.append(f"   → Apostar {seleccion}")
        lineas.append(f"   → Cuota: {cuota}")
        lineas.append(f"   → Stake: {stake}")
        lineas.append("")

    lineas.append("👇 Un tap abre todas las casas listas:")
    return "\n".join(lineas).rstrip() + "\n"


def enviar_alerta_con_launcher(bot_token: str, chat_id: str, alerta: dict) -> dict:
    """
    bot_token / chat_id desde variables de entorno — no hardcodear.
    """
    base = resolve_base_url()
    ejecucion_id = alerta.get("ejecucion")
    url_launcher = f"{base}/alerta/{ejecucion_id}"

    texto = generar_texto_resumen(alerta)

    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🚀 Abrir todas las casas", "url": url_launcher}]
            ]
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
        "Telegram launcher sent ejecucion=%s url=%s",
        ejecucion_id,
        url_launcher,
    )
    return resp.json()
