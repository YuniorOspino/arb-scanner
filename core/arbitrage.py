"""Arbitrage math: implied probabilities and stake sizing."""

from __future__ import annotations

import logging

from core.models import ArbitrageOpportunity, MarketOdds, OddsQuote

logger = logging.getLogger(__name__)


def implied_probability(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds <= 1.0:
        raise ValueError(f"Invalid odds: {odds}")
    return 1.0 / odds


def calculate_kelly_stake(
    bankroll: float,
    odds: float,
    personal_estimate: float,
    *,
    fraction: float = 1.0,
) -> dict:
    """
    Calcula el stake optimo usando el criterio de Kelly.

    bankroll: capital total disponible
    odds: cuota ofrecida por la casa (decimal)
    personal_estimate: probabilidad estimada por tu modelo (0..1)
    fraction: Kelly fraccional (ej: 0.5 = half-Kelly). Full Kelly es agresivo.

    Devuelve dict con stake recomendado y fraccion de Kelly.
    """
    empty = {
        "stake": 0.0,
        "kelly_fraction": 0.0,
        "full_kelly_fraction": 0.0,
        "edge": 0.0,
        "recommended": False,
    }

    try:
        bank = float(bankroll)
        cuota = float(odds)
        p = float(personal_estimate)
        frac = float(fraction)
    except (TypeError, ValueError):
        logger.error(
            "calculate_kelly_stake: invalid inputs bankroll=%r odds=%r p=%r",
            bankroll,
            odds,
            personal_estimate,
        )
        return empty

    if bank <= 0 or cuota <= 1.0 or not 0.0 < p < 1.0 or frac <= 0:
        logger.debug("calculate_kelly_stake: invalid params, stake=0")
        return empty

    b = cuota - 1.0
    q = 1.0 - p
    # f* = (b*p - q) / b
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        logger.debug("calculate_kelly_stake: negative EV, no bet")
        return empty

    kelly_fraction = full_kelly * frac
    # Cap at 100% bankroll for safety
    kelly_fraction = min(kelly_fraction, 1.0)
    stake = round(bank * kelly_fraction, 2)
    edge = p * cuota - 1.0

    result = {
        "stake": stake,
        "kelly_fraction": round(kelly_fraction, 4),
        "full_kelly_fraction": round(full_kelly, 4),
        "edge": round(edge, 4),
        "recommended": True,
        "bankroll": bank,
        "odds": cuota,
        "personal_estimate": p,
    }
    logger.info(
        "Kelly stake: %.2f (%.2f%% of bankroll, fraction=%.2f)",
        stake,
        kelly_fraction * 100.0,
        frac,
    )
    return result


def detect_value_bet(
    odds: float,
    market_consensus: float,
    personal_estimate: float,
    *,
    min_edge_percent: float = 0.0,
) -> dict | None:
    """
    Detecta si existe una apuesta de valor (value bet).

    odds: cuota ofrecida por la casa
    market_consensus: probabilidad implicita promedio del mercado
    personal_estimate: probabilidad estimada por tu modelo (0..1)
    min_edge_percent: umbral minimo de edge (%) para considerar value

    Devuelve dict con info de value bet si existe, o None si no hay.
    """
    try:
        cuota = float(odds)
        consensus = float(market_consensus)
        estimate = float(personal_estimate)
    except (TypeError, ValueError):
        logger.error(
            "detect_value_bet: invalid inputs odds=%r consensus=%r estimate=%r",
            odds,
            market_consensus,
            personal_estimate,
        )
        return None

    if cuota <= 1.0:
        logger.debug("detect_value_bet: odds must be > 1.0")
        return None
    if not 0.0 < estimate < 1.0:
        logger.error("detect_value_bet: personal_estimate must be in (0, 1)")
        return None

    implied_prob = 1.0 / cuota
    # Value: tu estimacion supera la probabilidad implicita de la cuota
    if estimate <= implied_prob:
        logger.debug(
            "detect_value_bet: no value estimate=%.3f <= implied=%.3f",
            estimate,
            implied_prob,
        )
        return None

    edge = (estimate - implied_prob) * 100.0
    if edge < min_edge_percent:
        logger.debug(
            "detect_value_bet: edge %.2f%% below threshold %.2f%%",
            edge,
            min_edge_percent,
        )
        return None

    # EV esperado por unidad apostada: estimate * odds - 1
    expected_value = estimate * cuota - 1.0

    result = {
        "cuota": cuota,
        "probabilidad_implicita": round(implied_prob, 4),
        "probabilidad_mercado": round(consensus, 4),
        "probabilidad_personal": round(estimate, 4),
        "valor_detectado": True,
        "edge": round(edge, 2),
        "expected_value": round(expected_value, 4),
        "vs_market_edge": round((estimate - consensus) * 100.0, 2),
    }
    logger.info(
        "Value bet: odds=%.3f edge=%.2f%% EV=%.4f",
        cuota,
        edge,
        expected_value,
    )
    return result


def calculate_market_consensus(odds_list: list[float]) -> float:
    """
    Calcula el consenso de probabilidad implicita del mercado
    a partir de las cuotas de varias casas para el MISMO resultado.

    odds_list: lista de cuotas para el mismo resultado (ej: "Colombia gana")

    Nota: normalizar probs del mismo outcome y promediar siempre da 1/n.
    El consenso correcto es el promedio de las probabilidades implicitas
    (sin normalizar entre casas). Para quitar overround usa normalize_odds()
    sobre un mercado completo (todos los outcomes de una casa).
    """
    probabilities: list[float] = []
    for odd in odds_list:
        try:
            v = float(odd)
        except (TypeError, ValueError):
            logger.error("calculate_market_consensus: invalid odd %r", odd)
            continue
        if v > 1.0:
            probabilities.append(1.0 / v)

    if not probabilities:
        logger.error("calculate_market_consensus: no valid odds")
        return 0.0

    consensus_probability = sum(probabilities) / len(probabilities)
    logger.debug(
        "calculate_market_consensus: n=%d consensus=%.4f (from odds=%s)",
        len(probabilities),
        consensus_probability,
        odds_list,
    )
    return consensus_probability


def calculate_arbitrage_stakes(
    odds: list[float] | dict,
    total_investment: float,
    *,
    labels: list[str] | None = None,
) -> dict:
    """
    Calcula los stakes exactos para cada resultado en un arbitraje.

    odds: lista de cuotas, o dict mejores_cuotas {outcome: {casa, cuota}} / {outcome: cuota}
    total_investment: monto total que se quiere invertir
    labels: nombres opcionales por outcome; default resultado_1, resultado_2, ...

    Devuelve dict con {resultado: stake_asignado}, mas metadatos de profit.
    """
    if total_investment <= 0:
        logger.error("calculate_arbitrage_stakes: total_investment must be > 0")
        return {}

    # Accept scan_multi_book_arbitrage["mejores_cuotas"] shape
    if isinstance(odds, dict):
        parsed_labels: list[str] = []
        parsed_odds: list[float] = []
        for outcome, info in odds.items():
            if isinstance(info, dict) and "cuota" in info:
                parsed_odds.append(float(info["cuota"]))
            else:
                parsed_odds.append(float(info))
            parsed_labels.append(str(outcome))
        odds = parsed_odds
        if labels is None:
            labels = parsed_labels

    clean: list[float] = []
    for o in odds:
        try:
            v = float(o)
        except (TypeError, ValueError):
            logger.error("calculate_arbitrage_stakes: invalid odd %r", o)
            return {}
        if v <= 1.0:
            logger.error("calculate_arbitrage_stakes: odds must be > 1.0, got %s", v)
            return {}
        clean.append(v)

    if len(clean) < 2:
        logger.error("calculate_arbitrage_stakes: need at least 2 odds")
        return {}

    inv_sum = sum(1.0 / o for o in clean)
    if inv_sum <= 0:
        logger.error("calculate_arbitrage_stakes: invalid inv_sum")
        return {}

    if labels is not None and len(labels) != len(clean):
        logger.error("calculate_arbitrage_stakes: labels length must match odds")
        return {}

    stakes: dict[str, float] = {}
    for i, odd in enumerate(clean):
        key = labels[i] if labels else f"resultado_{i + 1}"
        stake = (total_investment * (1.0 / odd)) / inv_sum
        stakes[key] = round(stake, 2)

    profit_percent = (1.0 / inv_sum - 1.0) * 100.0 if inv_sum < 1.0 else 0.0
    logger.debug(
        "calculate_arbitrage_stakes: inv_sum=%.4f profit=%.2f%% stakes=%s",
        inv_sum,
        profit_percent,
        stakes,
    )

    return {
        "stakes": stakes,
        "total_investment": total_investment,
        "inv_sum": round(inv_sum, 6),
        "is_arbitrage": inv_sum < 1.0,
        "profit_percent": round(profit_percent, 2) if inv_sum < 1.0 else 0.0,
        "expected_profit": (
            round(total_investment * profit_percent / 100.0, 2)
            if inv_sum < 1.0
            else 0.0
        ),
        "payout_per_outcome": {
            k: round(stakes[k] * clean[i], 2) for i, k in enumerate(stakes)
        },
    }


def detect_three_way_arbitrage(
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    *,
    book_home: str = "Casa Home",
    book_draw: str = "Casa Draw",
    book_away: str = "Casa Away",
    total_stake: float = 100.0,
) -> dict | None:
    """
    Detecta arbitraje en un mercado de tres resultados
    (ej: futbol: local, empate, visitante).

    Devuelve dict con info de arbitraje si existe, o None si no hay.
    """
    try:
        home = float(odds_home)
        draw = float(odds_draw)
        away = float(odds_away)
    except (TypeError, ValueError):
        logger.error(
            "detect_three_way_arbitrage: invalid odds %r / %r / %r",
            odds_home,
            odds_draw,
            odds_away,
        )
        return None

    if home <= 1.0 or draw <= 1.0 or away <= 1.0:
        logger.debug("detect_three_way_arbitrage: odds must be > 1.0")
        return None

    inv_sum = (1.0 / home) + (1.0 / draw) + (1.0 / away)
    if inv_sum >= 1.0:
        logger.debug("detect_three_way_arbitrage: no arb inv_sum=%.4f", inv_sum)
        return None

    profit_percent = (1.0 / inv_sum - 1.0) * 100.0
    stakes = {
        "home": round(total_stake * (1.0 / home) / inv_sum, 2),
        "draw": round(total_stake * (1.0 / draw) / inv_sum, 2),
        "away": round(total_stake * (1.0 / away) / inv_sum, 2),
    }

    result = {
        "mercado": "3-way",
        "casas": {
            "home": book_home,
            "draw": book_draw,
            "away": book_away,
        },
        "cuotas": {"home": home, "draw": draw, "away": away},
        "margen": round((1.0 - inv_sum) * 100.0, 2),
        "profit_percent": round(profit_percent, 2),
        "stakes": stakes,
        "total_stake": total_stake,
        "expected_profit": round(total_stake * profit_percent / 100.0, 2),
    }
    logger.info(
        "Three-way arb: %s@%.3f / %s@%.3f / %s@%.3f | profit=%.2f%%",
        book_home,
        home,
        book_draw,
        draw,
        book_away,
        away,
        profit_percent,
    )
    return result


def detect_two_way_arbitrage(
    odds_a: float,
    odds_b: float,
    *,
    book_a: str = "Casa A",
    book_b: str = "Casa B",
    total_stake: float = 100.0,
) -> dict | None:
    """
    Detecta arbitraje en un mercado de dos resultados
    (ej: tenis, baloncesto sin empate).

    odds_a / odds_b: cuotas decimales de cada casa.
    Devuelve dict con info de arbitraje si existe, o None si no hay.
    """
    try:
        a = float(odds_a)
        b = float(odds_b)
    except (TypeError, ValueError):
        logger.error("detect_two_way_arbitrage: invalid odds %r / %r", odds_a, odds_b)
        return None

    if a <= 1.0 or b <= 1.0:
        logger.debug("detect_two_way_arbitrage: odds must be > 1.0")
        return None

    inv_sum = (1.0 / a) + (1.0 / b)
    if inv_sum >= 1.0:
        logger.debug("detect_two_way_arbitrage: no arb inv_sum=%.4f", inv_sum)
        return None

    profit_percent = (1.0 / inv_sum - 1.0) * 100.0
    stake_a = round(total_stake * (1.0 / a) / inv_sum, 2)
    stake_b = round(total_stake * (1.0 / b) / inv_sum, 2)

    result = {
        "casas": [book_a, book_b],
        "cuotas": {"A": a, "B": b},
        "margen": round((1.0 - inv_sum) * 100.0, 2),
        "profit_percent": round(profit_percent, 2),
        "stakes": {"A": stake_a, "B": stake_b},
        "total_stake": total_stake,
        "expected_profit": round(total_stake * profit_percent / 100.0, 2),
    }
    logger.info(
        "Two-way arb: %s@%.3f / %s@%.3f | profit=%.2f%%",
        book_a,
        a,
        book_b,
        b,
        profit_percent,
    )
    return result


def normalize_odds(odds: dict) -> dict:
    """
    Convierte cuotas en probabilidades implícitas y las normaliza
    para quitar el margen de la casa (overround).

    odds: dict con {casa: cuota}  (o {outcome: cuota})
    Devuelve dict con {casa: probabilidad_normalizada} (suman 1.0).
    """
    probabilities = {
        book: 1.0 / float(o) for book, o in odds.items() if float(o) > 0
    }
    if not probabilities:
        logger.error("normalize_odds: no valid odds in input")
        return {}

    total = sum(probabilities.values())
    if total <= 0:
        logger.error("normalize_odds: invalid probability total=%s", total)
        return {}

    normalized = {book: p / total for book, p in probabilities.items()}
    overround = (total - 1.0) * 100.0
    logger.debug(
        "normalize_odds: overround=%.2f%% books=%d",
        overround,
        len(normalized),
    )
    return normalized


def calculate_arbitrage(
    quotes: list[OddsQuote],
    total_stake: float,
    min_profit_percent: float = 0.0,
    market_type: str = "multi-outcome",
) -> ArbitrageOpportunity | None:
    """
    Given one quote per mutually exclusive outcome, detect arb and size stakes.

    Returns None if no arb above min_profit_percent.
    """
    if len(quotes) < 2:
        logger.debug("Need at least 2 outcomes for arbitrage, got %d", len(quotes))
        return None

    outcomes = {q.outcome for q in quotes}
    if len(outcomes) != len(quotes):
        logger.debug("Duplicate outcomes in quote set; skipping")
        return None

    inv_sum = sum(implied_probability(q.odds) for q in quotes)
    if inv_sum >= 1.0:
        logger.debug(
            "No arb: inv_sum=%.4f for event=%s",
            inv_sum,
            quotes[0].event_name,
        )
        return None

    profit_percent = (1.0 / inv_sum - 1.0) * 100.0
    if profit_percent < min_profit_percent:
        logger.debug(
            "Arb below threshold: %.3f%% < %.3f%%",
            profit_percent,
            min_profit_percent,
        )
        return None

    legs: list[tuple[str, str, float, float]] = []
    for q in quotes:
        stake = total_stake * implied_probability(q.odds) / inv_sum
        legs.append((q.bookmaker, q.outcome, q.odds, round(stake, 2)))

    opportunity = ArbitrageOpportunity(
        event_name=quotes[0].event_name,
        market_type=market_type,
        profit_percent=round(profit_percent, 4),
        total_stake=total_stake,
        legs=tuple(legs),
    )
    logger.info(
        "Arbitrage found: %s | %.2f%% profit",
        opportunity.event_name,
        opportunity.profit_percent,
    )
    return opportunity


def best_quotes_per_outcome(market: MarketOdds) -> list[OddsQuote]:
    """Pick the highest odds available for each outcome."""
    best: list[OddsQuote] = []
    for outcome, quotes in market.quotes_by_outcome().items():
        if not quotes:
            continue
        top = max(quotes, key=lambda q: q.odds)
        best.append(top)
        logger.debug(
            "Best %s for %s: %s @ %.3f",
            outcome,
            market.event_name,
            top.bookmaker,
            top.odds,
        )
    return best


def find_opportunities(
    markets: list[MarketOdds],
    total_stake: float,
    min_profit_percent: float,
) -> list[ArbitrageOpportunity]:
    """Scan markets and return all arbs above the profit threshold."""
    found: list[ArbitrageOpportunity] = []

    for market in markets:
        best = best_quotes_per_outcome(market)
        if len(best) < 2:
            continue

        opp = calculate_arbitrage(
            best,
            total_stake=total_stake,
            min_profit_percent=min_profit_percent,
            market_type=market.market_type,
        )
        if opp is not None:
            found.append(opp)

    logger.info("Found %d arbitrage opportunities", len(found))
    return found


def scan_multi_book_arbitrage(
    market_odds: dict,
    *,
    min_profit_percent: float = 0.0,
    total_stake: float = 100.0,
) -> list[dict]:
    """
    Escanea oportunidades de arbitraje en mercados con multiples casas.

    market_odds: dict con estructura:
        {
            "evento": {
                "resultado_1": {"casaA": cuota, "casaB": cuota, ...},
                "resultado_2": {...},
                ...
            }
        }

    Para cada resultado toma la mejor cuota disponible y evalúa arb.
    Devuelve lista de oportunidades detectadas.
    """
    opportunities: list[dict] = []

    for event, outcomes in market_odds.items():
        if not isinstance(outcomes, dict) or not outcomes:
            logger.debug("scan_multi_book: skip empty event=%s", event)
            continue

        best_by_outcome: dict[str, tuple[str, float]] = {}
        for outcome, books in outcomes.items():
            if not isinstance(books, dict) or not books:
                continue
            valid = {
                str(casa): float(cuota)
                for casa, cuota in books.items()
                if float(cuota) > 1.0
            }
            if not valid:
                continue
            best_book = max(valid, key=valid.get)
            best_by_outcome[str(outcome)] = (best_book, valid[best_book])

        if len(best_by_outcome) < 2:
            logger.debug(
                "scan_multi_book: need >= 2 outcomes for %s, got %d",
                event,
                len(best_by_outcome),
            )
            continue

        inv_sum = sum(1.0 / odds for _, odds in best_by_outcome.values())
        if inv_sum >= 1.0:
            logger.debug("scan_multi_book: no arb for %s inv_sum=%.4f", event, inv_sum)
            continue

        profit_percent = (1.0 / inv_sum - 1.0) * 100.0
        if profit_percent < min_profit_percent:
            logger.debug(
                "scan_multi_book: %s below threshold %.3f%% < %.3f%%",
                event,
                profit_percent,
                min_profit_percent,
            )
            continue

        mejores_cuotas = {
            outcome: {"casa": book, "cuota": odds}
            for outcome, (book, odds) in best_by_outcome.items()
        }
        stakes = {
            outcome: round(total_stake * (1.0 / odds) / inv_sum, 2)
            for outcome, (_book, odds) in best_by_outcome.items()
        }

        opportunities.append(
            {
                "evento": event,
                "mejores_cuotas": mejores_cuotas,
                "casas_involucradas": [
                    book for book, _odds in best_by_outcome.values()
                ],
                "margen": round((1.0 - inv_sum) * 100.0, 2),
                "profit_percent": round(profit_percent, 2),
                "stakes": stakes,
                "total_stake": total_stake,
                "expected_profit": round(total_stake * profit_percent / 100.0, 2),
            }
        )
        logger.info(
            "scan_multi_book: %s | profit=%.2f%% books=%s",
            event,
            profit_percent,
            [b for b, _ in best_by_outcome.values()],
        )

    logger.info("scan_multi_book_arbitrage: %d opportunities", len(opportunities))
    return opportunities


def scan_arbitrage_opportunities(
    market_data: dict,
    min_profit_percent: float = 0.0,
) -> list[dict]:
    """
    Escanea oportunidades de arbitraje en un mercado dado.

    market_data: dict con cuotas de distintas casas para un mismo evento.
      Formato simple (una cuota por casa = un outcome distinto):
        {
          "Team A vs Team B": {"draftkings": 2.25, "fanduel": 3.90}
        }
      Formato por outcome (recomendado; se toma la mejor cuota por outcome):
        {
          "Team A vs Team B": {
            "home": {"draftkings": 2.25, "bet365": 2.10},
            "draw": {"draftkings": 3.50},
            "away": {"fanduel": 3.90},
          }
        }

    Devuelve una lista de oportunidades detectadas (dicts).
    """
    opportunities: list[dict] = []

    for event, odds in market_data.items():
        if not isinstance(odds, dict) or not odds:
            logger.debug("Skipping empty/invalid odds for event=%s", event)
            continue

        try:
            legs = _normalize_legs(event, odds)
        except ValueError as exc:
            logger.error("Invalid market_data for %s: %s", event, exc)
            continue

        if len(legs) < 2:
            logger.debug("Need >= 2 outcomes for %s, got %d", event, len(legs))
            continue

        inv_sum = sum(1.0 / cuota for _, _, cuota in legs)
        if inv_sum >= 1.0:
            logger.debug("No arb for %s: inv_sum=%.4f", event, inv_sum)
            continue

        # Profit garantizado = (1/inv_sum - 1) * 100  (no 1 - inv_sum)
        profit_percent = (1.0 / inv_sum - 1.0) * 100.0
        if profit_percent < min_profit_percent:
            logger.debug(
                "Arb below threshold for %s: %.3f%% < %.3f%%",
                event,
                profit_percent,
                min_profit_percent,
            )
            continue

        cuotas = {f"{casa}:{outcome}": cuota for casa, outcome, cuota in legs}
        opportunities.append(
            {
                "evento": event,
                "casas": [casa for casa, _, _ in legs],
                "outcomes": [outcome for _, outcome, _ in legs],
                "cuotas": cuotas,
                "margen": round((1.0 - inv_sum) * 100.0, 2),
                "profit_percent": round(profit_percent, 2),
            }
        )
        logger.info(
            "scan_arbitrage_opportunities: %s | profit=%.2f%%",
            event,
            profit_percent,
        )

    logger.info(
        "scan_arbitrage_opportunities: %d opportunities", len(opportunities)
    )
    return opportunities


def _normalize_legs(
    event: str, odds: dict
) -> list[tuple[str, str, float]]:
    """
    Normalize market odds into (bookmaker, outcome, odds) legs.

    - Flat {casa: cuota}: each book is treated as a distinct outcome.
    - Nested {outcome: {casa: cuota}} or {outcome: cuota}: best book per outcome.
    """
    sample = next(iter(odds.values()))

    # Nested: outcome -> bookmaker -> odds  OR  outcome -> odds
    if isinstance(sample, dict) or (
        isinstance(sample, (int, float)) and _looks_like_outcomes(odds)
    ):
        legs: list[tuple[str, str, float]] = []
        for outcome, value in odds.items():
            if isinstance(value, dict):
                best_book, best_odds = max(
                    ((b, float(o)) for b, o in value.items() if float(o) > 1.0),
                    key=lambda item: item[1],
                    default=(None, None),
                )
                if best_book is None:
                    continue
                legs.append((best_book, str(outcome), best_odds))
            else:
                cuota = float(value)
                if cuota > 1.0:
                    legs.append(("unknown", str(outcome), cuota))
        return legs

    # Flat: bookmaker -> odds (one mutually exclusive outcome per book)
    legs = []
    for idx, (casa, cuota) in enumerate(odds.items()):
        cuota_f = float(cuota)
        if cuota_f > 1.0:
            legs.append((str(casa), f"outcome_{idx}", cuota_f))
    return legs


def _looks_like_outcomes(odds: dict) -> bool:
    """Heuristic: keys look like outcome labels rather than bookmaker names."""
    outcome_keys = {"home", "away", "draw", "1", "x", "2", "over", "under", "yes", "no"}
    return any(str(k).lower() in outcome_keys for k in odds)
