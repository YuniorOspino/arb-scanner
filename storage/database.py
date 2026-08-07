"""SQLite persistence for detected arbitrage opportunities + Execution Manager."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)

STATUS_DISCARDED = "discarded"
STATUS_QUEUED = "queued"
STATUS_ACTIVE = "active"
STATUS_RESERVED = "active"  # alias: only one reserved/active at a time
STATUS_DONE = "done"
STATUS_RELEASED = "released"
STATUS_EXPIRED = "expired"
_ACTIVE_STATUSES = frozenset({STATUS_ACTIVE, "reserved"})
_OPEN_STATUSES = frozenset({STATUS_QUEUED, STATUS_ACTIVE, "reserved"})

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

CREATE TABLE IF NOT EXISTS book_capital (
    bookmaker TEXT PRIMARY KEY,
    bankroll REAL NOT NULL,
    reserved REAL NOT NULL DEFAULT 0,
    spent REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS execution_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    market_type TEXT NOT NULL,
    profit_percent REAL NOT NULL,
    total_stake REAL NOT NULL,
    expected_profit REAL NOT NULL,
    legs_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_execution_status_score
    ON execution_queue(status, score DESC);

CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    execution_id INTEGER,
    event_name TEXT NOT NULL,
    market_type TEXT,
    market_label TEXT,
    casas_json TEXT NOT NULL,
    roi REAL NOT NULL,
    total_stake REAL NOT NULL,
    status TEXT NOT NULL,
    discard_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alert_history_ts
    ON alert_history(ts);

CREATE INDEX IF NOT EXISTS idx_alert_history_status
    ON alert_history(status);
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
    """Dedupe store + Execution Manager (capital, score, reserve, done/release)."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        book_capitals: dict[str, float] | None = None,
        top_n: int = 25,
        ttl_seconds: int = 120,
        queue_max: int | None = None,
        alert_max_age_seconds: float | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.book_capitals = {
            str(k).strip().lower(): float(v)
            for k, v in (book_capitals or {}).items()
        }
        self.queue_max = max(1, int(queue_max if queue_max is not None else top_n))
        self.top_n = self.queue_max
        self.ttl_seconds = max(15, int(ttl_seconds))
        # Never activate/send items older than this (aligned with Telegram age filter).
        self.alert_max_age_seconds = float(
            alert_max_age_seconds
            if alert_max_age_seconds is not None
            else min(90.0, float(self.ttl_seconds))
        )
        self._init_db()
        self._sync_book_capitals()
        logger.info(
            "SQLite store ready: %s (queue_max=%d ttl=%ds alert_max_age=%.0fs)",
            self.db_path,
            self.queue_max,
            self.ttl_seconds,
            self.alert_max_age_seconds,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.debug("Database schema ensured")

    def _sync_book_capitals(self) -> None:
        """Ensure book_capital rows exist; update bankroll from config without wiping reserved/spent."""
        if not self.book_capitals:
            return
        with self._connect() as conn:
            for book, bankroll in self.book_capitals.items():
                row = conn.execute(
                    "SELECT bookmaker FROM book_capital WHERE bookmaker = ?",
                    (book,),
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO book_capital (bookmaker, bankroll, reserved, spent)
                        VALUES (?, ?, 0, 0)
                        """,
                        (book, float(bankroll)),
                    )
                else:
                    conn.execute(
                        "UPDATE book_capital SET bankroll = ? WHERE bookmaker = ?",
                        (float(bankroll), book),
                    )

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

    @staticmethod
    def _legs_json(opp: ArbitrageOpportunity) -> str:
        return json.dumps(
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

    def save_if_new(self, opp: ArbitrageOpportunity) -> bool:
        """
        Insert opportunity if fingerprint is new.

        Returns True when inserted (new), False if duplicate.
        """
        fp = self.fingerprint(opp)
        legs_json = self._legs_json(opp)
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

    # --- Execution Manager ---

    def available_capital(self) -> dict[str, float]:
        """bankroll - reserved - spent per book."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT bookmaker, bankroll, reserved, spent FROM book_capital"
            ).fetchall()
        out: dict[str, float] = {}
        for row in rows:
            avail = float(row["bankroll"]) - float(row["reserved"]) - float(row["spent"])
            out[str(row["bookmaker"])] = max(0.0, avail)
        return out

    @staticmethod
    def execution_score(opp: ArbitrageOpportunity, *, now: datetime | None = None) -> float:
        """Higher = better. Combines ROI, expected profit, capital need, age."""
        current = now or datetime.now(timezone.utc)
        detected = opp.detected_at
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        age = max(0.0, (current - detected.astimezone(timezone.utc)).total_seconds())
        total = max(float(opp.total_stake), 1.0)
        rel_profit = float(opp.expected_profit) / total
        return (
            float(opp.profit_percent) * 10.0
            + rel_profit * 100.0
            + float(opp.expected_profit) * 0.001
            - total * 0.00005
            - min(age, 900.0) / 60.0 * 5.0
        )

    def _can_fund(self, opp: ArbitrageOpportunity, available: dict[str, float]) -> bool:
        needed: dict[str, float] = {}
        for bookmaker, _outcome, _odds, stake in opp.legs:
            book = str(bookmaker).strip().lower()
            needed[book] = needed.get(book, 0.0) + float(stake)
        for book, stake in needed.items():
            if stake > available.get(book, 0.0) + 1e-9:
                return False
        return True

    def _reserve_legs(self, conn: sqlite3.Connection, opp: ArbitrageOpportunity) -> bool:
        """Atomically reserve stakes if still affordable. Returns False if not."""
        needed: dict[str, float] = {}
        for bookmaker, _outcome, _odds, stake in opp.legs:
            book = str(bookmaker).strip().lower()
            needed[book] = needed.get(book, 0.0) + float(stake)

        for book, stake in needed.items():
            row = conn.execute(
                "SELECT bankroll, reserved, spent FROM book_capital WHERE bookmaker = ?",
                (book,),
            ).fetchone()
            if row is None:
                return False
            avail = float(row["bankroll"]) - float(row["reserved"]) - float(row["spent"])
            if stake > avail + 1e-9:
                return False

        for book, stake in needed.items():
            conn.execute(
                "UPDATE book_capital SET reserved = reserved + ? WHERE bookmaker = ?",
                (stake, book),
            )
        return True

    def _release_legs(
        self,
        conn: sqlite3.Connection,
        legs: list[dict[str, Any]],
        *,
        spend: bool,
    ) -> None:
        needed: dict[str, float] = {}
        for leg in legs:
            book = str(leg.get("bookmaker", "")).strip().lower()
            needed[book] = needed.get(book, 0.0) + float(leg.get("stake") or 0)

        for book, stake in needed.items():
            if not book:
                continue
            conn.execute(
                """
                UPDATE book_capital
                SET reserved = CASE
                        WHEN reserved - ? < 0 THEN 0
                        ELSE reserved - ?
                    END,
                    spent = spent + ?
                WHERE bookmaker = ?
                """,
                (stake, stake, stake if spend else 0.0, book),
            )

    def ingest_for_execution(
        self,
        opportunities: list[ArbitrageOpportunity],
    ) -> list[dict[str, Any]]:
        """Backward-compatible: run queue cycle; return [active] if Telegram must send."""
        active = self.process_execution_cycle(opportunities)
        return [active] if active is not None else []

    def process_execution_cycle(
        self,
        opportunities: list[ArbitrageOpportunity] | None = None,
    ) -> dict[str, Any] | None:
        """
        Queue + rank + expire + single active.

        Returns the active execution that Telegram should send NOW
        (newly activated or replaced by a better one). None if no send needed.
        """
        now = datetime.now(timezone.utc)
        # Use the tighter of TTL and alert max-age so we never activate items
        # that the Telegram pipeline would immediately reject as DESCARTADA_EDAD.
        self.expire_stale(now=now, max_age_seconds=self.alert_max_age_seconds)
        if opportunities:
            self._enqueue_candidates(opportunities, now=now)
        self._trim_queue()
        self._refresh_open_scores(now=now)
        return self._ensure_best_active(now=now)

    def get_active_execution(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM execution_queue
                WHERE status IN (?, ?)
                ORDER BY score DESC, id ASC
                LIMIT 1
                """,
                (STATUS_ACTIVE, "reserved"),
            ).fetchone()
        return self._row_to_execution(row) if row else None

    def promote_next_active(self) -> dict[str, Any] | None:
        """Activate best queued (no current active). Returns active for Telegram."""
        now = datetime.now(timezone.utc)
        # Purge stale queue first — otherwise discard→promote chains through old items.
        self.expire_stale(now=now, max_age_seconds=self.alert_max_age_seconds)
        if self.get_active_execution() is not None:
            return None
        return self._ensure_best_active(now=now)

    def expire_stale(
        self,
        *,
        now: datetime | None = None,
        max_age_seconds: float | None = None,
    ) -> bool:
        """
        Expire open items past TTL (or max_age_seconds if tighter).
        Releases capital if active expires.

        Returns True if the active slot was cleared (caller should promote/send next).
        """
        current = now or datetime.now(timezone.utc)
        limit = float(self.ttl_seconds)
        if max_age_seconds is not None:
            limit = min(limit, float(max_age_seconds))
        active_cleared = False
        expired_n = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM execution_queue
                WHERE status IN (?, ?, ?)
                """,
                (STATUS_QUEUED, STATUS_ACTIVE, "reserved"),
            ).fetchall()
            for row in rows:
                detected = self._parse_detected(row["detected_at"])
                age = (current - detected).total_seconds()
                if age <= limit:
                    continue
                status = str(row["status"])
                if status in _ACTIVE_STATUSES:
                    legs = json.loads(row["legs_json"])
                    self._release_legs(conn, legs, spend=False)
                    active_cleared = True
                conn.execute(
                    """
                    UPDATE execution_queue
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (STATUS_EXPIRED, int(row["id"])),
                )
                expired_n += 1
                logger.debug(
                    "EM expired id=%s event=%s age=%.0fs limit=%.0fs",
                    row["id"],
                    row["event_name"],
                    age,
                    limit,
                )
        if expired_n:
            logger.info("EM: %d ejecución(es) expirada(s) (limit=%.0fs)", expired_n, limit)
        return active_cleared

    def purge_stale_open(
        self,
        *,
        max_age_seconds: float,
        now: datetime | None = None,
    ) -> int:
        """
        Force-expire every open queue/active row older than max_age_seconds.
        Used on startup to flush backlog that would otherwise re-alert.
        Returns number of rows purged.
        """
        current = now or datetime.now(timezone.utc)
        purged = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM execution_queue
                WHERE status IN (?, ?, ?)
                """,
                (STATUS_QUEUED, STATUS_ACTIVE, "reserved"),
            ).fetchall()
            for row in rows:
                detected = self._parse_detected(row["detected_at"])
                age = (current - detected).total_seconds()
                if age <= max_age_seconds:
                    continue
                if str(row["status"]) in _ACTIVE_STATUSES:
                    legs = json.loads(row["legs_json"])
                    self._release_legs(conn, legs, spend=False)
                conn.execute(
                    """
                    UPDATE execution_queue
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (STATUS_EXPIRED, int(row["id"])),
                )
                purged += 1
                logger.info(
                    "EM purge id=%s event=%s age=%.0fs (max=%.0fs)",
                    row["id"],
                    row["event_name"],
                    age,
                    max_age_seconds,
                )
        if purged:
            logger.warning(
                "EM purged %d stale open execution(s) older than %.0fs",
                purged,
                max_age_seconds,
            )
        return purged

    def discard_active(self, execution_id: int, *, reason: str = "") -> bool:
        """Drop active without spending capital (stale/ROI reject). Free slot."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_queue WHERE id = ?",
                (int(execution_id),),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) not in _ACTIVE_STATUSES:
                # Also allow discarding queued leftovers if needed
                if str(row["status"]) != STATUS_QUEUED:
                    return False
                conn.execute(
                    """
                    UPDATE execution_queue
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (STATUS_EXPIRED, int(execution_id)),
                )
                logger.info(
                    "EM discarded queued id=%s reason=%s", execution_id, reason or "-"
                )
                return True
            legs = json.loads(row["legs_json"])
            self._release_legs(conn, legs, spend=False)
            conn.execute(
                """
                UPDATE execution_queue
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (STATUS_EXPIRED, int(execution_id)),
            )
        logger.info(
            "EM discarded active id=%s reason=%s", execution_id, reason or "-"
        )
        return True

    def _enqueue_candidates(
        self,
        opportunities: list[ArbitrageOpportunity],
        *,
        now: datetime,
    ) -> None:
        available = self.available_capital()
        # Active holds reserved capital — for funding check of new queued items,
        # use capital as if only spent matters for queue eligibility of others.
        # Queued does not reserve; check against available + currently reserved by active
        # so we don't reject items that become fundable after demotion.
        funding_base = self._funding_base_including_active_release()
        discarded = 0
        with self._connect() as conn:
            for opp in opportunities:
                if not self._can_fund(opp, funding_base):
                    discarded += 1
                    continue
                fp = self.fingerprint(opp)
                score = self.execution_score(opp, now=now)
                existing = conn.execute(
                    "SELECT id, status FROM execution_queue WHERE fingerprint = ?",
                    (fp,),
                ).fetchone()
                if existing is not None:
                    st = str(existing["status"])
                    if st in _OPEN_STATUSES:
                        conn.execute(
                            """
                            UPDATE execution_queue
                            SET score = ?, profit_percent = ?, expected_profit = ?,
                                total_stake = ?, legs_json = ?,
                                updated_at = datetime('now')
                            WHERE id = ?
                            """,
                            (
                                float(score),
                                opp.profit_percent,
                                opp.expected_profit,
                                opp.total_stake,
                                self._legs_json(opp),
                                int(existing["id"]),
                            ),
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO execution_queue (
                        fingerprint, event_name, market_type,
                        profit_percent, total_stake, expected_profit,
                        legs_json, detected_at, score, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        opp.event_name,
                        opp.market_type,
                        opp.profit_percent,
                        opp.total_stake,
                        opp.expected_profit,
                        self._legs_json(opp),
                        opp.detected_at.isoformat(),
                        float(score),
                        STATUS_QUEUED,
                    ),
                )
        if discarded:
            logger.info("EM: %d candidates discarded (capital)", discarded)

    def _funding_base_including_active_release(self) -> dict[str, float]:
        """Available capital as if active reservation were released (for queue eligibility)."""
        avail = self.available_capital()
        active = self.get_active_execution()
        if active is None:
            return avail
        for leg in active["legs"]:
            book = str(leg.get("bookmaker", "")).strip().lower()
            avail[book] = avail.get(book, 0.0) + float(leg.get("stake") or 0)
        return avail

    def _trim_queue(self) -> None:
        """Keep only queue_max highest-score queued items."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM execution_queue
                WHERE status = ?
                ORDER BY score DESC, id ASC
                """,
                (STATUS_QUEUED,),
            ).fetchall()
            if len(rows) <= self.queue_max:
                return
            drop_ids = [int(r["id"]) for r in rows[self.queue_max :]]
            for eid in drop_ids:
                conn.execute(
                    """
                    UPDATE execution_queue
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (STATUS_DISCARDED, eid),
                )

    def _refresh_open_scores(self, *, now: datetime) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM execution_queue
                WHERE status IN (?, ?, ?)
                """,
                (STATUS_QUEUED, STATUS_ACTIVE, "reserved"),
            ).fetchall()
            for row in rows:
                opp = self.execution_to_opportunity(self._row_to_execution(row))
                score = self.execution_score(opp, now=now)
                conn.execute(
                    "UPDATE execution_queue SET score = ? WHERE id = ?",
                    (float(score), int(row["id"])),
                )

    def _ensure_best_active(self, *, now: datetime) -> dict[str, Any] | None:
        """
        Ensure the single active slot is the best fundable open item.

        Returns active dict only when Telegram should send (new/replaced).
        """
        with self._connect() as conn:
            open_rows = conn.execute(
                """
                SELECT * FROM execution_queue
                WHERE status IN (?, ?, ?)
                ORDER BY score DESC, id ASC
                """,
                (STATUS_QUEUED, STATUS_ACTIVE, "reserved"),
            ).fetchall()

        if not open_rows:
            return None

        active_row = next(
            (r for r in open_rows if str(r["status"]) in _ACTIVE_STATUSES),
            None,
        )
        # Best candidate by score that we can fund (with active capital released conceptually)
        # Skip anything already older than alert_max_age (Telegram would reject it).
        funding = self._funding_base_including_active_release()
        best_row = None
        for row in open_rows:
            detected = self._parse_detected(row["detected_at"])
            age = (now - detected).total_seconds()
            if age > self.alert_max_age_seconds:
                logger.debug(
                    "EM skip stale candidate id=%s age=%.1fs detected_at=%s",
                    row["id"],
                    age,
                    detected.isoformat(),
                )
                continue
            opp = self.execution_to_opportunity(self._row_to_execution(row))
            if self._can_fund(opp, funding):
                best_row = row
                break
        if best_row is None:
            return None

        best_id = int(best_row["id"])
        if active_row is not None and int(active_row["id"]) == best_id:
            return None  # already correct active — no Telegram resend

        # Demote current active back to queue (release capital)
        if active_row is not None:
            with self._connect() as conn:
                legs = json.loads(active_row["legs_json"])
                self._release_legs(conn, legs, spend=False)
                conn.execute(
                    """
                    UPDATE execution_queue
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (STATUS_QUEUED, int(active_row["id"])),
                )
            logger.info(
                "EM demoted active id=%s — better id=%s score=%.2f",
                active_row["id"],
                best_id,
                float(best_row["score"]),
            )

        # Activate best (reserve capital)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_queue WHERE id = ?",
                (best_id,),
            ).fetchone()
            if row is None or str(row["status"]) not in {STATUS_QUEUED, STATUS_ACTIVE, "reserved"}:
                return None
            opp = self.execution_to_opportunity(self._row_to_execution(row))
            if not self._reserve_legs(conn, opp):
                logger.warning("EM could not reserve capital for id=%s", best_id)
                return None
            conn.execute(
                """
                UPDATE execution_queue
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (STATUS_ACTIVE, best_id),
            )
            activated = self._row_to_execution(
                conn.execute(
                    "SELECT * FROM execution_queue WHERE id = ?",
                    (best_id,),
                ).fetchone()
            )
        logger.info(
            "EM active id=%s event=%s score=%.2f → Telegram",
            activated["id"],
            activated["event_name"],
            activated["score"],
        )
        return activated

    def get_execution(self, execution_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_queue WHERE id = ?",
                (int(execution_id),),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_execution(row)

    def mark_executed(self, execution_id: int) -> bool:
        """Finish active: spend capital. Caller should promote_next_active()."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_queue WHERE id = ?",
                (int(execution_id),),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) not in _ACTIVE_STATUSES:
                logger.warning(
                    "EM mark_executed ignored id=%s status=%s",
                    execution_id,
                    row["status"],
                )
                return False
            legs = json.loads(row["legs_json"])
            self._release_legs(conn, legs, spend=True)
            conn.execute(
                """
                UPDATE execution_queue
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (STATUS_DONE, int(execution_id)),
            )
        logger.info("EM executed id=%s", execution_id)
        return True

    def release_reservation(self, execution_id: int) -> bool:
        """Cancel active; capital returns. Caller should promote_next_active()."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_queue WHERE id = ?",
                (int(execution_id),),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) not in _ACTIVE_STATUSES:
                logger.warning(
                    "EM release ignored id=%s status=%s",
                    execution_id,
                    row["status"],
                )
                return False
            legs = json.loads(row["legs_json"])
            self._release_legs(conn, legs, spend=False)
            conn.execute(
                """
                UPDATE execution_queue
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (STATUS_RELEASED, int(execution_id)),
            )
        logger.info("EM released id=%s", execution_id)
        return True

    def record_alert_event(
        self,
        *,
        execution_id: int | None,
        event_name: str,
        market_type: str,
        market_label: str,
        casas: list[dict[str, Any]],
        roi: float,
        total_stake: float,
        status: str,
        discard_reason: str | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Persist sent/discarded alert for Excel export + exposure accounting."""
        when = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
        slim_casas = []
        for c in casas or []:
            slim_casas.append(
                {
                    "nombre": c.get("nombre") or c.get("bookmaker"),
                    "seleccion": c.get("seleccion") or c.get("outcome"),
                    "cuota": c.get("cuota") or c.get("odds"),
                    "stake": c.get("stake"),
                    "mercado": c.get("mercado") or market_label,
                }
            )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_history (
                    ts, execution_id, event_name, market_type, market_label,
                    casas_json, roi, total_stake, status, discard_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    when.isoformat(),
                    int(execution_id) if execution_id is not None else None,
                    str(event_name or ""),
                    str(market_type or ""),
                    str(market_label or ""),
                    json.dumps(slim_casas, ensure_ascii=False),
                    float(roi or 0),
                    float(total_stake or 0),
                    str(status),
                    discard_reason,
                ),
            )

    def exposure_sent_today(self, *, now: datetime | None = None) -> float:
        """Sum of stakes for alerts sent since 00:00 UTC today."""
        current = now or datetime.now(timezone.utc)
        day_start = current.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(total_stake), 0) AS s
                FROM alert_history
                WHERE status = 'sent' AND ts >= ?
                """,
                (day_start.isoformat(),),
            ).fetchone()
        return float(row["s"] if row else 0)

    def exposure_simultaneous(self) -> float:
        """Stake reserved by current active execution (unresolved)."""
        active = self.get_active_execution()
        if active is None:
            return 0.0
        return float(active.get("total_stake") or 0)

    def check_exposure_limits(
        self,
        stake: float,
        *,
        max_diaria: float,
        max_simultanea: float,
    ) -> str | None:
        """
        Return discard reason if stake would breach limits; else None.
        max_*=0 disables that limit.
        Simultaneous = stake of the alert that would become the single active.
        Daily = sum of stakes already sent (status=sent) since 00:00 UTC + this stake.
        """
        stake = float(stake or 0)
        if max_simultanea > 0 and stake > max_simultanea:
            return "DESCARTADA_LIMITE_EXPOSICION"
        if max_diaria > 0 and (self.exposure_sent_today() + stake) > max_diaria:
            return "DESCARTADA_LIMITE_EXPOSICION"
        return None

    @staticmethod
    def _parse_detected(raw: Any) -> datetime:
        """
        Parse detected_at as UTC.

        Naive timestamps are assumed UTC (Railway/SQLite convention).
        On parse failure, return epoch so age is huge and the row is expired —
        never 'now' (that would keep corrupt rows forever).
        """
        text = str(raw or "").strip().replace("Z", "+00:00")
        try:
            detected = datetime.fromisoformat(text)
        except ValueError:
            logger.warning(
                "EM detected_at unparseable %r — treating as ancient", raw
            )
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        return detected.astimezone(timezone.utc)

    def _row_to_execution(self, row: sqlite3.Row) -> dict[str, Any]:
        legs = json.loads(row["legs_json"])
        return {
            "id": int(row["id"]),
            "fingerprint": row["fingerprint"],
            "event_name": row["event_name"],
            "market_type": row["market_type"],
            "profit_percent": float(row["profit_percent"]),
            "total_stake": float(row["total_stake"]),
            "expected_profit": float(row["expected_profit"]),
            "legs": legs,
            "detected_at": self._parse_detected(row["detected_at"]),
            "score": float(row["score"]),
            "status": row["status"],
        }

    def execution_to_opportunity(self, execution: dict[str, Any]) -> ArbitrageOpportunity:
        legs = tuple(
            (
                str(leg["bookmaker"]),
                str(leg["outcome"]),
                float(leg["odds"]),
                float(leg["stake"]),
            )
            for leg in execution["legs"]
        )
        detected = execution["detected_at"]
        if not isinstance(detected, datetime):
            detected = datetime.now(timezone.utc)
        return ArbitrageOpportunity(
            event_name=str(execution["event_name"]),
            market_type=str(execution["market_type"]),
            profit_percent=float(execution["profit_percent"]),
            total_stake=float(execution["total_stake"]),
            legs=legs,
            detected_at=detected,
        )
