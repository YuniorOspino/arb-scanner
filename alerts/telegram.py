"""Telegram notifications for arbitrage opportunities."""

from __future__ import annotations

import logging
import os
from datetime import timezone
from typing import TYPE_CHECKING, Any, Iterable

import requests

from alerts.formatter import (
    format_arbitrage_alert,
    format_execution_ready_alert,
    format_value_bet_alert,
)
from core.arbitrage import calculate_arbitrage
from core.models import ArbitrageOpportunity, OddsQuote
from scrapers.base import BaseScraper

if TYPE_CHECKING:
    from storage.database import OpportunityStore

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


def format_alert(event: dict, profit: float) -> str:
    """Backward-compatible wrapper; prefer format_arbitrage_alert(opportunity)."""
    payload = dict(event)
    payload.setdefault("profit_percent", profit)
    payload.setdefault("margen", profit)
    if "match" in payload and "evento" not in payload:
        payload["evento"] = payload["match"]
    return format_arbitrage_alert(payload)


def _lookup_quote(
    quotes: list[OddsQuote],
    *,
    event_name: str,
    outcome: str,
    market_id: str,
) -> float | None:
    for quote in quotes:
        if (
            quote.event_name == event_name
            and quote.outcome == outcome
            and quote.market_id == market_id
        ):
            return quote.odds
    for quote in quotes:
        if quote.event_name == event_name and quote.outcome == outcome:
            return quote.odds
    return None


def _fetch_book_quotes(
    bookmaker: str,
    scrapers_by_name: dict[str, BaseScraper],
    cache: dict[str, list[OddsQuote]],
) -> list[OddsQuote] | None:
    if bookmaker in cache:
        return cache[bookmaker]
    scraper = scrapers_by_name.get(bookmaker)
    if scraper is None:
        logger.warning("Descartada: scraper ausente para casa=%s", bookmaker)
        return None
    try:
        cache[bookmaker] = scraper.fetch_odds()
    except Exception:
        logger.exception("Descartada: error re-leyendo cuotas de %s", bookmaker)
        return None
    return cache[bookmaker]


def prepare_opportunity_for_alert(
    opportunity: ArbitrageOpportunity,
    scrapers: Iterable[BaseScraper],
    *,
    total_stake: float | None = None,
    min_profit_percent: float = 0.0,
    quote_cache: dict[str, list[OddsQuote]] | None = None,
) -> ArbitrageOpportunity | None:
    """
    Re-check live quotes before sending.

    - If any leg is missing → cancel
    - If odds changed → recalculate stakes/ROI with the existing arb math
    - If ROI falls below min_profit_percent → cancel
    """
    cache = quote_cache if quote_cache is not None else {}
    scrapers_by_name = {s.bookmaker_name: s for s in scrapers}
    stake_total = float(total_stake if total_stake is not None else opportunity.total_stake)

    fresh_quotes: list[OddsQuote] = []
    odds_changed = False

    for bookmaker, outcome, expected_odds, _stake in opportunity.legs:
        quotes = _fetch_book_quotes(bookmaker, scrapers_by_name, cache)
        if quotes is None:
            return None

        current = _lookup_quote(
            quotes,
            event_name=opportunity.event_name,
            outcome=outcome,
            market_id=opportunity.market_type,
        )
        if current is None:
            logger.warning(
                "Descartada: cuota ausente antes de envio | casa=%s outcome=%s event=%s",
                bookmaker,
                outcome,
                opportunity.event_name,
            )
            return None

        if current != expected_odds:
            odds_changed = True
            logger.info(
                "Cuota cambio antes de envio — recalculando | casa=%s outcome=%s "
                "event=%s expected=%s current=%s",
                bookmaker,
                outcome,
                opportunity.event_name,
                expected_odds,
                current,
            )

        fresh_quotes.append(
            OddsQuote(
                bookmaker=bookmaker,
                outcome=outcome,
                odds=float(current),
                market_id=opportunity.market_type,
                event_name=opportunity.event_name,
            )
        )

    if not odds_changed:
        if opportunity.profit_percent < min_profit_percent:
            logger.warning(
                "Alerta cancelada: ROI %.4f%% < umbral %.4f%% | %s",
                opportunity.profit_percent,
                min_profit_percent,
                opportunity.event_name,
            )
            return None
        return opportunity

    recalculated = calculate_arbitrage(
        fresh_quotes,
        total_stake=stake_total,
        min_profit_percent=min_profit_percent,
        market_type=opportunity.market_type,
    )
    if recalculated is None:
        logger.warning(
            "Alerta cancelada tras recalculo (sin arb o ROI bajo umbral) | %s",
            opportunity.event_name,
        )
        return None

    # Preserve original detection timestamp for age display.
    return ArbitrageOpportunity(
        event_name=recalculated.event_name,
        market_type=opportunity.market_type,
        profit_percent=recalculated.profit_percent,
        total_stake=recalculated.total_stake,
        legs=recalculated.legs,
        detected_at=opportunity.detected_at
        if opportunity.detected_at.tzinfo
        else opportunity.detected_at.replace(tzinfo=timezone.utc),
    )


