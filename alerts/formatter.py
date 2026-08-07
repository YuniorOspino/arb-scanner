"""Format arbitrage opportunities into actionable Telegram alerts."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

_SEP = "━━━━━━━━━━━━━━━━━━━━━━"

_OUTCOME_LABELS = {
    "home": "Local",
    "1": "Local",
    "local": "Local",
    "draw": "Empate",
    "x": "Empate",
    "empate": "Empate",
    "away": "Visitante",
    "2": "Visitante",
    "visitante": "Visitante",
    "over": "Más de",
    "under": "Menos de",
    "yes": "Sí",
    "si": "Sí",
    "sí": "Sí",
    "no": "No",
    "1x": "Local o Empate",
    "12": "Local o Visitante",
    "x2": "Empate o Visitante",
}

_BOOK_DISPLAY = {
    "rushbet": "RushBet",
    "codere": "Codere",
    "zamba": "Zamba",
    "betano": "Betano",
    "betplay": "BetPlay",
    "wplay": "Wplay",
}

_BOOK_HOME = {
    "betano": "https://www.betano.co/sport/futbol/",
    "betplay": "https://betplay.com.co/apuestas",
    "wplay": "https://apuestas.wplay.co/es/s/FOOT/F%C3%BAtbol",
    "rushbet": "https://www.rushbet.co/?page=sportsbook",
    "zamba": "https://www.zamba.co/deportes",
    "codere": "https://m.codere.com.co/deportesCol/",
}


def _money(value: float) -> str:
    return f"{float(value):.2f}"


def _odds(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _display_book(name: str) -> str:
    key = str(name).strip().lower()
    return _BOOK_DISPLAY.get(key, str(name).strip())


def _split_teams(evento: str) -> tuple[str, str]:
    parts = re.split(r"\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+", evento, flags=re.IGNORECASE)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "equipo local", "equipo visitante"


def _line_from_market(market_type: str) -> str | None:
    """Extract line like '0.5' / '2.5' from market codes (OU_2.5, CORNERS_OU_8, …)."""
    m = str(market_type or "").strip().upper()
    if not m:
        return None
    # Prefer trailing numeric token after last underscore.
    match = re.search(
        r"(?:OU_HT2_|OU_HT_|AOU_|OU_|CORNERS_OU_|CARDS_OU_|TT_HOME_|TT_AWAY_|AH_|EH_)"
        r"([+-]?\d+(?:[.,]\d+)?)$",
        m,
    )
    if match:
        return match.group(1).replace(",", ".")
    # Fallback: last number in the string.
    nums = re.findall(r"[+-]?\d+(?:[.,]\d+)?", m)
    if nums:
        return nums[-1].replace(",", ".")
    return None


def _is_totals_market(market_type: str) -> bool:
    up = str(market_type or "").strip().upper()
    return bool(
        up.startswith(
            (
                "OU_",
                "AOU_",
                "OU_HT",
                "CORNERS_OU_",
                "CARDS_OU_",
                "TT_HOME_",
                "TT_AWAY_",
            )
        )
        or "OU" in up
        or "OVER" in up
        or "UNDER" in up
    )


def _is_btts_market(market_type: str) -> bool:
    return str(market_type or "").strip().upper().startswith("BTTS")


def _selection_label(
    outcome: str,
    event_name: str,
    market_type: str = "",
) -> str:
    """
    Etiqueta corta estilo casas CO: 'Más de 2.5', 'Empate', nombre de equipo, 'Sí'/'No'.
    """
    raw = str(outcome or "").strip()
    key = raw.lower()
    local, away = _split_teams(event_name)
    line = _line_from_market(market_type)
    market = str(market_type or "").strip()

    # 1X2 / DNB / AH sides → team name or Empate
    if key in {"home", "1", "local"}:
        return local or "Local"
    if key in {"away", "2", "visitante"}:
        return away or "Visitante"
    if key in {"draw", "x", "empate"}:
        return "Empate"

    # BTTS
    if key in {"yes", "si", "sí"} or (_is_btts_market(market) and key in {"y", "true"}):
        return "Sí"
    if key in {"no"} or (_is_btts_market(market) and key in {"n", "false"}):
        return "No"

    # Over / Under → Más de X.X / Menos de X.X
    if key in {"over", "o", "mas", "más"} or key.startswith("over"):
        return f"Más de {line}" if line else "Más de"
    if key in {"under", "u", "menos"} or key.startswith("under"):
        return f"Menos de {line}" if line else "Menos de"

    # If outcome already looks like Colombian book text, keep it.
    low = key
    if "más de" in low or "mas de" in low or "menos de" in low:
        return raw
    if low in {"sí", "si", "no"}:
        return "Sí" if low in {"sí", "si"} else "No"

    # Double chance
    if key == "1x":
        return "Local o Empate"
    if key == "12":
        return "Local o Visitante"
    if key == "x2":
        return "Empate o Visitante"

    # Totals market but odd outcome key — still try line
    if _is_totals_market(market) and line:
        if "over" in low or "mas" in low or "más" in low:
            return f"Más de {line}"
        if "under" in low or "menos" in low:
            return f"Menos de {line}"

    base = _OUTCOME_LABELS.get(key)
    if base is not None:
        if base in {"Más de", "Menos de"} and line:
            return f"{base} {line}"
        return base

    # Correct score / HTFT / unknown — keep compact raw
    return raw


def _market_label(market_type: str) -> str:
    m = str(market_type or "").strip()
    up = m.upper()
    if up in {"1X2", "MRES", "MR", "MATCH_RESULT"}:
        return "1X2"
    if up == "BTTS":
        return "BTTS"
    if up.startswith("BTTS"):
        return f"BTTS ({m})"
    if up == "DC":
        return "Double Chance"
    if up.startswith("DC"):
        return f"Double Chance ({m})"
    if up == "DNB":
        return "Draw No Bet"
    if up == "HTFT":
        return "Half Time / Full Time"
    if up == "HT_1X2":
        return "Half Time 1X2"
    if up == "HT2_1X2":
        return "Second Half 1X2"
    if up == "CS" or up.startswith("CS"):
        return "Correct Score"
    if up.startswith("OU_HT_"):
        return f"Over-Under 1H {up[6:]}"
    if up.startswith("OU_HT2_"):
        return f"Over-Under 2H {up[7:]}"
    if up.startswith("OU_"):
        return f"Over-Under {up[3:]}"
    if up.startswith("AOU_"):
        return f"Asian Over-Under {up[4:]}"
    if up.startswith("AH_"):
        return f"Handicap Asiático {up[3:]}"
    if up.startswith("EH_"):
        return f"Handicap Europeo {up[3:]}"
    if up.startswith("TT_HOME_"):
        return f"Team Total Local {up[8:]}"
    if up.startswith("TT_AWAY_"):
        return f"Team Total Visitante {up[8:]}"
    if up.startswith("CORNERS_OU_"):
        return f"Corners Over-Under {up[11:]}"
    if up.startswith("CARDS_OU_"):
        return f"Cards Over-Under {up[9:]}"
    if "HANDICAP" in up or up.startswith("AH") or up.startswith("EH"):
        return f"Handicap ({m})"
    if "OU" in up or "OVER" in up:
        return f"Over-Under ({m})"
    return m or "Mercado"


def _event_search_queries(event_name: str) -> tuple[str, str, str]:
    """
    Build URL-encoded search strings from an event name.

    Returns (query_vs, query_plain, query_home):
      - "home vs away" (best for most CO search UIs)
      - "home away"
      - home team only (WPlay-style; often more reliable)
    """
    home, away = _split_teams(event_name)
    home = (home or "").strip()
    away = (away or "").strip()
    if home and away and home != "equipo local":
        q_vs = quote_plus(f"{home} vs {away}")
        q_plain = quote_plus(f"{home} {away}")
        q_home = quote_plus(home)
    else:
        fallback = quote_plus(str(event_name or "").strip())
        q_vs = q_plain = q_home = fallback
    return q_vs, q_plain, q_home


def _event_link(bookmaker: str, event_name: str) -> str:
    """Best-effort search link to open the match quickly (alert-layer only)."""
    key = str(bookmaker).strip().lower()
    q_vs, q_plain, q_home = _event_search_queries(event_name)

    if key == "betano":
        # Official site search; "home vs away" matches listings better than bare concat.
        return f"https://www.betano.co/search?q={q_vs}"
    if key == "betplay":
        # Kambi SPA search (same surface as before, clearer query + hash route).
        return (
            "https://betplay.com.co/apuestas?"
            f"searchTerm={q_vs}#/search?query={q_vs}"
        )
    if key == "rushbet":
        return f"https://www.rushbet.co/?page=sportsbook&search={q_vs}"
    if key == "wplay":
        # Official search form: GET /es/search?s=...
        # Full "home vs away" often yields empty results; home team hits reliably.
        return f"https://apuestas.wplay.co/es/search?s={q_home}"
    if key == "zamba":
        # Deportes search: prefer "home vs away"; also keep plain names as q=.
        return f"https://www.zamba.co/deportes?search={q_vs}&q={q_plain}"
    if key == "codere":
        return f"https://m.codere.com.co/deportesCol/#/search/{q_vs}"
    return _BOOK_HOME.get(key, "No disponible")


def _format_detected_at(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _age_seconds(ts: datetime, *, now: datetime | None = None) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current - ts.astimezone(timezone.utc)).total_seconds()))


def format_arbitrage_alert(opportunity: dict | ArbitrageOpportunity) -> str:
    """Build a professional Telegram alert for manual execution (<20s)."""
    if not isinstance(opportunity, ArbitrageOpportunity):
        opportunity = opportunity_from_payload(opportunity)
    return format_execution_ready_alert(
        opportunity,
        status="LISTO",
        execution_id=None,
        score=None,
    )


def format_execution_ready_alert(
    opportunity: dict | ArbitrageOpportunity,
    *,
    status: str = "LISTO",
    execution_id: int | None = None,
    score: float | None = None,
) -> str:
    """Execution Manager payload: everything ready for click → confirm."""
    if not isinstance(opportunity, ArbitrageOpportunity):
        opportunity = opportunity_from_payload(opportunity)

    detected = _format_detected_at(opportunity.detected_at)
    age = _age_seconds(opportunity.detected_at)
    market = _market_label(opportunity.market_type)
    estado = str(status or "LISTO").upper()

    lines = [
        _SEP,
        "🚨 ARBITRAJE DETECTADO",
        _SEP,
        "🏆 Deporte: Fútbol",
        "🏆 Liga: No disponible",
        f"⚽ Partido: {opportunity.event_name}",
        "🕒 Hora del evento: No disponible",
        f"📊 Mercado: {market}",
        f"📈 ROI: {_odds(opportunity.profit_percent)}%",
        f"💰 Beneficio esperado: {_money(opportunity.expected_profit)}",
        f"📌 Estado: {estado}",
    ]
    if execution_id is not None:
        lines.append(f"🆔 Ejecución: {execution_id}")
    if score is not None:
        lines.append(f"⭐ Score: {_odds(score)}")
    lines.append(_SEP)

    for idx, (bookmaker, outcome, odds, stake) in enumerate(opportunity.legs, start=1):
        lines.extend(
            [
                f"CASA {idx}",
                f"Nombre: {_display_book(bookmaker)}",
                f"Mercado: {market}",
                f"Selección: {_selection_label(outcome, opportunity.event_name, opportunity.market_type)}",
                f"Stake: {_money(stake)}",
                f"Cuota: {_odds(odds)}",
                f"Link directo al partido: {_event_link(bookmaker, opportunity.event_name)}",
                f"Estado: {estado}",
                _SEP,
            ]
        )

    lines.extend(
        [
            f"⏱ Detectado: {detected}",
            f"⌛ Edad de la oportunidad: {age} segundos",
            "👉 Clic en el link → confirma en la casa → siguiente casa",
            _SEP,
        ]
    )

    msg = "\n".join(lines)
    logger.debug(
        "Formatted execution alert for event=%s (%d chars)",
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
        detected = datetime.now(timezone.utc)

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
        lines.append(f"Stake recomendado: {_money(float(stake))}")
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
