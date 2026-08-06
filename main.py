"""arb-scanner entrypoint: continuous scan loop."""

from __future__ import annotations

import logging
import signal
import sys
import time

from alerts.telegram import TelegramAlerter
from config import get_config, setup_logging
from core.scanner import ArbScanner
from scrapers import build_scrapers
from storage.database import OpportunityStore

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — shutting down after current cycle", signum)
    _shutdown = True


def main() -> int:
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
    alerter = TelegramAlerter(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        enabled=cfg.telegram_enabled,
    )
    scanner = ArbScanner(cfg, scrapers, store, alerter)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    while not _shutdown:
        try:
            scanner.run_once()
        except Exception:
            logger.exception("Unhandled error in scan cycle")

        if _shutdown:
            break

        logger.info("Sleeping %s seconds until next scan", cfg.scan_interval_seconds)
        # Interruptible sleep
        for _ in range(cfg.scan_interval_seconds):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("arb-scanner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
