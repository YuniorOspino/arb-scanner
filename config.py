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
DB_PATH = os.getenv("DB_PATH", os.getenv("DATABASE_PATH", "data/arb_scanner.db"))

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

    return Config(
        min_profit_percent=min_margin,
        max_stake_total=total_investment,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        telegram_enabled=bool(token and chat_id),
        database_path=db_path,
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

    logging.getLogger(__name__).debug("Logging initialized at %s", resolved)
