"""Age filter + EM promote must not activate items Telegram would reject."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load filtro_roi without importing alerts package __init__ (heavy deps).
_event_names = _load_module("scrapers.event_names", "scrapers/event_names.py")
sys.modules["scrapers.event_names"] = _event_names
filtro_roi = _load_module("alerts.filtro_roi_standalone", "alerts/filtro_roi.py")

from core.models import ArbitrageOpportunity  # noqa: E402
from storage.database import OpportunityStore  # noqa: E402


def _opp(*, age_s: float, event: str = "a vs b", profit: float = 2.0) -> ArbitrageOpportunity:
    detected = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return ArbitrageOpportunity(
        event_name=event,
        market_type="1x2",
        profit_percent=profit,
        total_stake=100.0,
        legs=(
            ("wplay", "1", 2.10, 50.0),
            ("betplay", "2", 2.10, 50.0),
        ),
        detected_at=detected,
    )


class TestAgeCalc(unittest.TestCase):
    def test_utc_naive_and_aware_agree(self) -> None:
        now = datetime.now(timezone.utc)
        detected = now - timedelta(seconds=45)
        age, parsed, _now2 = filtro_roi.edad_detalle({"detected_at": detected.isoformat()})
        self.assertIsNotNone(age)
        assert age is not None
        self.assertAlmostEqual(age, 45.0, delta=1.5)
        self.assertEqual(parsed.tzinfo, timezone.utc)

        naive = detected.replace(tzinfo=None).isoformat()
        age_n, parsed_n, _ = filtro_roi.edad_detalle({"detected_at": naive})
        self.assertIsNotNone(age_n)
        assert age_n is not None
        self.assertAlmostEqual(age_n, age, delta=1.0)
        self.assertEqual(parsed_n.tzinfo, timezone.utc)

    def test_fresh_passes_stale_rejected(self) -> None:
        os.environ["ALERT_MAX_AGE_SECONDS"] = "90"
        fresh = {
            "roi": 2.0,
            "partido": "shamrock rovers vs dundalk",
            "detected_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        }
        stale = {
            "roi": 2.0,
            "partido": "shamrock rovers vs dundalk",
            "detected_at": (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat(),
            "ejecucion": 21,
        }
        self.assertEqual(filtro_roi.clasificar_alerta(fresh), "VALIDA")
        self.assertEqual(filtro_roi.clasificar_alerta(stale), "DESCARTADA_EDAD")


class TestEmAgeAlign(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "t.db"
        self.store = OpportunityStore(
            self.db,
            book_capitals={"wplay": 1000.0, "betplay": 1000.0},
            ttl_seconds=120,
            queue_max=25,
            alert_max_age_seconds=90.0,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_does_not_activate_between_90_and_120(self) -> None:
        """Regression: TTL=120 used to activate items Telegram rejects at 90."""
        now = datetime.now(timezone.utc)
        opps = [
            _opp(age_s=5, event=f"team{i} vs other{i}", profit=3.0 + i * 0.1)
            for i in range(5)
        ]
        sent = self.store.process_execution_cycle(opps)
        self.assertIsNotNone(sent)

        past = (now - timedelta(seconds=100)).isoformat()
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE execution_queue SET detected_at = ? "
                "WHERE status IN ('queued','active','reserved')",
                (past,),
            )
            conn.execute(
                "UPDATE execution_queue SET status='expired' "
                "WHERE status IN ('active','reserved')"
            )

        nxt = self.store.promote_next_active()
        self.assertIsNone(nxt, "must not promote items aged 100s when max_age=90")

        with self.store._connect() as conn:
            open_n = conn.execute(
                "SELECT COUNT(*) AS n FROM execution_queue "
                "WHERE status IN ('queued','active','reserved')"
            ).fetchone()["n"]
        self.assertEqual(int(open_n), 0)

    def test_fresh_still_promotes(self) -> None:
        opp = _opp(age_s=20, event="shamrock rovers vs dundalk", profit=2.5)
        active = self.store.process_execution_cycle([opp])
        self.assertIsNotNone(active)
        assert active is not None
        self.assertIn("shamrock", active["event_name"].lower())
        age = (datetime.now(timezone.utc) - active["detected_at"]).total_seconds()
        self.assertLess(age, 90)


if __name__ == "__main__":
    unittest.main()
