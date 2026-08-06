"""arb-scanner entrypoint: continuous scan loop with Telegram startup ping."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

from alerts.formatter import format_arbitrage_alert
from alerts.telegram import (
    TelegramAlerter,
    send_telegram_message,
    verify_opportunity_odds,
)
from config import get_config, setup_logging
from core.scanner import ArbScanner
from scrapers import build_scrapers
from storage.database import OpportunityStore

logger = logging.getLogger(__name__)

_shutdown = False
_scanner: ArbScanner | None = None

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — shutting down after current cycle", signum)
    _shutdown = True


def send_startup_message(alerter: TelegramAlerter | None) -> None:
    if alerter is None or not alerter.enabled:
        logger.warning("Skipping startup Telegram message (alerter disabled)")
        return

    ok = alerter.send_message(
        "El motor arb-scanner inicio y esta buscando oportunidades."
    )
    if ok:
        logger.info("Startup Telegram message sent")
    else:
        logger.warning("Startup Telegram message not sent (check token/chat_id)")


def run_scan_cycle() -> None:
    if _scanner is None:
        raise RuntimeError("Scanner not initialized")

    opportunities = _scanner.run_once()
    if not opportunities:
        logger.info("No se encontraron oportunidades en este ciclo.")
        return

    logger.info("Ciclo con %d oportunidad(es)", len(opportunities))
    quote_cache: dict = {}
    for opp in opportunities:
        still_valid = verify_opportunity_odds(
            opp,
            _scanner.scrapers,
            quote_cache=quote_cache,
        )
        if not still_valid:
            logger.warning(
                "Oportunidad descartada (cuotas ausentes o cambiaron): %s",
                opp.event_name,
            )
            continue

        message = format_arbitrage_alert(opp)
        sent = send_telegram_message(
            message,
            bot_token=TELEGRAM_TOKEN,
            chat_id=TELEGRAM_CHAT_ID,
        )
        logger.info(
            "Alert sent=%s event=%s profit=%s",
            sent,
            opp.event_name,
            opp.profit_percent,
        )


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
    )


def main() -> int:
    global _scanner

    setup_logging()
    cfg = get_config()

    logger.info("arb-scanner starting")
    logger.info(
        "Config: interval=%ss min_profit=%.2f%% stake=%.2f books=%s",
        cfg.scan_interval_seconds,
        cfg.min_profit_percent,
        cfg.max_stake_total,
        ", ".join(cfg.active_bookmakers),
    )
    logger.debug("Database path: %s", cfg.database_path)

    scrapers = build_scrapers(cfg.active_bookmakers)
    if not scrapers:
        logger.error("No scrapers enabled — check config.active_bookmakers")
        return 1

    store = OpportunityStore(cfg.database_path)
    alerter = _build_alerter()
    if alerter is not None:
        alerter.scrapers = scrapers
    # alerter=None avoids duplicate Telegram sends from scanner; main sends alerts
    _scanner = ArbScanner(cfg, scrapers, store, alerter=None)

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

        logger.info("Sleeping %s seconds until next scan", sleep_seconds)
        for _ in range(sleep_seconds):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("arb-scanner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
