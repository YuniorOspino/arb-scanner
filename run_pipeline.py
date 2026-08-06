"""Demo pipeline: arbitrage scan + value bet -> storage + Telegram."""

from __future__ import annotations

import logging
import sys

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
from storage.arb_history import save_arbitrage_opportunity
from storage.value_bet_history import save_value_bet

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    logger.info(
        "Pipeline start | investment=%.2f threshold=%.2f%% db=%s tg=%s",
        TOTAL_INVESTMENT,
        MIN_MARGIN_THRESHOLD,
        DB_PATH,
        bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
    )

    # Datos simulados (Brasil ajustado para que exista arb demo > 1.5%)
    market_odds = {
        "Colombia vs Brasil": {
            "Colombia": {"CasaA": 2.8, "CasaB": 3.0},
            "Empate": {"CasaA": 3.2, "CasaB": 3.4},
            "Brasil": {"CasaA": 2.5, "CasaB": 2.8},
        }
    }

    # --- Escaneo de arbitraje ---
    opportunities = scan_multi_book_arbitrage(
        market_odds,
        min_profit_percent=MIN_MARGIN_THRESHOLD,
        total_stake=TOTAL_INVESTMENT,
    )
    logger.info("Arbitrage opportunities: %d", len(opportunities))

    for opp in opportunities:
        metric = opp.get("profit_percent", opp.get("margen", 0))
        if metric < MIN_MARGIN_THRESHOLD:
            logger.debug("Skip opp below threshold: %s", opp.get("evento"))
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

    # --- Ejemplo de Value Bet ---
    odds_list = [2.8, 3.0]  # cuotas de Colombia
    consensus = calculate_market_consensus(odds_list)
    personal_estimate = 0.40  # modelo: 40% probabilidad
    logger.info("Market consensus (Colombia)=%.4f", consensus)

    value_bet = detect_value_bet(3.0, consensus, personal_estimate)
    if value_bet:
        kelly = calculate_kelly_stake(
            bankroll=500,
            odds=value_bet["cuota"],
            personal_estimate=personal_estimate,
            fraction=0.5,
        )
        value_bet["stake"] = kelly["stake"]
        value_bet["evento"] = "Colombia vs Brasil"
        value_bet["casa"] = "CasaB"

        saved = save_value_bet(value_bet, db_path=DB_PATH)
        logger.info("Value bet saved=%s edge=%s%%", saved, value_bet.get("edge"))

        sent = send_value_bet_alert(
            value_bet, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        )
        logger.info("Value bet telegram sent=%s", sent)
    else:
        logger.info("No value bet for Colombia @ 3.0 with estimate=%.2f", personal_estimate)

    logger.info("Pipeline done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
