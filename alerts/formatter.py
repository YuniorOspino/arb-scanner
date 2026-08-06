"""Format arbitrage opportunities into alert messages."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

_OUTCOME_ALIASES = {
    "home": "home",
    "1": "home",
    "local": "home",
    "draw": "draw",
    "x": "draw",
    "empate": "draw",
    "away": "away",
    "2": "away",
    "visitante": "away",
}


def format_arbitrage_alert(opportunity: dict | ArbitrageOpportunity) -> str:
    if isinstance(opportunity, ArbitrageOpportunity):
        opportunity = _from_model(opportunity)

    evento = (
        opportunity.get("evento")
        or opportunity.get("event_name")
        or "Evento desconocido"
    )
    equipo_local, equipo_visitante = _split_teams(str(evento))
    profit = opportunity.get("profit_percent", opportunity.get("margen", 0))

    legs = _resolve_legs(opportunity)
    casas = _unique_books(legs) or _format_books(opportunity)

    lines = [
        "ARBITRAJE DETECTADO",
        f"Evento: {evento}",
        f"Casas: {casas}",
        "👉 Apuesta paso a paso:",
    ]

    home = legs.get("home")
    draw = legs.get("draw")
    away = legs.get("away")

    if home:
        lines.append(
            f"- En {_display_book(home['casa'])} pon {_format_cop(home['stake'])} COP "
            f"a que gana {equipo_local}"
        )
    if draw:
        lines.append(
            f"- En {_display_book(draw['casa'])} pon {_format_cop(draw['stake'])} COP "
            f"al empate"
        )
    if away:
        lines.append(
            f"- En {_display_book(away['casa'])} pon {_format_cop(away['stake'])} COP "
            f"a que gana {equipo_visitante}"
        )

    for key, leg in legs.items():
        if key in {"home", "draw", "away"}:
            continue
        lines.append(
            f"- En {_display_book(leg['casa'])} pon {_format_cop(leg['stake'])} COP a {key}"
        )

    lines.append(f"Profit esperado: {float(profit):.2f}%")

    msg = "\n".join(lines)
    logger.debug("Formatted alert for event=%s (%d chars)", evento, len(msg))
    return msg


def _format_cop(amount: float | int) -> str:
    value = int(round(float(amount) / 10.0) * 10)
    return f"{value:,}"


def _display_book(name: str) -> str:
    mapping = {
        "rushbet": "RushBet",
        "codere": "Codere",
        "zamba": "Zamba",
        "betano": "Betano",
        "betplay": "BetPlay",
        "wplay": "Wplay",
    }
    key = str(name).strip().lower()
    return mapping.get(key, str(name).strip().title())


def _split_teams(evento: str) -> tuple[str, str]:
    parts = re.split(r"\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+", evento, flags=re.IGNORECASE)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "equipo local", "equipo visitante"


def _normalize_outcome(key: str) -> str:
    return _OUTCOME_ALIASES.get(str(key).strip().lower(), str(key).strip().lower())


def _resolve_legs(opportunity: dict) -> dict[str, dict[str, Any]]:
    """Build {home|draw|away: {casa, cuota, stake}} from opportunity fields."""
    from config import TOTAL_INVESTMENT
    from core.arbitrage import calculate_arbitrage_stakes

    mejores = opportunity.get("mejores_cuotas") or {}
    stakes_raw = opportunity.get("stakes") or {}
    if isinstance(stakes_raw, dict) and "stakes" in stakes_raw:
        stakes_raw = stakes_raw["stakes"]

    total = float(
        opportunity.get("total_stake")
        or opportunity.get("total_investment")
        or TOTAL_INVESTMENT
    )

    legs: dict[str, dict[str, Any]] = {}

    if isinstance(mejores, dict) and mejores:
        sample = next(iter(mejores.values()), None)
        if isinstance(sample, dict) and "casa" in sample:
            for outcome, info in mejores.items():
                key = _normalize_outcome(str(outcome))
                if not isinstance(info, dict):
                    continue
                legs[key] = {
                    "casa": str(info.get("casa", "?")),
                    "cuota": float(info.get("cuota", 0) or 0),
                    "stake": 0.0,
                }

    if not legs and isinstance(opportunity.get("legs"), (list, tuple)):
        for book, outcome, odds, stake in opportunity["legs"]:
            key = _normalize_outcome(str(outcome))
            legs[key] = {
                "casa": str(book),
                "cuota": float(odds),
                "stake": float(stake),
            }

    if isinstance(stakes_raw, dict) and stakes_raw:
        for outcome, stake in stakes_raw.items():
            key = _normalize_outcome(str(outcome))
            if key in legs:
                legs[key]["stake"] = float(stake)
            else:
                legs[key] = {"casa": "?", "cuota": 0.0, "stake": float(stake)}

    needs_calc = bool(legs) and any(float(leg.get("stake") or 0) <= 0 for leg in legs.values())
    if needs_calc:
        odds_map = {k: v["cuota"] for k, v in legs.items() if float(v.get("cuota") or 0) > 1}
        if len(odds_map) >= 2:
            calc = calculate_arbitrage_stakes(
                odds_map, total, labels=list(odds_map.keys())
            )
            for key, stake in calc.get("stakes", {}).items():
                if key in legs:
                    legs[key]["stake"] = float(stake)

    return legs


def _unique_books(legs: dict[str, dict[str, Any]]) -> str:
    books = []
    seen = set()
    for leg in legs.values():
        casa = str(leg.get("casa", ""))
        if casa and casa not in seen and casa != "?":
            seen.add(casa)
            books.append(_display_book(casa))
    return ", ".join(books)


def _from_model(opp: ArbitrageOpportunity) -> dict[str, Any]:
    return {
        "evento": opp.event_name,
        "market_type": opp.market_type,
        "margen": opp.profit_percent,
        "profit_percent": opp.profit_percent,
        "expected_profit": round(opp.expected_profit, 2),
        "total_stake": opp.total_stake,
        "casas_involucradas": [leg[0] for leg in opp.legs],
        "mejores_cuotas": {
            outcome: {"casa": book, "cuota": odds}
            for book, outcome, odds, _stake in opp.legs
        },
        "stakes": {outcome: stake for _book, outcome, _odds, stake in opp.legs},
        "legs": opp.legs,
    }


def _format_books(opportunity: dict) -> str:
    if "casas_involucradas" in opportunity:
        books = opportunity["casas_involucradas"]
        if isinstance(books, list):
            return ", ".join(_display_book(str(b)) for b in books)
    casas = opportunity.get("casas")
    if isinstance(casas, list):
        return ", ".join(_display_book(str(b)) for b in casas)
    if isinstance(casas, dict):
        return ", ".join(
            f"{k}={_display_book(str(v))}" for k, v in casas.items()
        )
    return ""


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
        lines.append(f"Stake recomendado: {_format_cop(stake)} COP")
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