def verify_opportunity_odds(
    opportunity: ArbitrageOpportunity,
    scrapers: Iterable[BaseScraper],
    *,
    quote_cache: dict[str, list[OddsQuote]] | None = None,
    total_stake: float | None = None,
    min_profit_percent: float = 0.0,
) -> ArbitrageOpportunity | None:
    """
    Backward-compatible pre-send gate.

    Returns the (possibly recalculated) opportunity, or None to cancel.
    """
    return prepare_opportunity_for_alert(
        opportunity,
        scrapers,
        total_stake=total_stake,
        min_profit_percent=min_profit_percent,
        quote_cache=quote_cache,
    )


def send_telegram_message(
    message: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    *,
    timeout: float = 15.0,
    reply_markup: dict[str, Any] | None = None,
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

    return _post_telegram(
        token, chat, message, timeout=timeout, reply_markup=reply_markup
    )


def _api_url(bot_token: str, method: str) -> str:
    return TELEGRAM_API_URL.format(token=bot_token, method=method)


def _post_telegram(
    bot_token: str,
    chat_id: str,
    message: str,
    *,
    timeout: float = 15.0,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    if not bot_token or not chat_id:
        logger.error("Telegram send: missing bot_token or chat_id")
        return False

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            _api_url(bot_token, "sendMessage"), json=payload, timeout=timeout
        )
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


def _execution_keyboard(execution_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Ejecutado",
                    "callback_data": f"em:done:{execution_id}",
                },
                {
                    "text": "🔓 Liberar",
                    "callback_data": f"em:free:{execution_id}",
                },
            ]
        ]
    }


def _opportunity_from_execution(execution: dict[str, Any]) -> ArbitrageOpportunity:
    from datetime import datetime as _dt

    legs = tuple(
        (
            str(leg["bookmaker"]),
            str(leg["outcome"]),
            float(leg["odds"]),
            float(leg["stake"]),
        )
        for leg in execution["legs"]
    )
    detected = execution.get("detected_at")
    if not isinstance(detected, _dt):
        detected = _dt.now(timezone.utc)
    elif detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    return ArbitrageOpportunity(
        event_name=str(execution["event_name"]),
        market_type=str(execution["market_type"]),
        profit_percent=float(execution["profit_percent"]),
        total_stake=float(execution["total_stake"]),
        legs=legs,
        detected_at=detected,
    )


def send_execution_ready_telegram(
    execution: dict[str, Any],
    bot_token: str,
    chat_id: str,
    *,
    timeout: float = 15.0,
    store: OpportunityStore | None = None,
) -> bool:
    """Send EM-prepared opportunity with Ejecutado/Liberar buttons."""
    if store is not None:
        model = store.execution_to_opportunity(execution)
    else:
        model = _opportunity_from_execution(execution)

    message = format_execution_ready_alert(
        model,
        status="ACTIVA",
        execution_id=int(execution["id"]),
        score=float(execution.get("score") or 0),
    )
    return _post_telegram(
        bot_token,
        chat_id,
        message,
        timeout=timeout,
        reply_markup=_execution_keyboard(int(execution["id"])),
    )


