"""arb-scanner: FastAPI (launcher) + Scanner → EM → filtro/buffer → Telegram launcher."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI

from alerts.endpoint_launcher import router as launcher_router
from alerts.telegram import TelegramAlerter, poll_execution_callbacks
from alerts.telegram import prepare_opportunity_for_alert
from alerts.telegram_bot import (
    configure_pipeline,
    enviar_ejecucion_por_pipeline,
    flush_discard_summary,
)
from config import get_config, setup_logging
from core.scanner import ArbScanner
from scrapers import build_scrapers
from storage.database import OpportunityStore

logger = logging.getLogger(__name__)

_shutdown = False
_scanner: ArbScanner | None = None
_store: OpportunityStore | None = None
_min_profit_percent: float = 0.0
_alert_max_age_seconds: float = 90.0
_tg_update_offset: int = 0

# Categorías del pipeline que no deben ocupar el slot active.
_PIPELINE_DISCARD_RELEASE = frozenset(
    {
        "DESCARTADA_EDAD",
        "DESCARTADA_VIRTUAL",
        "DESCARTADA_BAJO_ROI",
        "DESCARTADA_ROI_INVALIDO",
        "DESCARTADA_LIMITE_EXPOSICION",
        "SOSPECHOSA_ERROR_CUOTA",
    }
)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# FastAPI principal (única instancia) — rutas del launcher
app = FastAPI(title="arb-scanner")
app.include_router(launcher_router)


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — shutting down after current cycle", signum)
    _shutdown = True


def send_startup_message(alerter: TelegramAlerter | None) -> None:
    if alerter is None or not alerter.enabled:
        logger.warning("Skipping startup Telegram message (alerter disabled)")
        return

    ok = alerter.send_message(
        "arb-scanner listo. Flujo: EM → filtro ROI → buffer → launcher Telegram."
    )
    if ok:
        logger.info("Startup Telegram message sent")
    else:
        logger.warning("Startup Telegram message not sent (check token/chat_id)")


def _send_active(execution: dict[str, Any], *, _depth: int = 0) -> None:
    """Intercepta ANTES de Telegram: filtro_roi + buffer → launcher."""
    if _store is None:
        return
    if _depth > 15:
        logger.error("EM→pipeline: demasiados descartes encadenados; abortando cadena")
        return

    categoria = enviar_ejecucion_por_pipeline(execution)
    logger.debug(
        "EM→pipeline id=%s event=%s score=%s categoria=%s",
        execution["id"],
        execution["event_name"],
        execution.get("score"),
        categoria,
    )
    if categoria == "VALIDA":
        logger.info(
            "EM→pipeline VALIDA id=%s event=%s score=%s",
            execution["id"],
            execution["event_name"],
            execution.get("score"),
        )

    if categoria not in _PIPELINE_DISCARD_RELEASE:
        return

    # No dejar active ocupado sin Telegram: liberar y promover la siguiente.
    _store.discard_active(int(execution["id"]), reason=categoria)
    nxt = _store.promote_next_active()
    if nxt is not None:
        _send_active(nxt, _depth=_depth + 1)


def run_scan_cycle() -> None:
    """Scanner → verify → EM → pipeline (filtro/buffer/launcher)."""
    if _scanner is None or _store is None:
        raise RuntimeError("Scanner/store not initialized")

    opportunities = _scanner.run_once()
    verified = []
    cancelled_pre_em = 0
    if opportunities:
        logger.info("Ciclo con %d oportunidad(es)", len(opportunities))
        quote_cache: dict = {}
        for opp in opportunities:
            ready = prepare_opportunity_for_alert(
                opp,
                _scanner.scrapers,
                total_stake=opp.total_stake,
                min_profit_percent=_min_profit_percent,
                quote_cache=quote_cache,
            )
            if ready is None:
                cancelled_pre_em += 1
                continue
            verified.append(ready)
        if cancelled_pre_em:
            logger.info(
                "%d oportunidades canceladas pre-EM (cuota/ROI) en este ciclo",
                cancelled_pre_em,
            )
    else:
        logger.debug("Sin oportunidades nuevas; EM reordena/expira cola existente.")

    # Expira cola abierta más vieja que el umbral de alerta (además del TTL EM).
    _store.expire_stale(max_age_seconds=_alert_max_age_seconds)
    to_send = _store.process_execution_cycle(verified)
    if to_send is None:
        active = _store.get_active_execution()
        if active is None:
            logger.debug("EM: sin oportunidad activa")
        else:
            logger.debug(
                "EM: activa sin cambios id=%s event=%s",
                active["id"],
                active["event_name"],
            )
        flush_discard_summary()
        return

    _send_active(to_send)
    flush_discard_summary()


def _poll_callbacks_once() -> None:
    global _tg_update_offset
    if _store is None or not TELEGRAM_TOKEN:
        return

    if _store.expire_stale(max_age_seconds=_alert_max_age_seconds):
        nxt = _store.promote_next_active()
        if nxt is not None:
            _send_active(nxt)

    _tg_update_offset, nxt = poll_execution_callbacks(
        _store,
        TELEGRAM_TOKEN,
        offset=_tg_update_offset or None,
    )
    if nxt is not None:
        _send_active(nxt)


def _build_alerter() -> TelegramAlerter | None:
    token = TELEGRAM_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.error(
            "Missing TELEGRAM variables. Please set TELEGRAM_TOKEN "
            "(or TELEGRAM_BOT_TOKEN) and TELEGRAM_CHAT_ID in Railway."
        )
        return None

    return TelegramAlerter(
        bot_token=token,
        chat_id=chat_id,
        enabled=True,
        total_stake=None,
        min_profit_percent=_min_profit_percent,
    )


def _start_fastapi_server() -> None:
    """Sirve el launcher en el mismo proceso (Railway PORT)."""
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info("FastAPI launcher listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> int:
    global _scanner, _store, _min_profit_percent, _alert_max_age_seconds

    setup_logging()
    cfg = get_config()
    _min_profit_percent = float(cfg.min_profit_percent)
    _alert_max_age_seconds = float(cfg.alert_max_age_seconds)

    logger.info("arb-scanner starting")
    logger.info(
        "Config: interval=%ss min_profit=%.2f%% stake=%.2f queue_max=%d ttl=%ds "
        "alert_max_age=%.0fs exp_day=%.0f exp_sim=%.0f books=%s",
        cfg.scan_interval_seconds,
        cfg.min_profit_percent,
        cfg.max_stake_total,
        cfg.execution_queue_max,
        cfg.execution_ttl_seconds,
        _alert_max_age_seconds,
        cfg.max_exposure_diaria,
        cfg.max_exposure_simultanea,
        ", ".join(cfg.active_bookmakers),
    )
    logger.debug("Database path: %s", cfg.database_path)

    scrapers = build_scrapers(cfg.active_bookmakers)
    if not scrapers:
        logger.error("No scrapers enabled — check config.active_bookmakers")
        return 1

    _store = OpportunityStore(
        cfg.database_path,
        book_capitals=cfg.book_capital_map(),
        top_n=cfg.execution_queue_max,
        ttl_seconds=cfg.execution_ttl_seconds,
        queue_max=cfg.execution_queue_max,
    )
    # Limpia backlog de execution_queue (alertas viejas que se reenviarían al promover).
    _store.purge_stale_open(max_age_seconds=_alert_max_age_seconds)
    configure_pipeline(
        _store,
        max_exposure_diaria=cfg.max_exposure_diaria,
        max_exposure_simultanea=cfg.max_exposure_simultanea,
    )
    alerter = _build_alerter()
    if alerter is not None:
        alerter.scrapers = scrapers
        alerter.min_profit_percent = _min_profit_percent
        alerter.total_stake = cfg.max_stake_total
    _scanner = ArbScanner(cfg, scrapers, _store, alerter=None)

    # FastAPI en hilo daemon; el loop del scanner queda en el hilo principal
    api_thread = threading.Thread(
        target=_start_fastapi_server, name="fastapi-launcher", daemon=True
    )
    api_thread.start()

    send_startup_message(alerter)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    sleep_seconds = int(cfg.scan_interval_seconds) or 60

    while not _shutdown:
        try:
            run_scan_cycle()
        except Exception as e:
            logger.error("Error en ciclo: %s", e, exc_info=True)

        if _shutdown:
            break

        logger.debug(
            "Sleeping %s seconds (poll EM callbacks / expiry)", sleep_seconds
        )
        for _ in range(sleep_seconds):
            if _shutdown:
                break
            try:
                _poll_callbacks_once()
            except Exception:
                logger.exception("Error polling Telegram callbacks")
            time.sleep(1)

    logger.info("arb-scanner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
