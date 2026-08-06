"""Format arbitrage opportunities into alert messages."""

from __future__ import annotations

import logging
from typing import Any

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)


def format_arbitrage_alert(opportunity: dict | ArbitrageOpportunity) -> str:
    """
    Formatea una oportunidad de arbitraje en un mensaje legible.

    opportunity: dict con info de arbitraje (evento, casas, cuotas, margen, stakes)
                 o una instancia de ArbitrageOpportunity.
    Devuelve un string listo para enviar como alerta.
    """
    if isinstance(opportunity, ArbitrageOpportunity):
        opportunity = _from_model(opportunity)

    evento = opportunity.get("evento") or opportunity.get("event_name") or "Evento desconocido"
    mercado = opportunity.get("mercado") or opportunity.get("market_type") or ""
    margen = opportunity.get("margen", 0)
    profit = opportunity.get("profit_percent", margen)
    expected = opportunity.get("expected_profit")
    total_stake = opportunity.get("total_stake") or opportunity.get("total_investment")

    casas = _format_books(opportunity)
    cuotas_block = _format_odds(opportunity)
    stakes_block = _format_stakes(opportunity.get("stakes", {}))

    lines = [
        "ARBITRAJE DETECTADO",
        f"Evento: {evento}",
    ]
    if mercado:
        lines.append(f"Mercado: {mercado}")
    if casas:
        lines.append(f"Casas: {casas}")
    if cuotas_block:
        lines.append("Cuotas:")
        lines.extend(cuotas_block)
    lines.append(f"Margen: {margen}%")
    lines.append(f"Profit: {profit}%")
    if expected is not None:
        lines.append(f"Profit esperado: {expected}")
    if total_stake is not None:
        lines.append(f"Stake total: {total_stake}")
    if stakes_block:
        lines.append("Stakes sugeridos:")
        lines.extend(stakes_block)
    lines.append("Actua rapido: las cuotas pueden cambiar.")

    msg = "\n".join(lines)
    logger.debug("Formatted alert for event=%s (%d chars)", evento, len(msg))
    return msg


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
    }


def _format_books(opportunity: dict) -> str:
    if "casas_involucradas" in opportunity:
        books = opportunity["casas_involucradas"]
        if isinstance(books, list):
            return ", ".join(str(b) for b in books)
    casas = opportunity.get("casas")
    if isinstance(casas, list):
        return ", ".join(str(b) for b in casas)
    if isinstance(casas, dict):
        return ", ".join(f"{k}={v}" for k, v in casas.items())
    return ""


def _format_odds(opportunity: dict) -> list[str]:
    lines: list[str] = []
    mejores = opportunity.get("mejores_cuotas")
    if isinstance(mejores, dict):
        for outcome, info in mejores.items():
            if isinstance(info, dict):
                casa = info.get("casa", "?")
                cuota = info.get("cuota", "?")
                lines.append(f"  - {outcome}: {casa} @ {cuota}")
            else:
                lines.append(f"  - {outcome}: {info}")
        return lines

    if isinstance(mejores, list):
        for i, cuota in enumerate(mejores, start=1):
            lines.append(f"  - resultado_{i}: {cuota}")
        return lines

    cuotas = opportunity.get("cuotas")
    if isinstance(cuotas, dict):
        for key, value in cuotas.items():
            lines.append(f"  - {key}: {value}")
    return lines


def _format_stakes(stakes: Any) -> list[str]:
    if not isinstance(stakes, dict) or not stakes:
        return []
    # Nested shape from calculate_arbitrage_stakes
    if "stakes" in stakes and isinstance(stakes["stakes"], dict):
        stakes = stakes["stakes"]
    return [f"  - {name}: {amount}" for name, amount in stakes.items()]


def format_value_bet_alert(value_bet: dict) -> str:
    """
    Formatea una value bet en un mensaje legible para Telegram.

    value_bet: dict con cuota, probabilidades, edge, stake (opcional).
    """
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
    lines.append(f"Stake recomendado: {stake if stake is not None else 'N/A'}")

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
