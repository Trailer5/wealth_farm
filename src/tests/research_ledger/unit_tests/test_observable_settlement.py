import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.event_intel import EventIntelService, Observable, SQLiteEventIntelRepository
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
    ClaimResult,
    LedgerScope,
    LedgerSettlementService,
    ObservableMetricSeriesSource,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
)

OBS = "obs:us-bis-se-asia-rule"
# medium_short from 2026-11-15: checkpoints 2027-02-13 and 2027-05-14, then the expiry 2027-06-30.
FIRST, SECOND, FINAL = "2027-02-13", "2027-05-14", "2027-06-30"


class ObservableSettlementTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        self.intel = EventIntelService(
            SQLiteEventIntelRepository(path),
            fetch=lambda task, payload: [],
            clock=lambda: datetime(2027, 7, 1, tzinfo=timezone.utc),
        )
        baseline = self.intel.add_evidence(url="https://example.com/none", title="no rule yet", published_on="2026-11-15")
        self.intel.define_observable(
            Observable(OBS, "BIS rule", "event", "0/1", "BIS rule in the Federal Register", "2026-11-15"),
            {
                "period_end": "2026-11-15",
                "value": "0",
                "available_at": "2026-11-15",
                "evidence_refs": [baseline["item_id"]],
                "note": "not yet",
            },
        )
        repository = SQLiteResearchLedgerRepository(path)
        self.ledger = ResearchLedgerService(repository)
        self.settlement = LedgerSettlementService(
            repository=repository,
            prices=None,
            metrics=ObservableMetricSeriesSource(intel=self.intel),
            clock=lambda: datetime(2027, 7, 1, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _observe(self, day, value, published=None):
        evidence = self.intel.add_evidence(url=f"https://example.com/{day}", title="rule", published_on=published or day)
        self.intel.record_observation(
            {
                "observable_id": OBS,
                "period_end": day,
                "value": value,
                "available_at": published or day,
                "evidence_refs": [evidence["item_id"]],
                "note": "checked",
            }
        )

    def _settle(self, comparison, as_of_date):
        (entry,) = self.ledger.record(
            (_view(comparison),), scope=LedgerScope.PERSONAL, user_id="user:a", source_ref="test"
        )
        self.settlement.settle_due(as_of_date)
        return entry, {item.checkpoint_date: item for item in self.ledger.list_settlements(entry.entry_id, user_id="user:a")}

    def test_event_that_happens_settles_at_the_next_checkpoint(self):
        self._observe("2027-01-10", "1")

        _, settled = self._settle(ClaimComparison.ABOVE, FINAL)

        self.assertEqual(settled[FIRST].claim_result, ClaimResult.MET)
        self.assertEqual(settled[FIRST].observed_value, Decimal(1))
        self.assertIn("Agent 依据证据记录的观测", settled[FIRST].note)

    def test_event_published_after_a_checkpoint_does_not_settle_it(self):
        self._observe("2027-01-10", "1", published="2027-03-01")

        _, settled = self._settle(ClaimComparison.ABOVE, FINAL)

        self.assertEqual(settled[FIRST].status, SettlementStatus.NOT_SETTLEABLE)
        self.assertEqual(settled[SECOND].claim_result, ClaimResult.MET)

    def test_undecided_event_waits_for_a_status_at_expiry(self):
        entry, settled = self._settle(ClaimComparison.ABOVE, FINAL)

        self.assertEqual(settled[FIRST].status, SettlementStatus.NOT_SETTLEABLE)
        self.assertIn("到期日", settled[FIRST].note)
        self.assertEqual(settled[FINAL].status, SettlementStatus.DATA_INSUFFICIENT)

        self._observe(FINAL, "0")
        self.settlement.settle_due(FINAL)
        (latest,) = [
            item
            for item in self.ledger.list_settlements(entry.entry_id, user_id="user:a")
            if item.status is SettlementStatus.SETTLED
        ]

        self.assertEqual((latest.checkpoint_date, latest.claim_result), (FINAL, ClaimResult.MISSED))

    def test_will_not_happen_claim_is_missed_once_it_happens(self):
        self._observe("2027-01-10", "1")

        _, settled = self._settle(ClaimComparison.BELOW, FINAL)

        self.assertEqual(settled[FIRST].claim_result, ClaimResult.MISSED)

    def test_value_series_at_expiry_settles_without_a_status_near_the_deadline(self):
        value_id = "obs:quarterly-margin"
        evidence = self.intel.add_evidence(url="https://example.com/q", title="10-Q", published_on="2026-08-26")
        self.intel.define_observable(
            Observable(value_id, "margin", "value", "ratio", "GAAP gross margin per quarter", "2026-11-15"),
            {"period_end": "2026-07-26", "value": "0.75", "available_at": "2026-08-26",
             "evidence_refs": [evidence["item_id"]], "note": "Q2"},
        )
        later = self.intel.add_evidence(url="https://example.com/q4", title="10-K", published_on="2027-02-25")
        self.intel.record_observation(
            {"observable_id": value_id, "period_end": "2027-01-31", "value": "0.73", "available_at": "2027-02-25",
             "evidence_refs": [later["item_id"]], "note": "Q4"}
        )
        margin_view = View(
            view_id="view:test:margin",
            subject_type=SubjectType.MACRO,
            subject_id="macro:us",
            origin=ViewOrigin.USER,
            user_id="user:a",
            proposition="margin below 70% in some quarter",
            horizon=ResearchHorizon.MEDIUM_SHORT,
            as_of_date="2026-11-15",
            expires_on=FINAL,
            confidence=Confidence.MEDIUM,
            claim=ViewClaim("min_level", ClaimComparison.BELOW, Decimal("0.70")),
            settlement_rule=SettlementRule(SettlementMethod.METRIC_OBSERVATION, value_id),
        )
        (entry,) = self.ledger.record((margin_view,), scope=LedgerScope.PERSONAL, user_id="user:a", source_ref="test")
        self.settlement.settle_due(FINAL)
        settled = {item.checkpoint_date: item for item in self.ledger.list_settlements(entry.entry_id, user_id="user:a")}

        self.assertEqual(settled[SECOND].status, SettlementStatus.NOT_SETTLEABLE)
        self.assertEqual((settled[FINAL].claim_result, settled[FINAL].observed_value), (ClaimResult.MISSED, Decimal("0.73")))


def _view(comparison):
    return View(
        view_id=f"view:test:{comparison.value}",
        subject_type=SubjectType.MACRO,
        subject_id="macro:us",
        origin=ViewOrigin.USER,
        user_id="user:a",
        proposition="BIS publishes a rule on SE Asia AI chips by 2027-06-30",
        horizon=ResearchHorizon.MEDIUM_SHORT,
        as_of_date="2026-11-15",
        expires_on=FINAL,
        confidence=Confidence.MEDIUM,
        claim=ViewClaim("max_level", comparison, Decimal("0.5")),
        settlement_rule=SettlementRule(SettlementMethod.METRIC_OBSERVATION, OBS),
    )


if __name__ == "__main__":
    unittest.main()
