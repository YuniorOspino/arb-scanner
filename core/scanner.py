"""Orchestrates scrapers → arb calc → storage → alerts."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from alerts.telegram import TelegramAlerter
from config import Config
from core.arbitrage import find_opportunities
from core.models import ArbitrageOpportunity, MarketOdds, OddsQuote
from scrapers.base import BaseScraper
from scrapers.event_names import is_virtual_or_esport_event
from storage.database import OpportunityStore

logger = logging.getLogger(__name__)

# Wall-clock budget per bookmaker so one hung scraper cannot stall the cycle.
_SCRAPER_HARD_TIMEOUT = float(os.getenv("SCRAPER_HARD_TIMEOUT_SECONDS", "90"))


class ArbScanner:
    """Main scan cycle coordinator."""

    def __init__(
        self,
        config: Config,
        scrapers: list[BaseScraper],
        store: OpportunityStore,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        self.config = config
        self.scrapers = scrapers
        self.store = store
        self.alerter = alerter

    def collect_quotes(self) -> list[OddsQuote]:
        quotes: list[OddsQuote] = []
        for scraper in self.scrapers:
            name = scraper.bookmaker_name
            logger.info("Scraping %s ...", name)
            batch = self._fetch_odds_with_timeout(scraper, name)
            if batch is None:
                continue
            if not batch:
                logger.warning("Skipping %s: empty quotes (no fresh data)", name)
                continue
            logger.info("Got %d quotes from %s", len(batch), name)
            quotes.extend(batch)
        return quotes

    def _fetch_odds_with_timeout(
        self, scraper: BaseScraper, name: str
    ) -> list[OddsQuote] | None:
        """Run fetch_odds under a hard timeout; never block the scan loop forever."""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(scraper.fetch_odds)
            try:
                return future.result(timeout=_SCRAPER_HARD_TIMEOUT)
            except FuturesTimeout:
                logger.warning(
                    "Scraper timed out after %.0fs: %s — skipping",
                    _SCRAPER_HARD_TIMEOUT,
                    name,
                )
                future.cancel()
                return None
            except Exception:
                logger.exception("Scraper failed: %s — skipping", name)
                return None
        finally:
            # Do not wait for a hung request thread; let it die when its HTTP timeout fires.
            pool.shutdown(wait=False, cancel_futures=True)

    def group_markets(self, quotes: list[OddsQuote]) -> list[MarketOdds]:
        """Group flat quotes into markets by event + inferred market key."""
        buckets: dict[tuple[str, str], list[OddsQuote]] = defaultdict(list)
        for q in quotes:
            market_key = q.market_id or "default"
            buckets[(q.event_name, market_key)].append(q)

        markets: list[MarketOdds] = []
        for (event_name, market_type), qs in buckets.items():
            markets.append(
                MarketOdds(event_name=event_name, market_type=market_type, quotes=qs)
            )
        logger.debug("Grouped into %d markets", len(markets))
        return markets

    def run_once(self) -> list[ArbitrageOpportunity]:
        logger.info("=== Scan cycle start ===")
        quotes = self.collect_quotes()
        if not quotes:
            logger.warning("No quotes collected; skipping arb calculation")
            return []

        markets = self.group_markets(quotes)
        opportunities = find_opportunities(
            markets,
            total_stake=self.config.max_stake_total,
            # Include low-profit arbs; Telegram classifies rentable vs poco rentable
            min_profit_percent=0.0,
        )

        newly_saved: list[ArbitrageOpportunity] = []
        for opp in opportunities:
            if is_virtual_or_esport_event(opp.event_name):
                logger.info(
                    "Skipping virtual/eSport opportunity: %s", opp.event_name
                )
                continue
            is_new = self.store.save_if_new(opp)
            if is_new:
                newly_saved.append(opp)
                logger.info("New opportunity persisted: %s", opp.event_name)
                # Telegram de oportunidades: solo vía main._send_active → pipeline launcher.
                # No llamar self.alerter.send_opportunity (formato antiguo desactivado).
                if self.alerter:
                    logger.debug(
                        "Alerter presente pero envío de oportunidades desactivado "
                        "(usar pipeline launcher en main.py): %s",
                        opp.event_name,
                    )
            else:
                logger.debug("Duplicate opportunity skipped: %s", opp.event_name)

        logger.info(
            "=== Scan cycle done (%d found, %d new) ===",
            len(opportunities),
            len(newly_saved),
        )
        return newly_saved
