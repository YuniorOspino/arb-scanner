"""Telegram notifications for arbitrage opportunities."""

from __future__ import annotations

import logging
import os
from typing import Iterable

import requests

from alerts.formatter import format_arbitrage_alert, format_value_bet_alert
from core.models import ArbitrageOpportunity, OddsQuote
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def format_alert(event: dict, profit: float) -> str:
    """Backward-compatible wrapper; prefer format_arbitrage_alert(opportunity)."""
    payload = dict(event)
    payload.setdefault("profit_percent", profit)
    payload.setdefault("margen", profit)
    if "match" in payload and "evento" not in payload:
        payload["evento"] = payload["match"]
    return format_arbitrage_alert(payload)


def verify_opportunity_odds(
    opportunity: ArbitrageOpportunity,
    scrapers: Iterable[BaseScraper],
    *,
    quote_cache: dict[str, list[OddsQuote]] | None = None,
) -> bool:
    """
    Re-check live quotes before sending.

    Returns False (and caller must discard) if any leg is missing or its odds
    differ from the exact values used by the scanner.
    """
    cache = quote_cache if quote_cache is not None else {}
    scrapers_by_name = {s.bookmaker_name: s for s in scrapers}

    for bookmaker, outcome, expected_odds, _stake in opportunity.legs:
        scraper = scrapers_by_name.get(bookmaker)
        if scraper is None:
            logger.warning(
                "Descartada: scraper ausente para casa=%s event=%s",
                bookmaker,
                opportunity.event_name,
            )
            return False

        if bookmaker not in cache:
            try:
                cache[bookmaker] = scraper.fetch_odds()
            except Exception:
                logger.exception(
                    "Descartada: error re-leyendo cuotas de %s", bookmaker
                )
                return False

        current = None
        for quote in cache[bookmaker]:
            if (
                quote.event_name == opportunity.event_name
                and quote.outcome == outcome
                and quote.market_id == opportunity.market_type
            ):
                current = quote.odds
                break
        # Fallback: same event/outcome even if market_id empty on some quotes.
        if current is None:
            for quote in cache[bookmaker]:
                if (
                    quote.event_name == opportunity.event_name
                    and quote.outcome == outcome
                ):
                    current = quote.odds
                    break

        if current is None:
            logger.warning(
                "Descartada: cuota ausente antes de envio | casa=%s outcome=%s event=%s",
                bookmaker,
                outcome,
                opportunity.event_name,
            )
            return False

        if current != expected_odds:
            logger.warning(
                "Descartada: cuota cambio antes de envio | casa=%s outcome=%s "
                "event=%s expected=%s current=%s",
                bookmaker,
                outcome,
                opportunity.event_name,
                expected_odds,
                current,
            )
            return False

    return True


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
        "disable_web_page_preview": False,
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
    scrapers: Iterable[BaseScraper] | None = None,
    skip_verification: bool = False,
) -> bool:
    """
    Format and send an arb alert.

    If scrapers are provided, re-validates exact odds before sending.
    """
    if isinstance(opportunity, dict):
        from alerts.formatter import opportunity_from_payload

        model = opportunity_from_payload(opportunity)
    else:
        model = opportunity

    if not skip_verification and scrapers is not None:
        if not verify_opportunity_odds(model, scrapers):
            logger.warning(
                "Alerta no enviada: oportunidad descartada por cuotas | %s",
                model.event_name,
            )
            return False

    message = format_arbitrage_alert(model)
    return _post_telegram(bot_token, chat_id, message, timeout=timeout)


class TelegramAlerter:
    """Send arb alerts via Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        scrapers: Iterable[BaseScraper] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token and chat_id)
        self.scrapers = list(scrapers) if scrapers is not None else None

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
            opportunity,
            self.bot_token,
            self.chat_id,
            scrapers=self.scrapers,
        )

    def send_value_bet(self, value_bet: dict) -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled; value bet not sent")
            return False
        return send_value_bet_alert(value_bet, self.bot_token, self.chat_id)
