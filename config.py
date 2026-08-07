"""Application configuration: thresholds, bookmakers, scan intervals.

Secrets (Telegram) MUST come from environment / .env — never hardcode them here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# --- Non-secret defaults (override via env) ---
TOTAL_INVESTMENT = float(os.getenv("TOTAL_INVESTMENT", "100.0"))
MIN_MARGIN_THRESHOLD = float(os.getenv("MIN_MARGIN_THRESHOLD", "1.5"))
# Gate post-recalc pre-envío (alerts/telegram.prepare_opportunity_for_alert).
# Detección puede exigir MIN_MARGIN_THRESHOLD; tras releer cuotas basta este ROI.
ALERT_POST_RECALC_MIN_ROI = float(os.getenv("ALERT_POST_RECALC_MIN_ROI", "1.2"))

# Plan diario de ingresos (alerts/daily_plan.py)
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "10000"))
DAILY_RISK_CAP = float(os.getenv("DAILY_RISK_CAP", "150000"))
MAX_CONSERVATIVE_ALERTS_PER_DAY = int(os.getenv("MAX_CONSERVATIVE_ALERTS_PER_DAY", "8"))
MAX_COMBO_ALERTS_PER_DAY = int(os.getenv("MAX_COMBO_ALERTS_PER_DAY", "3"))

DB_PATH = os.getenv("DB_PATH", os.getenv("DATABASE_PATH", "data/arb_scanner.db"))
# Single-active execution queue
EXECUTION_TTL_SECONDS = int(os.getenv("EXECUTION_TTL_SECONDS", "120"))
EXECUTION_QUEUE_MAX = int(os.getenv("EXECUTION_QUEUE_MAX", "25"))
# Backward-compatible alias (queue size)
EXECUTION_TOP_N = int(os.getenv("EXECUTION_TOP_N", str(EXECUTION_QUEUE_MAX)))
# Max age for Telegram alerts (odds go stale fast). Also used to purge open queue.
ALERT_MAX_AGE_SECONDS = float(os.getenv("ALERT_MAX_AGE_SECONDS", "90"))
# Risk limits (0 = disabled). Override via Railway env without code changes.
MAX_EXPOSURE_DIARIA = float(os.getenv("MAX_EXPOSURE_DIARIA", "5000"))
MAX_EXPOSURE_SIMULTANEA = float(os.getenv("MAX_EXPOSURE_SIMULTANEA", "500"))
# Max new arb alerts to enqueue/send per scan cycle (best ROI first).
MAX_ALERTS_PER_CYCLE = int(os.getenv("MAX_ALERTS_PER_CYCLE", "3"))

_DEFAULT_BOOKS = (
    "betplay",
    "wplay",
    "betano",
    "rushbet",
    "zamba",
    "codere",
)


def _load_book_capitals() -> dict[str, float]:
    """Per-book bankroll from BOOK_CAPITAL_<BOOK> or fallback TOTAL_INVESTMENT."""
    capitals: dict[str, float] = {}
    fallback = float(os.getenv("TOTAL_INVESTMENT", str(TOTAL_INVESTMENT)))
    for book in _DEFAULT_BOOKS:
        raw = os.getenv(f"BOOK_CAPITAL_{book.upper()}", "").strip()
        if raw:
            capitals[book] = float(raw)
        else:
            capitals[book] = fallback
    return capitals

# Secrets from .env only — never hardcode
# TELEGRAM_TOKEN (Railway) or TELEGRAM_BOT_TOKEN (.env local)
TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TELEGRAM_TOKEN = TELEGRAM_BOT_TOKEN


@dataclass(frozen=True)
class Config:
    # Scan loop
    scan_interval_seconds: int = 60
    request_timeout_seconds: float = 15.0

    # Arbitrage thresholds
    min_profit_percent: float = MIN_MARGIN_THRESHOLD
    max_stake_total: float = TOTAL_INVESTMENT

    # Active bookmakers (keys under scrapers/)
    active_bookmakers: tuple[str, ...] = (
        "betplay",
        "wplay",
        "betano",
        "rushbet",
        "zamba",
        "codere",
    )

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    log_date_format: str = "%Y-%m-%d %H:%M:%S"

    # Telegram (loaded from env — never hardcode secrets)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False

    # Storage
    database_path: Path = BASE_DIR / "data" / "arb_scanner.db"

    # Execution Manager (single active + ranked queue)
    book_capitals: tuple[tuple[str, float], ...] = ()
    execution_top_n: int = EXECUTION_QUEUE_MAX  # max queued candidates
    execution_ttl_seconds: int = EXECUTION_TTL_SECONDS
    execution_queue_max: int = EXECUTION_QUEUE_MAX
    alert_max_age_seconds: float = ALERT_MAX_AGE_SECONDS
    max_exposure_diaria: float = MAX_EXPOSURE_DIARIA
    max_exposure_simultanea: float = MAX_EXPOSURE_SIMULTANEA
    max_alerts_per_cycle: int = MAX_ALERTS_PER_CYCLE

    def book_capital_map(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.book_capitals}


def get_config() -> Config:
    """Build config from defaults + environment variables."""
    token = (
        os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    ).strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        logging.getLogger(__name__).error(
            "Missing TELEGRAM variables. Please set TELEGRAM_TOKEN "
            "(or TELEGRAM_BOT_TOKEN) and TELEGRAM_CHAT_ID in Railway."
        )

    db_raw = (
        os.getenv("DB_PATH", "").strip()
        or os.getenv("DATABASE_PATH", "").strip()
        or DB_PATH
    )
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    total_investment = float(os.getenv("TOTAL_INVESTMENT", str(TOTAL_INVESTMENT)))
    min_margin = float(os.getenv("MIN_MARGIN_THRESHOLD", str(MIN_MARGIN_THRESHOLD)))
    queue_max = int(
        os.getenv(
            "EXECUTION_QUEUE_MAX",
            os.getenv("EXECUTION_TOP_N", str(EXECUTION_QUEUE_MAX)),
        )
    )
    ttl = int(os.getenv("EXECUTION_TTL_SECONDS", str(EXECUTION_TTL_SECONDS)))
    alert_max_age = float(
        os.getenv("ALERT_MAX_AGE_SECONDS", str(ALERT_MAX_AGE_SECONDS))
    )
    max_exp_day = float(os.getenv("MAX_EXPOSURE_DIARIA", str(MAX_EXPOSURE_DIARIA)))
    max_exp_sim = float(
        os.getenv("MAX_EXPOSURE_SIMULTANEA", str(MAX_EXPOSURE_SIMULTANEA))
    )
    max_alerts = int(os.getenv("MAX_ALERTS_PER_CYCLE", str(MAX_ALERTS_PER_CYCLE)))
    capitals = _load_book_capitals()

    return Config(
        min_profit_percent=min_margin,
        max_stake_total=total_investment,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        telegram_enabled=bool(token and chat_id),
        database_path=db_path,
        book_capitals=tuple(sorted(capitals.items())),
        execution_top_n=max(1, queue_max),
        execution_ttl_seconds=max(15, ttl),
        execution_queue_max=max(1, queue_max),
        alert_max_age_seconds=max(15.0, alert_max_age),
        max_exposure_diaria=max(0.0, max_exp_day),
        max_exposure_simultanea=max(0.0, max_exp_sim),
        max_alerts_per_cycle=max(1, max_alerts),
    )


def setup_logging(level: str | None = None) -> None:
    """Configure root logging with INFO/DEBUG/ERROR levels."""
    cfg = get_config()
    resolved = (level or cfg.log_level).upper()
    numeric = getattr(logging, resolved, logging.INFO)

    logging.basicConfig(
        level=numeric,
        format=cfg.log_format,
        datefmt=cfg.log_date_format,
        force=True,
    )
    if numeric > logging.DEBUG:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        # uvicorn access logs are a major Railway rate-limit source
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    logging.getLogger(__name__).debug("Logging initialized at %s", resolved)
