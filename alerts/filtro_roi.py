"""
Filtro de sensatez — separa arbitraje real de errores de cuota / alertas viejas.

Cualquier alerta con ROI fuera de rango normal de arbitraje deportivo
se enruta a cola de revisión manual, NUNCA al flujo de notificación rápida.
Alertas con edad > ALERT_MAX_AGE se descartan (la cuota ya no es fiable).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from scrapers.event_names import is_virtual_or_esport_event

logger = logging.getLogger(__name__)

ROI_MIN = 0.5  # por debajo de esto no vale la pena por costos/fricción
ROI_MAX = 30  # por encima de esto, probable error de cuota
# (calibrado con ~300-400 alertas históricas: la mayoría
# de arbitrajes reales cae hasta ~30%; valores como 229%,
# 365%, 998% fueron confirmados como errores de cuota)

# Odds se mueven en segundos; no notificar oportunidades viejas.
# Read lazily via get_alert_max_age() so .env / Railway env is always current.
def get_alert_max_age() -> float:
    return float(os.getenv("ALERT_MAX_AGE_SECONDS", "90"))


# Backward-compatible name (may be stale if env changes after import — prefer getter).
ALERT_MAX_AGE_SECONDS = get_alert_max_age()


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
        # Convention: naive = UTC (same as OpportunityStore / Railway).
        detected = detected.replace(tzinfo=timezone.utc)
    return detected.astimezone(timezone.utc)


def edad_detalle(alerta: dict) -> tuple[float | None, datetime | None, datetime]:
    """
    Returns (age_seconds, detected_utc, now_utc).
    age_seconds is None if detected_at missing/unparseable.
    """
    now = datetime.now(timezone.utc)
    detected = _parse_detected_at(alerta.get("detected_at"))
    if detected is None:
        return None, None, now
    return (now - detected).total_seconds(), detected, now


def edad_segundos(alerta: dict) -> float | None:
    """Edad desde detected_at; None si no hay timestamp usable."""
    age, _detected, _now = edad_detalle(alerta)
    return age


def clasificar_alerta(alerta: dict) -> str:
    """
    alerta: dict con al menos 'roi'; opcionalmente 'detected_at' / 'partido'.
    Devuelve una de: 'VALIDA', 'SOSPECHOSA_ERROR_CUOTA',
                      'DESCARTADA_BAJO_ROI', 'DESCARTADA_ROI_INVALIDO',
                      'DESCARTADA_EDAD', 'DESCARTADA_VIRTUAL'
    """
    partido = str(alerta.get("partido") or alerta.get("event_name") or "")
    if is_virtual_or_esport_event(partido):
        return "DESCARTADA_VIRTUAL"

    age, detected, now = edad_detalle(alerta)
    max_age = get_alert_max_age()
    if age is not None and age > max_age:
        # One WARNING with full evidence — critical for diagnosing false positives.
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

    try:
        roi = float(alerta.get("roi"))
    except (TypeError, ValueError):
        return "DESCARTADA_ROI_INVALIDO"

    if roi > ROI_MAX:
        return "SOSPECHOSA_ERROR_CUOTA"

    if roi < ROI_MIN:
        return "DESCARTADA_BAJO_ROI"

    return "VALIDA"
