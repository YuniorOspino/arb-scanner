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
from alerts.filtro_roi import clasificar_alerta
from alerts.telegram import send_arbitrage_alert_telegram

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def _on_ganadora(alerta: dict) -> None:
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
        "VALIDA_ENTRA_A_BUFFER ejecucion=%s roi=%s",
        alerta.get("ejecucion"),
        alerta.get("roi"),
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
    "send_arbitrage_alert_telegram",
    "alerta_from_execution",
    "enviar_alerta_con_launcher",
]
