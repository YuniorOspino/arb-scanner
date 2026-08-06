"""Format arbitrage opportunities into actionable Telegram alerts."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

_OUTCOME_LABELS = {
    "home": "Local (1)",
    "1": "Local (1)",
    "local": "Local (1)",
    "draw": "Empate (X)",
    "x": "Empate (X)",
    "empate": "Empate (X)",
    "away": "Visitante (2)",
    "2": "Visitante (2)",
    "visitante": "Visitante (2)",
}

_BOOK_LINKS = {
    "betano": "https://www.betano.co/sport/futbol/",
    "betplay": "https://betplay.com.co/apuestas",
    "wplay": "https://apuestas.wplay.co/es/s/FOOT/F%C3%BAtbol",
    "rushbet": "https://www.rushbet.co/?page=sportsbook",
    "zamba": "https://www.zamba.co/deportes",
    "codere": "https://m.codere.com.co/deportesCol/",
}

_BOOK_DISPLAY = {
    "rushbet": "RushBet",
    "codere": "Codere",
    "zamba": "Zamba",
    "betano": "Betano",
    "betplay": "BetPlay",
    "wplay": "Wplay",
}


def _exact(value: Any) -> str:
    """Render scanner numeric values without extra rounding."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def _display_book(name: str) -> str:
    key = str(name).strip().lower()
    return _BOOK_DISPLAY.get(key, str(name).strip())


def _selection_label(outcome: str, event_name: str) -> str:
    key = str(outcome).strip().lower()
    base = _OUTCOME_LABELS.get(key)
    if base is None:
        return str(outcome)
    local, away = _split_teams(event_name)
    if key in {"home", "1", "local"}:
        return f"{base} — {local}"
    if key in {"away", "2", "visitante"}:
        return f"{base} — {away}"
    return base


def _split_teams(evento: str) -> tuple[str, str]:
    parts = re.split(r"\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+", evento, flags=re.IGNORECASE)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "equipo local", "equipo visitante"


def _sport_for_market(market_type: str) -> str:
    market = str(market_type or "").upper()
    if market in {"1X2", "MRES", "MR", "MATCH_RESULT"}:
        return "Futbol"
    return "Desconocido"


def _capture_time(opportunity: ArbitrageOpportunity) -> str:
    ts = opportunity.detected_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _market_link(bookmaker: str) -> str:
    return _BOOK_LINKS.get(str(bookmaker).strip().lower(), "No disponible")


def format_arbitrage_alert(opportunity: dict | ArbitrageOpportunity) -> str:
    """Build Telegram text using exact scanner opportunity fields only."""
    if not isinstance(opportunity, ArbitrageOpportunity):
        opportunity = opportunity_from_payload(opportunity)

    expected_profit = opportunity.expected_profit
    expected_return = opportunity.total_stake + expected_profit

    lines = [
        "ARBITRAJE DETECTADO",
        "",
        f"Partido: {opportunity.event_name}",
        f"Deporte: {_sport_for_market(opportunity.market_type)}",
        f"Mercado: {opportunity.market_type}",
        f"Hora de captura: {_capture_time(opportunity)}",
        f"Ganancia estimada: {_exact(opportunity.profit_percent)}%",
        "",
        "APUESTAS (datos exactos del scanner):",
    ]

    for idx, (bookmaker, outcome, odds, stake) in enumerate(opportunity.legs, start=1):
        lines.extend(
            [
                f"{idx})",
                f"Casa de apuestas: {_display_book(bookmaker)}",
                f"Selección: {_selection_label(outcome, opportunity.event_name)}",
                f"Cuota exacta: {_exact(odds)}",
                f"Stake sugerido: {_exact(stake)}",
                f"Link: {_market_link(bookmaker)}",
                "",
            ]
        )

    lines.append("Distribución sugerida del dinero:")
    for bookmaker, outcome, odds, stake in opportunity.legs:
        lines.append(
            f"- {_display_book(bookmaker)} / {_selection_label(outcome, opportunity.event_name)}: "
            f"{_exact(stake)}"
        )

    lines.extend(
        [
            "",
            f"Stake total: {_exact(opportunity.total_stake)}",
            f"Retorno esperado: {_exact(expected_return)}",
            f"Ganancia esperada: {_exact(expected_profit)}",
        ]
    )

    msg = "\n".join(lines)
    logger.debug(
        "Formatted alert for event=%s (%d chars)",
        opportunity.event_name,
        len(msg),
    )
    return msg