def poll_execution_callbacks(
    store: OpportunityStore,
    bot_token: str,
    *,
    offset: int | None = None,
    timeout: float = 2.0,
) -> tuple[int, dict[str, Any] | None]:
    """
    Process Telegram callback queries for Ejecutado/Liberar.

    Returns (next_offset, next_active_to_send_or_None).
    When active finishes, automatically promotes the next queued item.
    """
    token = (bot_token or "").strip()
    if not token:
        return offset or 0, None

    params: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(
            _api_url(token, "getUpdates"), params=params, timeout=timeout + 5
        )
        if not resp.ok:
            logger.warning("Telegram getUpdates failed: %s", resp.status_code)
            return offset or 0, None
        payload = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("Telegram getUpdates error")
        return offset or 0, None

    next_offset = offset or 0
    next_active: dict[str, Any] | None = None
    for update in payload.get("result") or []:
        update_id = int(update.get("update_id", 0))
        next_offset = max(next_offset, update_id + 1)
        cb = update.get("callback_query")
        if not isinstance(cb, dict):
            continue
        data = str(cb.get("data") or "")
        cb_id = cb.get("id")
        text, promoted = _handle_em_callback(store, data)
        if promoted is not None:
            next_active = promoted
        if cb_id:
            try:
                requests.post(
                    _api_url(token, "answerCallbackQuery"),
                    json={"callback_query_id": cb_id, "text": text, "show_alert": False},
                    timeout=10,
                )
            except requests.RequestException:
                logger.exception("answerCallbackQuery failed")
    return next_offset, next_active


def _handle_em_callback(
    store: OpportunityStore, data: str
) -> tuple[str, dict[str, Any] | None]:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "em":
        return "Ignorado", None
    action, raw_id = parts[1], parts[2]
    try:
        exec_id = int(raw_id)
    except ValueError:
        return "ID invalido", None
    if action == "done":
        ok = store.mark_executed(exec_id)
        if not ok:
            return "No se pudo marcar", None
        nxt = store.promote_next_active()
        return ("Marcado EJECUTADO — siguiente enviada" if nxt else "Marcado EJECUTADO"), nxt
    if action == "free":
        ok = store.release_reservation(exec_id)
        if not ok:
            return "No se pudo liberar", None
        nxt = store.promote_next_active()
        return ("Capital LIBERADO — siguiente enviada" if nxt else "Capital LIBERADO"), nxt
    return "Accion desconocida", None


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
    total_stake: float | None = None,
    min_profit_percent: float = 0.0,
) -> bool:
    """
    Format and send an arb alert.

    If scrapers are provided, re-validates quotes, recalculates on change,
    and cancels when ROI falls below threshold.
    """
    if isinstance(opportunity, dict):
        from alerts.formatter import opportunity_from_payload

        model = opportunity_from_payload(opportunity)
    else:
        model = opportunity

    if not skip_verification and scrapers is not None:
        model = prepare_opportunity_for_alert(
            model,
            scrapers,
            total_stake=total_stake,
            min_profit_percent=min_profit_percent,
        )
        if model is None:
            logger.warning("Alerta no enviada: oportunidad cancelada pre-envio")
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
        *,
        total_stake: float | None = None,
        min_profit_percent: float = 0.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token and chat_id)
        self.scrapers = list(scrapers) if scrapers is not None else None
        self.total_stake = total_stake
        self.min_profit_percent = min_profit_percent

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
            total_stake=self.total_stake,
            min_profit_percent=self.min_profit_percent,
        )

    def send_value_bet(self, value_bet: dict) -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled; value bet not sent")
            return False
        return send_value_bet_alert(value_bet, self.bot_token, self.chat_id)
