"""Send a fake arbitrage alert to verify Telegram credentials."""

from __future__ import annotations

import logging
import sys

from alerts.telegram_bot import send_arbitrage_alert_telegram
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, setup_logging

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
        return 1

    fake_opportunity = {
        "evento": "TEST ALERTA",
        "casas_involucradas": ["CasaA", "CasaB"],
        "mejores_cuotas": [2.5, 3.0, 2.8],
        "margen": 5.0,
        "stakes": {"Opcion1": 50, "Opcion2": 50},
    }

    ok = send_arbitrage_alert_telegram(
        fake_opportunity, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    )
    if not ok:
        logger.error(
            "Telegram failed. Open @DeportesY_bot, send /start, then update "
            "TELEGRAM_CHAT_ID in .env with the chat.id from getUpdates."
        )
        return 1

    logger.info("Test alert sent OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