def opportunity_from_payload(opportunity: dict[str, Any]) -> ArbitrageOpportunity:
    """Adapt legacy dict opportunities without recalculating stakes/odds."""
    event_name = str(
        opportunity.get("evento")
        or opportunity.get("event_name")
        or "Evento desconocido"
    )
    market_type = str(opportunity.get("market_type") or "1X2")
    profit = float(
        opportunity.get("profit_percent", opportunity.get("margen", 0)) or 0
    )
    total_stake = float(
        opportunity.get("total_stake")
        or opportunity.get("total_investment")
        or 0
    )

    legs_raw = opportunity.get("legs")
    legs: list[tuple[str, str, float, float]] = []
    if isinstance(legs_raw, (list, tuple)) and legs_raw:
        for item in legs_raw:
            if isinstance(item, (list, tuple)) and len(item) >= 4:
                book, outcome, odds, stake = item[:4]
                legs.append((str(book), str(outcome), float(odds), float(stake)))
    else:
        mejores = opportunity.get("mejores_cuotas") or {}
        stakes = opportunity.get("stakes") or {}
        if isinstance(stakes, dict) and "stakes" in stakes:
            stakes = stakes["stakes"]
        if isinstance(mejores, dict):
            for outcome, info in mejores.items():
                if not isinstance(info, dict):
                    continue
                book = str(info.get("casa", "?"))
                odds = float(info.get("cuota", 0) or 0)
                stake = float(
                    stakes.get(outcome, stakes.get(str(outcome).lower(), 0)) or 0
                )
                if odds > 1.0:
                    legs.append((book, str(outcome), odds, stake))

    detected = opportunity.get("detected_at")
    if not isinstance(detected, datetime):
        detected = datetime.now()

    return ArbitrageOpportunity(
        event_name=event_name,
        market_type=market_type,
        profit_percent=profit,
        total_stake=total_stake,
        legs=tuple(legs),
        detected_at=detected,
    )


def format_value_bet_alert(value_bet: dict) -> str:
    lines = [
        "VALUE BET DETECTADO",
        f"Cuota: {value_bet.get('cuota', 'N/A')}",
        f"Prob. implicita: {value_bet.get('probabilidad_implicita', 'N/A')}",
        f"Prob. mercado: {value_bet.get('probabilidad_mercado', 'N/A')}",
        f"Prob. personal: {value_bet.get('probabilidad_personal', 'N/A')}",
        f"Edge: {value_bet.get('edge', 'N/A')}%",
    ]
    if "expected_value" in value_bet:
        lines.append(f"EV: {value_bet.get('expected_value')}")
    if "vs_market_edge" in value_bet:
        lines.append(f"Edge vs mercado: {value_bet.get('vs_market_edge')}%")

    stake = value_bet.get("stake")
    if stake is None and isinstance(value_bet.get("kelly"), dict):
        stake = value_bet["kelly"].get("stake")
    if stake is not None:
        lines.append(f"Stake recomendado: {_exact(stake)}")
    else:
        lines.append("Stake recomendado: N/A")

    if value_bet.get("evento") or value_bet.get("event_name"):
        lines.insert(1, f"Evento: {value_bet.get('evento') or value_bet.get('event_name')}")
    if value_bet.get("casa") or value_bet.get("bookmaker"):
        lines.insert(
            2 if (value_bet.get("evento") or value_bet.get("event_name")) else 1,
            f"Casa: {value_bet.get('casa') or value_bet.get('bookmaker')}",
        )

    lines.append("Apuesta con ventaja matematica estimada.")
    msg = "\n".join(lines)
    logger.debug("Formatted value-bet alert (%d chars)", len(msg))
    return msg
