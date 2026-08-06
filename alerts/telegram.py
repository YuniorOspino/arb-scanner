"""Telegram notifications for arbitrage opportunities."""

from __future__ import annotations

import logging
import os

import requests

from alerts.formatter import format_arbitrage_alert, format_value_bet_alert
from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
RENTABLE_THRESHOLD = 1.50


def format_alert(event: dict, profit: float) -> str:
    """Build Telegram message with profit classification and dynamic stake."""
    from core.arbitrage import calculate_dynamic_stake

    profit_f = float(profit)
    stake = calculate_dynamic_stake(profit_f)
    status = (
        "✅ Cuota rentable" if profit_f >= RENTABLE_THRESHOLD else "⚠️ Cuota poco rentable"
    )

    books = event.get("books") or []
    if isinstance(books, str):
        books_str = books
    else:
        books_str = ", ".join(str(b) for b in books)

    message = (
        f"{status}\n"
        f"Evento: {event.get('match', 'Evento desconocido')}\n"
        f"Casas: {books_str}\n"
        f"Profit: {profit_f:.2f}%\n"
        f"Stake sugerido: {stake:.0f} COP"
    )

    detail = event.get("detail")
    if detail:
        message = f"{message}\n\n{detail}"

    return message


def send_telegram_message(
    message: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    *,
    timeout: float = 15.0,
) -> bool:
    """Send a raw text message to Telegram."""
    token = (
        bot_token
        or os.getenv("TELEGRAM_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    chat = (chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat:
        logger.error(
            "Missing TELEGRAM variables. Please set TELEGRAM_TOKEN "
            "(or TELEGRAM_BOT_TOKEN) and TELEGRAM_CHAT_ID in Railway."
        )
        return False

    return _post_telegram(token, chat, message, timeout=timeout)


def _post_telegram(
    bot_token: str,
    chat_id: str,
    message: str,
    *,
    timeout: float = 15.0,
) -> bool:
    if not bot_token or not chat_id:
        logger.error("Telegram send: missing bot_token or chat_id")
        return False

    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.ok:
            logger.info("Telegram message sent (chat_id=%s)", chat_id)
            return True
        logger.error(
            "Telegram API error: status=%s body=%s",
            resp.status_code,
            resp.text[:200],
        )
        return False
    except requests.RequestException:
        logger.exception("Error enviando mensaje a Telegram")
        return False


def send_value_bet_alert(
    value_bet: dict,
    bot_token: str,
    chat_id: str,
    *,
    timeout: float = 15.0,
) -> bool:
    message = format_value_bet_alert(value_bet)
    logger.debug(
        "Sending value-bet alert: odds=%s edge=%s",
        value_bet.get("cuota"),
        value_bet.get("edge"),
    )
    return _post_telegram(bot_token, chat_id, message, timeout=timeout)


def send_arbitrage_alert_telegram(
    opportunity: dict | ArbitrageOpportunity,
    bot_token: str,
    chat_id: str,
    *,
    timeout: float = 15.0,
) -> bool:
    from core.arbitrage import calculate_dynamic_stake

    if isinstance(opportunity, ArbitrageOpportunity):
        profit = float(opportunity.profit_percent)
        stake = calculate_dynamic_stake(profit)
        books = []
        seen = set()
        for book, _o, _od, _s in opportunity.legs:
            if book not in seen:
                seen.add(book)
                books.append(book)
        detail_payload = {
            "evento": opportunity.event_name,
            "profit_percent": profit,
            "total_stake": stake,
            "casas_involucradas": books,
            "mejores_cuotas": {
                outcome: {"casa": book, "cuota": odds}
                for book, outcome, odds, _stake in opportunity.legs
            },
        }
        event = {
            "match": opportunity.event_name,
            "books": books,
            "stake": stake,
            "detail": format_arbitrage_alert(detail_payload),
        }
    else:
        profit = float(
            opportunity.get("profit_percent", opportunity.get("margen", 0)) or 0
        )
        stake = calculate_dynamic_stake(profit)
        books = (
            opportunity.get("casas_involucradas")
            or opportunity.get("casas")
            or []
        )
        detail_payload = dict(opportunity)
        detail_payload["total_stake"] = stake
        event = {
            "match": opportunity.get("evento") or opportunity.get("event_name", "?"),
            "books": books,
            "stake": stake,
            "detail": format_arbitrage_alert(detail_payload),
        }

    message = format_alert(event, profit)
    return _post_telegram(bot_token, chat_id, message, timeout=timeout)


class TelegramAlerter:
    """Send arb alerts via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token and chat_id)

        if not self.enabled:
            logger.warning(
                "Telegram alerts disabled (missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
            )
        else:
            logger.info("Telegram alerter ready (chat_id=%s)", chat_id)

    def send_message(self, text: str) -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled; message not sent")
            return False
        return send_telegram_message(text, self.bot_token, self.chat_id)

    def send_opportunity(
        self, opportunity: dict | ArbitrageOpportunity
    ) -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled; opportunity not sent")
            return False
        return send_arbitrage_alert_telegram(
            opportunity, self.bot_token, self.chat_id
        )

    def send_value_bet(self, value_bet: dict) -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled; value bet not sent")
            return False
        return send_value_bet_alert(value_bet, self.bot_token, self.chat_id)
