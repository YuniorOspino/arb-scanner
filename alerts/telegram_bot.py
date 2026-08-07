"""
Telegram + pipeline filtro ROI → buffer → launcher.

Reemplaza el envío directo: cada alerta activa del EM pasa por aquí
ANTES de Telegram.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from alerts.buffer_agrupacion import BufferAgrupacion
from alerts.daily_plan import apply_daily_plan, record_sent
from alerts.endpoint_launcher import guardar_alerta_activa
from alerts.enviar_telegram_launcher import (
    alerta_from_execution,
    enviar_alerta_con_launcher,
)
from alerts.filtro_roi import clasificar_alerta, edad_segundos

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# Aggregate discard reasons; flushed once per scan cycle (avoids Railway log spam).
_discard_counts: Counter[str] = Counter()
_store_ref: Any = None
_max_exposure_diaria: float = 0.0
_max_exposure_simultanea: float = 0.0


def configure_pipeline(
    store: Any,
    *,
    max_exposure_diaria: float = 0.0,
    max_exposure_simultanea: float = 0.0,
) -> None:
    global _store_ref, _max_exposure_diaria, _max_exposure_simultanea
    _store_ref = store
    _max_exposure_diaria = float(max_exposure_diaria or 0)
    _max_exposure_simultanea = float(max_exposure_simultanea or 0)


def flush_discard_summary() -> None:
    """Emit one INFO line for all discards accumulated this cycle."""
    global _discard_counts
    if not _discard_counts:
        return
    parts = [f"{n}× {reason}" for reason, n in sorted(_discard_counts.items())]
    total = sum(_discard_counts.values())
    logger.info("Descartes del ciclo (%d): %s", total, ", ".join(parts))
    _discard_counts = Counter()


def _count_discard(reason: str) -> None:
    _discard_counts[reason] += 1


def _record(
    alerta: dict,
    *,
    status: str,
    discard_reason: str | None = None,
) -> None:
    store = _store_ref
    if store is None:
        return
    try:
        store.record_alert_event(
            execution_id=alerta.get("ejecucion"),
            event_name=str(alerta.get("partido") or ""),
            market_type=str(alerta.get("market_type") or ""),
            market_label=str(alerta.get("mercado") or ""),
            casas=list(alerta.get("casas") or []),
            roi=float(alerta.get("roi") or 0),
            total_stake=float(alerta.get("total_stake") or 0),
            status=status,
            discard_reason=discard_reason,
        )
    except Exception:
        logger.exception("No se pudo guardar alert_history")


def _on_ganadora(alerta: dict) -> None:
    # Re-check age only if buffer delayed the send.
    if clasificar_alerta(alerta) == "DESCARTADA_EDAD":
        _count_discard("DESCARTADA_EDAD")
        _record(alerta, status="discarded", discard_reason="DESCARTADA_EDAD")
        return

    # Plan diario: tipo + riesgo/cupos (arb siempre prioritario).
    ok_plan, tipo_plan, motivo_plan = apply_daily_plan(alerta)
    if not ok_plan:
        _count_discard("DESCARTADA_PLAN_DIARIO")
        _record(alerta, status="discarded", discard_reason=f"PLAN:{motivo_plan}")
        logger.info(
            "Omitida por plan diario ejecucion=%s tipo=%s motivo=%s",
            alerta.get("ejecucion"),
            tipo_plan,
            motivo_plan,
        )
        return

    # Exposure check immediately before send
    store = _store_ref
    if store is not None:
        reason = store.check_exposure_limits(
            float(alerta.get("total_stake") or 0),
            max_diaria=_max_exposure_diaria,
            max_simultanea=_max_exposure_simultanea,
        )
        if reason:
            _count_discard(reason)
            _record(alerta, status="discarded", discard_reason=reason)
            logger.warning(
                "%s stake=%.2f day=%.2f lim_day=%.2f lim_sim=%.2f",
                reason,
                float(alerta.get("total_stake") or 0),
                store.exposure_sent_today(),
                _max_exposure_diaria,
                _max_exposure_simultanea,
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
        record_sent(alerta, tipo_plan)
        _record(alerta, status="sent")
        logger.info(
            "Alerta enviada tipo=%s ejecucion=%s roi=%s score=%s stake=%.2f partido=%s",
            tipo_plan,
            alerta.get("ejecucion"),
            alerta.get("roi"),
            alerta.get("quality_score"),
            float(alerta.get("total_stake") or 0),
            alerta.get("partido"),
        )
    except Exception:
        logger.exception(
            "Falló envío launcher ejecucion=%s", alerta.get("ejecucion")
        )


def _on_descartadas(alertas: list[dict]) -> None:
    if not alertas:
        return
    _discard_counts["DESCARTADA_BUFFER"] += len(alertas)
    for a in alertas:
        _record(a, status="discarded", discard_reason="DESCARTADA_BUFFER")


_buffer = BufferAgrupacion(
    on_ganadora=_on_ganadora,
    on_descartadas=_on_descartadas,
    # 0 = envío inmediato (recomendado). >0 agrupa N segundos y elige mayor ROI.
    ventana_seg=float(os.getenv("BUFFER_VENTANA_SEG", "0")),
    criterio="roi",
)


def procesar_alerta_entrante(alerta: dict) -> str:
    """
    Intercepta alerta ANTES de Telegram.
    Retorna categoría del filtro (para logs).
    """
    categoria = clasificar_alerta(alerta)

    if categoria != "VALIDA":
        _count_discard(categoria)
        _record(alerta, status="discarded", discard_reason=categoria)
        if categoria == "SOSPECHOSA_ERROR_CUOTA":
            logger.warning(
                "SOSPECHOSA_ERROR_CUOTA ejecucion=%s roi=%s",
                alerta.get("ejecucion"),
                alerta.get("roi"),
            )
        return categoria

    # Exposure pre-buffer (fail fast)
    store = _store_ref
    if store is not None:
        reason = store.check_exposure_limits(
            float(alerta.get("total_stake") or 0),
            max_diaria=_max_exposure_diaria,
            max_simultanea=_max_exposure_simultanea,
        )
        if reason:
            _count_discard(reason)
            _record(alerta, status="discarded", discard_reason=reason)
            return reason

    age = edad_segundos(alerta)
    logger.debug(
        "VALIDA→envio ejecucion=%s roi=%s age=%.1fs (buffer_ventana=%.1fs)",
        alerta.get("ejecucion"),
        alerta.get("roi"),
        age if age is not None else -1,
        float(_buffer.ventana_seg),
    )
    _buffer.agregar_sync(alerta)
    return categoria


def enviar_ejecucion_por_pipeline(execution: dict[str, Any]) -> str:
    """Convierte execution EM → alerta launcher y entra al pipeline."""
    alerta = alerta_from_execution(execution)
    return procesar_alerta_entrante(alerta)


__all__ = [
    "configure_pipeline",
    "enviar_ejecucion_por_pipeline",
    "procesar_alerta_entrante",
    "flush_discard_summary",
    "alerta_from_execution",
    "enviar_alerta_con_launcher",
]
