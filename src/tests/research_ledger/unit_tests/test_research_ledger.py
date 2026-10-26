import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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
    LedgerAccessError,
    LedgerConflictError,
    LedgerEntryType,
    LedgerScope,
    ResearchLedgerService,
    ReviewProcessClass,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
    new_review,
    new_settlement,
)


AS_OF = "2026-08-24"


class ResearchLedgerServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ledger.sqlite3"
        self.ledger = ResearchLedgerService(
            SQLiteResearchLedgerRepository(self.db_path),
            clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _record(self, view=None, user_id="user:a"):
        return self.ledger.record(
            (view or _view(),),
            scope=LedgerScope.PERSONAL,
            user_id=user_id,
            source_ref="final-report:test",
        )

    def test_rerecording_the_same_source_is_idempotent(self):
        first = self._record()
        second = self._record()

        self.assertEqual(first, second)
        self.assertEqual(
            len(self.ledger.list_entries(user_id="user:a")),
            1,
        )
        self.assertEqual(first[0].artifact, _view())

    def test_changing_a_recorded_fact_is_rejected(self):
        self._record()

        with self.assertRaises(LedgerConflictError):
            self._record(replace(_view(), proposition="改写后的结论"))

    def test_sql_update_and_delete_are_blocked(self):
        (entry,) = self._record()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with self.assertRaisesRegex(
                sqlite3.DatabaseError,
                "append-only",
            ):
                conn.execute(
                    "UPDATE research_ledger_entries SET subject_id = 'x'"
                )
            with self.assertRaisesRegex(
                sqlite3.DatabaseError,
                "append-only",
            ):
                conn.execute(
                    "DELETE FROM research_ledger_entries WHERE entry_id = ?",
                    [entry.entry_id],
                )

    def test_personal_entries_are_hidden_from_other_users(self):
        (entry,) = self._record()

        with self.assertRaises(LedgerAccessError):
            self.ledger.get_entry(entry.entry_id, user_id="user:b")
        self.assertEqual(self.ledger.list_entries(user_id="user:b"), ())
        self.assertEqual(self.ledger.list_entries(user_id=None), ())

    def test_user_views_must_be_personal_to_their_author(self):
        user_view = replace(
            _view(),
            view_id="view:user:crisis",
            origin=ViewOrigin.USER,
            user_id="user:a",
            claim=None,
        )

        entry = self.ledger.record_user_view(
            user_view,
            source_ref="request:q7",
        )

        self.assertEqual(entry.user_id, "user:a")
        with self.assertRaisesRegex(ValueError, "personal"):
            self.ledger.record(
                (user_view,),
                scope=LedgerScope.OBJECTIVE,
                user_id=None,
                source_ref="request:q7",
            )
        with self.assertRaisesRegex(ValueError, "user views"):
            self.ledger.record_user_view(_view(), source_ref="request:q7")

    def test_active_entries_exclude_expired_and_revised(self):
        (entry,) = self._record()
        revised = self.ledger.record_revision(
            replace(_view(), view_id="view:test:revised"),
            supersedes_entry_id=entry.entry_id,
            user_id="user:a",
            source_ref="final-report:revised",
        )

        active = self.ledger.list_entries(
            user_id="user:a",
            entry_type=LedgerEntryType.VIEW,
            active_on="2026-12-31",
        )
        expired = self.ledger.list_entries(
            user_id="user:a",
            active_on="2030-01-01",
        )

        self.assertEqual(active, (revised,))
        self.assertEqual(expired, ())

    def test_settlement_is_idempotent_and_reviews_cite_it(self):
        (entry,) = self._record()
        settlement = _settlement(entry.entry_id)

        first = self.ledger.append_settlement(settlement)
        rerun = self.ledger.append_settlement(
            replace(settlement, computed_at="2027-02-21T00:00:00+00:00")
        )
        review = self.ledger.append_review(
            new_review(
                entry_id=entry.entry_id,
                settlement_id=first.settlement_id,
                process_class=ReviewProcessClass.SOUND_PROCESS_FAVORABLE,
                lessons=("估值修复先于盈利兑现。",),
                recorded_at="2027-02-22T00:00:00+00:00",
            )
        )

        self.assertEqual(rerun, first)
        self.assertEqual(
            self.ledger.list_settlements(entry.entry_id, user_id="user:a"),
            (first,),
        )
        self.assertEqual(
            self.ledger.list_reviews(entry.entry_id, user_id="user:a"),
            (review,),
        )
        with self.assertRaisesRegex(ValueError, "cite a settlement"):
            self.ledger.append_review(
                new_review(
                    entry_id=entry.entry_id,
                    settlement_id="settlement-missing",
                    process_class=ReviewProcessClass.UNDETERMINED,
                    lessons=("缺少结算。",),
                    recorded_at="2027-02-22T00:00:00+00:00",
                )
            )

    def test_unsettled_checkpoints_need_reason_and_no_result(self):
        with self.assertRaisesRegex(ValueError, "need a note"):
            new_settlement(
                entry_id="ledger-x",
                checkpoint_date="2027-02-20",
                status=SettlementStatus.DATA_INSUFFICIENT,
                claim_result=ClaimResult.UNKNOWN,
                computed_at="2027-02-21T00:00:00+00:00",
            )
        with self.assertRaisesRegex(ValueError, "need reference"):
            new_settlement(
                entry_id="ledger-x",
                checkpoint_date="2027-02-20",
                status=SettlementStatus.SETTLED,
                claim_result=ClaimResult.MET,
                computed_at="2027-02-21T00:00:00+00:00",
            )


def _view() -> View:
    return View(
        view_id="view:test",
        subject_type=SubjectType.ASSET,
        subject_id="stock:test",
        origin=ViewOrigin.SYSTEM,
        proposition="测试标的未来半年跑赢基准",
        horizon=ResearchHorizon.MEDIUM_SHORT,
        as_of_date=AS_OF,
        expires_on="2027-02-20",
        confidence=Confidence.MEDIUM,
        claim=ViewClaim("total_return", ClaimComparison.OUTPERFORM),
        falsifiers=("经营现金流连续两个季度转负。",),
        settlement_rule=SettlementRule(
            SettlementMethod.PRICE_RELATIVE,
            "stock:test",
            benchmark_id="index:test",
        ),
    )


def _settlement(entry_id):
    return new_settlement(
        entry_id=entry_id,
        checkpoint_date="2027-02-20",
        status=SettlementStatus.SETTLED,
        claim_result=ClaimResult.MET,
        computed_at="2027-02-21T00:00:00+00:00",
        reference_date="2026-08-25",
        reference_value=Decimal("10"),
        observed_date="2027-02-19",
        observed_value=Decimal("12"),
        asset_return=Decimal("0.2"),
        benchmark_return=Decimal("0.05"),
        excess_return=Decimal("0.15"),
    )


if __name__ == "__main__":
    unittest.main()
