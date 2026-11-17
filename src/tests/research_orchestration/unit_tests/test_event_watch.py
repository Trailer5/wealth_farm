import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.data_src.models import NewsItem
from src.event_intel import EventIntelService, Observable, SQLiteEventIntelRepository
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.research_contracts import (
    ClaimComparison,
    Confidence,
    ResearchHorizon,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    View,
    ViewClaim,
    ViewOrigin,
)
from src.research_ledger import (
    CompositeMetricSource,
    ObservableMetricSeriesSource,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
)
from src.research_orchestration.event_watch import ObservableWatchService, watch_next_actions
from src.research_orchestration.view_recording import ViewRecorder

OBS = "obs:huang-returns-to-nvidia"


class EventWatchTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        self.queries = []
        self.intel = EventIntelService(
            SQLiteEventIntelRepository(path),
            fetch=self._fetch,
            clock=lambda: datetime(2026, 11, 15, tzinfo=timezone.utc),
        )
        evidence = self.intel.add_evidence(url="https://example.com/8k", title="8-K 5.02", published_on="2026-11-12")
        self.intel.define_observable(
            Observable(
                OBS,
                "Huang holds any NVIDIA role",
                "event",
                "0/1",
                "NVIDIA filing or release naming Huang as officer, director or adviser",
                "2026-11-15",
                watch_queries=("en:Jensen Huang return Nvidia",),
            ),
            {"period_end": "2026-11-12", "value": "0", "available_at": "2026-11-12",
             "evidence_refs": [evidence["item_id"]], "note": "resigned all roles"},
        )
        self.ledger = ResearchLedgerService(SQLiteResearchLedgerRepository(path))
        self.recorder = ViewRecorder(
            ledger=self.ledger,
            metrics=CompositeMetricSource(ObservableMetricSeriesSource(intel=self.intel)),
            master_data=MasterDataService(SQLiteMasterDataRepository(path)),
        )
        self.watch = ObservableWatchService(ledger=self.ledger, intel=self.intel)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _fetch(self, task, payload):
        self.queries.append(payload)
        return [
            NewsItem(source="google_news", kind="news", title="Shareholders say Huang return is an option",
                     url="https://example.com/n1", published_on="2026-11-14", publisher="WSJ", language="en")
        ]

    def test_recorded_view_is_watched_with_news_since_the_last_observation(self):
        self.recorder.record(_view("2026-11-15", "2026-12-31"))

        (row,) = self.watch.run(user_id="user:a", as_of_date="2026-11-20")

        self.assertEqual(row["views"][0]["status"], "open")
        self.assertEqual(self.queries[0]["since"], "2026-11-12")
        self.assertEqual(row["news"][0]["items"][0]["publisher"], "WSJ")
        (action,) = watch_next_actions([row], "2026-11-20")
        self.assertIn("record-observation", action["command"])

    def test_expired_view_without_settlement_is_due(self):
        self.recorder.record(_view("2026-11-15", "2026-12-31"))

        (row,) = self.watch.run(user_id="user:a", as_of_date="2027-01-05", allow_remote=False)

        self.assertEqual(row["views"][0]["status"], "due")
        self.assertIn("已到期", watch_next_actions([row], "2027-01-05")[0]["reason"])

    def test_view_dated_before_the_baseline_is_rejected(self):
        with self.assertRaises(ValueError):
            self.recorder.record(_view("2026-11-10", "2026-12-31"))

    def test_signposted_observable_without_views_still_gets_news(self):
        self.assertEqual(self.watch.run(user_id="user:a", as_of_date="2026-11-20"), [])

        (row,) = self.watch.run(user_id="user:a", as_of_date="2026-11-20", signposted=frozenset({OBS}))

        self.assertEqual(row["views"], [])
        self.assertTrue(row["signposted"])
        self.assertEqual(row["news"][0]["items"][0]["publisher"], "WSJ")
        self.assertIn("新闻", watch_next_actions([row], "2026-11-20")[0]["reason"])


def _view(as_of, expires):
    return View(
        view_id=f"view:test:{as_of}",
        subject_type=SubjectType.MACRO,
        subject_id="macro:us",
        origin=ViewOrigin.USER,
        user_id="user:a",
        proposition="黄仁勋在 2026 年内不会以任何身份回到英伟达",
        horizon=ResearchHorizon.SHORT,
        as_of_date=as_of,
        expires_on=expires,
        confidence=Confidence.HIGH,
        claim=ViewClaim("max_level", ClaimComparison.BELOW, Decimal("0.5")),
        settlement_rule=SettlementRule(SettlementMethod.METRIC_OBSERVATION, OBS),
    )


if __name__ == "__main__":
    unittest.main()
