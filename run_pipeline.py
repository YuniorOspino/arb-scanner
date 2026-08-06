from __future__ import annotations

import logging
import sys
from collections import defaultdict
from typing import Any

from alerts.telegram_bot import send_arbitrage_alert_telegram
from alerts.value_bet_alerts import send_value_bet_alert
from config import (
    DB_PATH,
    MIN_MARGIN_THRESHOLD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TOTAL_INVESTMENT,
    setup_logging,
)
from core.arb_scanner import calculate_arbitrage_stakes, scan_multi_book_arbitrage
from core.value_betting import (
    calculate_kelly_stake,
    calculate_market_consensus,
    detect_value_bet,
)
from scrapers import bet365, betano, betplay, draftkings, fanduel, wplay
from storage.arb_history import save_arbitrage_opportunity
from storage.value_bet_history import save_value_bet

logger = logging.getLogger(__name__)


def _quotes_from_class_scraper(scraper_cls: type, bookmaker: str) -> list[dict[str, Any]]:
    scraper = scraper_cls()
    by_event: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for q in scraper.fetch_odds():
        by_event[(q.event_name, q.market_id or "1X2")][q.outcome] = float(q.odds)
    events: list[dict[str, Any]] = []
    for (event_name, market), odds_map in by_event.items():
        if {"home", "draw", "away"} <= set(odds_map):
            events.append(
                {
                    "event": event_name,
                    "market": market,
                    "bookmaker": bookmaker,
                    "odds": {
                        "home": odds_map["home"],
                        "draw": odds_map["draw"],
                        "away": odds_map["away"],
                    },
                }
            )
    return events


def _to_market_odds(quotes: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    market_odds: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for q in quotes:
        event = q["event"]
        book = q["bookmaker"]
        odds = q["odds"]
        market_odds[event]["home"][book] = float(odds["home"])
        market_odds[event]["draw"][book] = float(odds["draw"])
        market_odds[event]["away"][book] = float(odds["away"])
    return {event: dict(outcomes) for event, outcomes in market_odds.items()}


def run_once() -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []

    quotes.extend(_quotes_from_class_scraper(bet365.Bet365Scraper, "bet365"))
    quotes.extend(_quotes_from_class_scraper(draftkings.DraftKingsScraper, "draftkings"))
    quotes.extend(_quotes_from_class_scraper(fanduel.FanDuelScraper, "fanduel"))

    start = len(quotes)
    quotes.extend(betano.scrape_betano())
    for q in quotes[start:]:
        q["bookmaker"] = "betano"

    start = len(quotes)
    quotes.extend(wplay.scrape_wplay())
    for q in quotes[start:]:
        q["bookmaker"] = "wplay"

    start = len(quotes)
    quotes.extend(betplay.scrape_betplay())
    for q in quotes[start:]:
        q["bookmaker"] = "betplay"

    logger.info("Collected %d quote blocks from all books", len(quotes))
    market_odds = _to_market_odds(quotes)
    opportunities = scan_multi_book_arbitrage(
        market_odds,
        min_profit_percent=MIN_MARGIN_THRESHOLD,
        total_stake=TOTAL_INVESTMENT,
    )
    logger.info("Arbitrage opportunities: %d", len(opportunities))

    for opp in opportunities:
        metric = opp.get("profit_percent", opp.get("margen", 0))
        if metric < MIN_MARGIN_THRESHOLD:
            continue

        stakes_info = calculate_arbitrage_stakes(
            opp["mejores_cuotas"], TOTAL_INVESTMENT
        )
        opp["stakes"] = stakes_info.get("stakes", stakes_info)
        opp["total_stake"] = TOTAL_INVESTMENT
        if "expected_profit" not in opp and stakes_info.get("expected_profit"):
            opp["expected_profit"] = stakes_info["expected_profit"]

        saved = save_arbitrage_opportunity(opp, db_path=DB_PATH)
        logger.info("Arb saved=%s event=%s profit=%s%%", saved, opp.get("evento"), metric)

        sent = send_arbitrage_alert_telegram(
            opp, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        )
        logger.info("Arb telegram sent=%s", sent)

    return opportunities


def main() -> int:
    setup_logging()
    logger.info(
        "Pipeline start | investment=%.2f threshold=%.2f%% db=%s tg=%s",
        TOTAL_INVESTMENT,
        MIN_MARGIN_THRESHOLD,
        DB_PATH,
        bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
    )

    opportunities = run_once()

    colombia_odds = []
    for opp in opportunities:
        mejores = opp.get("mejores_cuotas") or {}
        if isinstance(mejores, dict) and "home" in mejores:
            cuota = mejores["home"].get("cuota")
            if cuota:
                colombia_odds.append(float(cuota))

    if len(colombia_odds) < 2:
        colombia_odds = [2.8, 3.0]

    consensus = calculate_market_consensus(colombia_odds)
    personal_estimate = 0.40
    logger.info("Market consensus=%.4f", consensus)

    value_bet = detect_value_bet(max(colombia_odds), consensus, personal_estimate)
    if value_bet:
        kelly = calculate_kelly_stake(
            bankroll=500,
            odds=value_bet["cuota"],
            personal_estimate=personal_estimate,
            fraction=0.5,
        )
        value_bet["stake"] = kelly["stake"]
        value_bet["evento"] = "Colombia vs Brasil"
        value_bet["casa"] = "multi"

        saved = save_value_bet(value_bet, db_path=DB_PATH)
        logger.info("Value bet saved=%s edge=%s%%", saved, value_bet.get("edge"))

        sent = send_value_bet_alert(
            value_bet, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        )
        logger.info("Value bet telegram sent=%s", sent)
    else:
        logger.info("No value bet detected")

    logger.info("Pipeline done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
