"""Telegram notifications for arbitrage opportunities."""

from __future__ import annotations

import logging
import os

import requests

from alerts.formatter import format_value_bet_alert
from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
RENTABLE_THRESHOLD = 1.50


def format_alert(event: dict, profit: float) -> str:
    """Build Telegram message with classification, stake and betting instructions."""
    from core.arbitrage import calculate_dynamic_stake

    profit_f = float(profit)
    stake = calculate_dynamic_stake(profit_f)
    status = (
        "✅ Cuota rentable" if profit_f >= RENTABLE_THRESHOLD else "⚠️ Cuota poco rentable"
    )

    books = event.get("books") or []
    if isinstance(books, str):
        books_list = [b.strip() for b in books.split(",") if b.strip()]
        books_str = books
    else:
        books_list = list(books)
        books_str = ", ".join(str(b) for b in books_list)

    outcomes = event.get("outcomes") or {}
    stakes_by_book = event.get("stakes") or {}

    instruction_lines = []
    for book in books_list:
        outcome = outcomes.get(book) or outcomes.get(str(book).lower()) or "resultado"
        book_stake = stakes_by_book.get(book, stakes_by_book.get(str(book).lower(), stake))
        try:
            book_stake_f = float(book_stake)
        except (TypeError, ValueError):
            book_stake_f = stake
        instruction_lines.append(
            f"- {book}: {book_stake_f:.0f} COP a {outcome}"
        )

    if not instruction_lines:
        instruction_lines = [f"- Stake total sugerido: {stake:.0f} COP"]

    instructions = "\n".join(instruction_lines)

    return (
        f"{status}\n"
        f"Evento: {event.get('match', 'Evento desconocido')}\n"
        f"Casas: {books_str}\n"
        f"Profit: {profit_f:.2f}%\n"
        f"Stake sugerido: {stake:.0f} COP\n\n"
        f"👉 Apuesta:\n{instructions}"
    )


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
    import re

    from core.arbitrage import calculate_arbitrage_stakes, calculate_dynamic_stake

    if isinstance(opportunity, ArbitrageOpportunity):
        profit = float(opportunity.profit_percent)
        stake = calculate_dynamic_stake(profit)
        parts = re.split(
            r"\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+",
            opportunity.event_name,
            flags=re.IGNORECASE,
        )
        local = parts[0].strip() if len(parts) >= 2 else "equipo local"
        visitante = parts[1].strip() if len(parts) >= 2 else "equipo visitante"
        labels = {
            "home": f"que gana {local}",
            "draw": "empate",
            "away": f"que gana {visitante}",
        }
        odds_map = {o: od for _b, o, od, _s in opportunity.legs}
        stakes_by_outcome = calculate_arbitrage_stakes(
            odds_map, stake, labels=list(odds_map.keys())
        ).get("stakes", {})

        books, outcomes, stakes_by_book = [], {}, {}
        seen = set()
        for book, outcome, _od, _s in opportunity.legs:
            if book not in seen:
                seen.add(book)
                books.append(book)
            outcomes[book] = labels.get(str(outcome).lower(), str(outcome))
            stakes_by_book[book] = float(
                stakes_by_outcome.get(outcome, stake / max(len(opportunity.legs), 1))
            )
        event = {
            "match": opportunity.event_name,
            "books": books,
            "outcomes": outcomes,
            "stakes": stakes_by_book,
            "stake": stake,
        }
    else:
        profit = float(
            opportunity.get("profit_percent", opportunity.get("margen", 0)) or 0
        )
        stake = calculate_dynamic_stake(profit)
        books = list(
            opportunity.get("casas_involucradas")
            or opportunity.get("casas")
            or []
        )
        mejores = opportunity.get("mejores_cuotas") or {}
        outcomes = opportunity.get("outcomes") or {}
        stakes_by_book = opportunity.get("stakes") or {}
        if isinstance(mejores, dict) and not outcomes:
            for outcome, info in mejores.items():
                if isinstance(info, dict) and info.get("casa"):
                    outcomes[str(info["casa"])] = str(outcome)
        event = {
            "match": opportunity.get("evento") or opportunity.get("event_name", "?"),
            "books": books,
            "outcomes": outcomes,
            "stakes": stakes_by_book if isinstance(stakes_by_book, dict) else {},
            "stake": stake,
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
