"""
Telegram + pipeline filtro ROI → buffer → launcher.

Reemplaza el envío directo: cada alerta activa del EM pasa por aquí
ANTES de Telegram.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from alerts.buffer_agrupacion import BufferAgrupacion
from alerts.endpoint_launcher import guardar_alerta_activa
from alerts.enviar_telegram_launcher import (
    alerta_from_execution,
    enviar_alerta_con_launcher,
)
from alerts.filtro_roi import ALERT_MAX_AGE_SECONDS, clasificar_alerta, edad_segundos

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def _on_ganadora(alerta: dict) -> None:
    # Re-check age after buffer window — odds may already be stale.
    if clasificar_alerta(alerta) == "DESCARTADA_EDAD":
        age = edad_segundos(alerta)
        logger.info(
            "DESCARTADA_EDAD post-buffer ejecucion=%s age=%.1fs",
            alerta.get("ejecucion"),
            age if age is not None else -1,
        )
        return
    guardar_alerta_activa(alerta)
    token = TELEGRAM_TOKEN
    chat = TELEGRAM_CHAT_ID
    if not token or not chat:
        logger.error("Telegram launcher: missing token/chat_id")
        return
    try:
        enviar_alerta_con_launcher(token, chat, alerta)
    except Exception:
        logger.exception(
            "Falló envío launcher ejecucion=%s", alerta.get("ejecucion")
        )


def _on_descartadas(alertas: list[dict]) -> None:
    for a in alertas:
        logger.info(
            "DESCARTADA_BUFFER ejecucion=%s roi=%s partido=%s",
            a.get("ejecucion"),
            a.get("roi"),
            a.get("partido"),
        )


_buffer = BufferAgrupacion(
    on_ganadora=_on_ganadora,
    on_descartadas=_on_descartadas,
    ventana_seg=float(os.getenv("BUFFER_VENTANA_SEG", "4.0")),
    criterio="roi",
)


def procesar_alerta_entrante(alerta: dict) -> str:
    """
    Intercepta alerta ANTES de Telegram.
    Retorna categoría del filtro (para logs).
    """
    categoria = clasificar_alerta(alerta)
    age = edad_segundos(alerta)

    if categoria == "DESCARTADA_EDAD":
        logger.info(
            "DESCARTADA_EDAD ejecucion=%s age=%.1fs max=%.0fs roi=%s partido=%s",
            alerta.get("ejecucion"),
            age if age is not None else -1,
            ALERT_MAX_AGE_SECONDS,
            alerta.get("roi"),
            alerta.get("partido"),
        )
        return categoria

    if categoria == "DESCARTADA_VIRTUAL":
        logger.info(
            "DESCARTADA_VIRTUAL ejecucion=%s partido=%s roi=%s",
            alerta.get("ejecucion"),
            alerta.get("partido"),
            alerta.get("roi"),
        )
        return categoria

    if categoria == "SOSPECHOSA_ERROR_CUOTA":
        logger.warning(
            "SOSPECHOSA_ERROR_CUOTA ejecucion=%s roi=%s — revisión manual",
            alerta.get("ejecucion"),
            alerta.get("roi"),
        )
        return categoria

    if categoria in ("DESCARTADA_BAJO_ROI", "DESCARTADA_ROI_INVALIDO"):
        logger.info(
            "%s ejecucion=%s roi=%s",
            categoria,
            alerta.get("ejecucion"),
            alerta.get("roi"),
        )
        return categoria

    logger.info(
        "VALIDA_ENTRA_A_BUFFER ejecucion=%s roi=%s age=%.1fs",
        alerta.get("ejecucion"),
        alerta.get("roi"),
        age if age is not None else -1,
    )
    _buffer.agregar_sync(alerta)
    return categoria


def enviar_ejecucion_por_pipeline(execution: dict[str, Any]) -> str:
    """Convierte execution EM → alerta launcher y entra al pipeline."""
    alerta = alerta_from_execution(execution)
    return procesar_alerta_entrante(alerta)


__all__ = [
    "enviar_ejecucion_por_pipeline",
    "procesar_alerta_entrante",
    "alerta_from_execution",
    "enviar_alerta_con_launcher",
]
