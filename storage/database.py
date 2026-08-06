"""SQLite persistence for detected arbitrage opportunities."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    market_type TEXT NOT NULL,
    profit_percent REAL NOT NULL,
    total_stake REAL NOT NULL,
    expected_profit REAL NOT NULL,
    legs_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_opportunities_event
    ON opportunities(event_name);

CREATE INDEX IF NOT EXISTS idx_opportunities_detected
    ON opportunities(detected_at);

CREATE TABLE IF NOT EXISTS arbitrage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evento TEXT,
    casas TEXT,
    cuotas TEXT,
    margen REAL,
    profit_percent REAL,
    stakes TEXT,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_arb_history_fecha
    ON arbitrage_history(fecha);

CREATE TABLE IF NOT EXISTS value_bet_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evento TEXT,
    casa TEXT,
    cuota REAL,
    prob_implicita REAL,
    prob_mercado REAL,
    prob_personal REAL,
    edge REAL,
    expected_value REAL,
    stake REAL,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_value_bet_fecha
    ON value_bet_history(fecha);
"""


def save_value_bet(
    value_bet: dict,
    db_path: str | Path = "arb_scanner.db",
) -> bool:
    """
    Guarda una apuesta de valor en la base de datos.

    value_bet: dict con cuota, probabilidad mercado/personal, edge, stake
    db_path: ruta de la base de datos SQLite

    Devuelve True si se inserto correctamente.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    stake = value_bet.get("stake", 0)
    if stake is None and isinstance(value_bet.get("kelly"), dict):
        stake = value_bet["kelly"].get("stake", 0)

    try:
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """
                INSERT INTO value_bet_history (
                    evento, casa, cuota, prob_implicita, prob_mercado,
                    prob_personal, edge, expected_value, stake
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value_bet.get("evento") or value_bet.get("event_name") or "",
                    value_bet.get("casa") or value_bet.get("bookmaker") or "",
                    float(value_bet.get("cuota", 0) or 0),
                    float(value_bet.get("probabilidad_implicita", 0) or 0),
                    float(value_bet.get("probabilidad_mercado", 0) or 0),
                    float(value_bet.get("probabilidad_personal", 0) or 0),
                    float(value_bet.get("edge", 0) or 0),
                    float(value_bet.get("expected_value", 0) or 0),
                    float(stake or 0),
                ),
            )
        logger.info(
            "Saved value_bet_history: cuota=%s edge=%s%%",
            value_bet.get("cuota"),
            value_bet.get("edge"),
        )
        return True
    except (TypeError, ValueError, sqlite3.Error):
        logger.exception("Failed to save value bet to %s", path)
        return False


def save_arbitrage_opportunity(
    opportunity: dict,
    db_path: str | Path = "arb_scanner.db",
) -> bool:
    """
    Guarda una oportunidad de arbitraje en la base de datos.

    opportunity: dict con info de arbitraje (evento, casas, cuotas, margen, stakes)
    db_path: ruta de la base de datos SQLite

    Devuelve True si se inserto correctamente.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    evento = opportunity.get("evento") or opportunity.get("event_name") or ""
    casas = opportunity.get("casas_involucradas") or opportunity.get("casas") or []
    if isinstance(casas, dict):
        casas_str = ",".join(str(v) for v in casas.values())
    elif isinstance(casas, list):
        casas_str = ",".join(str(c) for c in casas)
    else:
        casas_str = str(casas)

    cuotas = opportunity.get("mejores_cuotas", opportunity.get("cuotas", {}))
    stakes = opportunity.get("stakes", {})
    # Unwrap nested stakes from calculate_arbitrage_stakes
    if isinstance(stakes, dict) and "stakes" in stakes and isinstance(
        stakes["stakes"], dict
    ):
        stakes = stakes["stakes"]

    margen = float(opportunity.get("margen", 0) or 0)
    profit = float(
        opportunity.get("profit_percent", margen) or 0
    )

    try:
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """
                INSERT INTO arbitrage_history
                    (evento, casas, cuotas, margen, profit_percent, stakes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evento,
                    casas_str,
                    json.dumps(cuotas, ensure_ascii=False),
                    margen,
                    profit,
                    json.dumps(stakes, ensure_ascii=False),
                ),
            )
        logger.info("Saved arbitrage_history: %s | margen=%.2f%%", evento, margen)
        return True
    except sqlite3.Error:
        logger.exception("Failed to save arbitrage opportunity to %s", path)
        return False


class OpportunityStore:
    """Save and deduplicate opportunities in SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("SQLite store ready: %s", self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.debug("Database schema ensured")

    @staticmethod
    def fingerprint(opp: ArbitrageOpportunity) -> str:
        """Stable hash to avoid alerting the same arb repeatedly."""
        legs_key = tuple(
            (bm, outcome, round(odds, 3)) for bm, outcome, odds, _stake in opp.legs
        )
        raw = json.dumps(
            {
                "event": opp.event_name,
                "market": opp.market_type,
                "legs": legs_key,
                "profit": round(opp.profit_percent, 3),
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def save_if_new(self, opp: ArbitrageOpportunity) -> bool:
        """
        Insert opportunity if fingerprint is new.

        Returns True when inserted (new), False if duplicate.
        """
        fp = self.fingerprint(opp)
        legs_json = json.dumps(
            [
                {
                    "bookmaker": bm,
                    "outcome": outcome,
                    "odds": odds,
                    "stake": stake,
                }
                for bm, outcome, odds, stake in opp.legs
            ]
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO opportunities (
                        fingerprint, event_name, market_type,
                        profit_percent, total_stake, expected_profit,
                        legs_json, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        opp.event_name,
                        opp.market_type,
                        opp.profit_percent,
                        opp.total_stake,
                        opp.expected_profit,
                        legs_json,
                        opp.detected_at.isoformat(),
                    ),
                )
            logger.debug("Saved opportunity fingerprint=%s", fp[:12])
            return True
        except sqlite3.IntegrityError:
            logger.debug("Duplicate fingerprint=%s", fp[:12])
            return False
        except sqlite3.Error:
            logger.exception("Failed to save opportunity")
            return False

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM opportunities
                ORDER BY detected_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())
