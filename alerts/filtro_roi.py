"""
Filtro de sensatez + ejecutabilidad — decide qué alertas llegan a Telegram.

Capa 1 (seguridad): edad, virtuales, ROI absurdo / inválido.
Capa 2 (ejecución): ROI mínimo configurable, stake mínimo por pierna,
                    mercados frágiles (rechazo o ROI extra).

El buffer de agrupación prioriza ROI (mayor primero); score_ejecucion es desempate.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from scrapers.event_names import is_virtual_or_esport_event

logger = logging.getLogger(__name__)

# --- Configurable thresholds (env) -------------------------------------------
ROI_MAX = float(os.getenv("ALERT_ROI_MAX", "30"))


def get_roi_min() -> float:
    """ROI mínimo para notificar (default 1.0%). Override: ALERT_ROI_MIN."""
    return float(os.getenv("ALERT_ROI_MIN", os.getenv("MIN_MARGIN_THRESHOLD", "1.5")))


def get_min_stake() -> float:
    """Stake mínimo por pierna en COP. Override: ALERT_MIN_STAKE."""
    return float(os.getenv("ALERT_MIN_STAKE", "5000"))


def get_fragile_roi_extra() -> float:
    """ROI extra exigido a mercados volátiles (corners/cards/AOU…)."""
    return float(os.getenv("ALERT_FRAGILE_ROI_EXTRA", "1.5"))


# Backward-compatible aliases (may be stale after env change — prefer getters).
ROI_MIN = get_roi_min()


def get_alert_max_age() -> float:
    return float(os.getenv("ALERT_MAX_AGE_SECONDS", "90"))


ALERT_MAX_AGE_SECONDS = get_alert_max_age()

# Mercados que casi nunca se ejecutan a tiempo / muy ruidosos → rechazo duro.
_HARD_FRAGILE_KEYWORDS = (
    "anotador",
    "jugador",
    "player",
    "asistenc",
    "atajad",
    "faltas",
    "disparos",
    "tiros a puerta",
    "fueras de juego",
)

# Volátiles pero permitidos si el ROI compensa.
_SOFT_FRAGILE_PREFIXES = (
    "CORNERS_OU_",
    "CARDS_OU_",
    "AOU_",
    "TT_HOME_",
    "TT_AWAY_",
)


def _parse_detected_at(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        detected = raw
    else:
        try:
            detected = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    return detected.astimezone(timezone.utc)


def edad_detalle(alerta: dict) -> tuple[float | None, datetime | None, datetime]:
    now = datetime.now(timezone.utc)
    detected = _parse_detected_at(alerta.get("detected_at"))
    if detected is None:
        return None, None, now
    return (now - detected).total_seconds(), detected, now


def edad_segundos(alerta: dict) -> float | None:
    age, _detected, _now = edad_detalle(alerta)
    return age


def _market_raw(alerta: dict) -> str:
    if alerta.get("market_type"):
        return str(alerta["market_type"])
    # From launcher casas (per-leg) — use first non-empty.
    for casa in alerta.get("casas") or []:
        raw = casa.get("market_type") or casa.get("mercado") or ""
        if raw:
            return str(raw)
    return str(alerta.get("mercado") or "")


def _ou_line(market: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*$", str(market).upper().replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def mercado_es_fragil_duro(alerta: dict) -> bool:
    """Correct score / HTFT / player props → no enviar."""
    raw = _market_raw(alerta).upper().strip()
    label = str(alerta.get("mercado") or "").lower()
    blob = f"{raw} {label}".lower()

    if raw in {"CS", "HTFT"} or raw.startswith("CS_") or raw.startswith("HTFT"):
        return True
    for kw in _HARD_FRAGILE_KEYWORDS:
        if kw in blob:
            return True
    return False


def mercado_es_fragil_blando(alerta: dict) -> bool:
    """
    Corners/cards/team totals/asian OU o líneas OU extremas.
    Se permiten, pero exigen ROI más alto.
    """
    raw = _market_raw(alerta).upper()
    for prefix in _SOFT_FRAGILE_PREFIXES:
        if raw.startswith(prefix):
            return True
    if raw.startswith("AH_"):
        line = _ou_line(raw)
        if line is not None and abs(line) >= 2.0:
            return True
    if raw.startswith("OU_") or raw.startswith("OU_HT"):
        line = _ou_line(raw)
        # 0.5 / 1.5 / 2.5 / 3.5 are common; >4.5 moves fast / low liquidity.
        if line is not None and line > 4.5:
            return True
    return False


def estabilidad_mercado(alerta: dict) -> float:
    """
    0–10. Más alto = más fácil de ejecutar (para desempatar en el buffer).
    """
    raw = _market_raw(alerta).upper()
    if mercado_es_fragil_duro(alerta):
        return 0.0
    if raw in {"1X2", "MRES", "MR", "DNB"} or raw.startswith("DC"):
        return 10.0
    if raw.startswith("BTTS"):
        return 8.0
    if raw.startswith("OU_"):
        line = _ou_line(raw)
        if line in {0.5, 1.5, 2.5, 3.5}:
            return 7.0
        if line is not None and line <= 4.5:
            return 5.0
        return 3.0
    if raw.startswith("EH_") or raw.startswith("AH_"):
        return 4.0
    if mercado_es_fragil_blando(alerta):
        return 2.0
    return 5.0


def score_ejecucion(alerta: dict) -> float:
    """
    Score para elegir ganadora del buffer: ROI + bonus por mercado estable.
    """
    try:
        roi = float(alerta.get("roi") or 0)
    except (TypeError, ValueError):
        roi = 0.0
    return roi * 10.0 + estabilidad_mercado(alerta) * 2.5


def _min_leg_stake(alerta: dict) -> float | None:
    casas = alerta.get("casas") or []
    if not casas:
        # Fallback legs from EM shape if present
        legs = alerta.get("legs") or []
        stakes = []
        for leg in legs:
            if isinstance(leg, dict):
                try:
                    stakes.append(float(leg.get("stake") or 0))
                except (TypeError, ValueError):
                    continue
            elif isinstance(leg, (list, tuple)) and len(leg) >= 4:
                try:
                    stakes.append(float(leg[3]))
                except (TypeError, ValueError):
                    continue
        return min(stakes) if stakes else None
    stakes = []
    for c in casas:
        try:
            stakes.append(float(c.get("stake") or 0))
        except (TypeError, ValueError):
            continue
    return min(stakes) if stakes else None


def clasificar_alerta(alerta: dict) -> str:
    """
    Devuelve una de:
      VALIDA,
      SOSPECHOSA_ERROR_CUOTA,
      DESCARTADA_BAJO_ROI,
      DESCARTADA_ROI_INVALIDO,
      DESCARTADA_EDAD,
      DESCARTADA_VIRTUAL,
      DESCARTADA_MERCADO_FRAGIL,
      DESCARTADA_STAKE_BAJO
    """
    partido = str(alerta.get("partido") or alerta.get("event_name") or "")
    if is_virtual_or_esport_event(partido):
        return "DESCARTADA_VIRTUAL"

    age, detected, now = edad_detalle(alerta)
    max_age = get_alert_max_age()
    if age is not None and age > max_age:
        logger.warning(
            "DESCARTADA_EDAD id=%s partido=%s detected_at=%s now_utc=%s "
            "age=%.1fs max=%.0fs raw_detected_at=%r",
            alerta.get("ejecucion"),
            partido,
            detected.isoformat() if detected else None,
            now.isoformat(),
            age,
            max_age,
            alerta.get("detected_at"),
        )
        return "DESCARTADA_EDAD"

    if mercado_es_fragil_duro(alerta):
        logger.info(
            "DESCARTADA_MERCADO_FRAGIL id=%s market=%s partido=%s",
            alerta.get("ejecucion"),
            _market_raw(alerta),
            partido,
        )
        return "DESCARTADA_MERCADO_FRAGIL"

    try:
        roi = float(alerta.get("roi"))
    except (TypeError, ValueError):
        return "DESCARTADA_ROI_INVALIDO"

    roi_max = ROI_MAX
    if roi > roi_max:
        return "SOSPECHOSA_ERROR_CUOTA"

    roi_min = get_roi_min()
    if mercado_es_fragil_blando(alerta):
        roi_min = roi_min + get_fragile_roi_extra()

    if roi < roi_min:
        logger.info(
            "DESCARTADA_BAJO_ROI id=%s roi=%.2f min=%.2f market=%s",
            alerta.get("ejecucion"),
            roi,
            roi_min,
            _market_raw(alerta),
        )
        return "DESCARTADA_BAJO_ROI"

    min_stake = get_min_stake()
    leg_min = _min_leg_stake(alerta)
    if leg_min is not None and leg_min < min_stake:
        logger.info(
            "DESCARTADA_STAKE_BAJO id=%s min_leg=%.0f required=%.0f",
            alerta.get("ejecucion"),
            leg_min,
            min_stake,
        )
        return "DESCARTADA_STAKE_BAJO"

    # Attach score for buffer tie-break (ROI + estabilidad).
    alerta["score_ejecucion"] = score_ejecucion(alerta)
    # Score de proyección (alerta diferenciada en Telegram si es alta).
    try:
        from alerts.quality_score import enrich_alerta_quality

        enrich_alerta_quality(alerta)
    except Exception:
        logger.debug("quality_score enrich failed", exc_info=True)
    return "VALIDA"
