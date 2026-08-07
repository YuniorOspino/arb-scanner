"""
Filtro de sensatez — separa arbitraje real de errores de cuota / alertas viejas.

Cualquier alerta con ROI fuera de rango normal de arbitraje deportivo
se enruta a cola de revisión manual, NUNCA al flujo de notificación rápida.
Alertas con edad > ALERT_MAX_AGE se descartan (la cuota ya no es fiable).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from scrapers.event_names import is_virtual_or_esport_event

ROI_MIN = 0.5  # por debajo de esto no vale la pena por costos/fricción
ROI_MAX = 30  # por encima de esto, probable error de cuota
# (calibrado con ~300-400 alertas históricas: la mayoría
# de arbitrajes reales cae hasta ~30%; valores como 229%,
# 365%, 998% fueron confirmados como errores de cuota)

# Odds se mueven en segundos; no notificar oportunidades viejas.
ALERT_MAX_AGE_SECONDS = float(os.getenv("ALERT_MAX_AGE_SECONDS", "90"))


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


def edad_segundos(alerta: dict) -> float | None:
    """Edad desde detected_at; None si no hay timestamp usable."""
    detected = _parse_detected_at(alerta.get("detected_at"))
    if detected is None:
        return None
    return (datetime.now(timezone.utc) - detected).total_seconds()


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

    age = edad_segundos(alerta)
    if age is not None and age > ALERT_MAX_AGE_SECONDS:
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
