import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.macro_data import MacroDataService, SQLiteMacroDataRepository
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
    LedgerScope,
    MacroMetricSeriesSource,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
)
from src.research_orchestration.view_recording import ViewRecorder


class ViewRecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        self.ledger = ResearchLedgerService(SQLiteResearchLedgerRepository(path))
        self.recorder = ViewRecorder(
            ledger=self.ledger,
            metrics=MacroMetricSeriesSource(
                macro_data=MacroDataService(SQLiteMacroDataRepository(path))
            ),
            master_data=MasterDataService(SQLiteMasterDataRepository(path)),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_falsifiable_rewrite_supersedes_the_raw_user_view(self):
        raw = self.ledger.record_user_view(
            _view("view:raw", claim=None, rule=None),
            source_ref="request:q7",
        )

        result = self.recorder.record(
            _view("view:rewrite"),
            supersedes_entry_id=raw.entry_id,
        )

        self.assertEqual(result["scope"], LedgerScope.PERSONAL.value)
        self.assertTrue(result["settleable"])
        self.assertEqual(
            self.ledger.list_entries(user_id="user:a", active_on="2026-11-01")[0].entry_id,
            result["ledger_entry_id"],
        )

    def test_system_view_is_objective(self):
        result = self.recorder.record(
            _view(
                "view:system",
                origin=ViewOrigin.SYSTEM,
                falsifiers=("PMI 连续三个月高于 50",),
            )
        )

        self.assertEqual(result["scope"], LedgerScope.OBJECTIVE.value)

    def test_unsettleable_metric_views_are_rejected(self):
        cases = {
            "unknown series": _view(
                "view:unknown",
                rule=SettlementRule(
                    SettlementMethod.METRIC_OBSERVATION,
                    "macro:no_such_series",
                ),
            ),
            "unsupported key": _view(
                "view:key",
                claim=ViewClaim("cpi_yoy", ClaimComparison.BELOW, Decimal("0")),
            ),
            "unknown price asset": _view(
                "view:index",
                claim=ViewClaim("total_return", ClaimComparison.BELOW, Decimal("-0.2")),
                rule=SettlementRule(
                    SettlementMethod.PRICE_RELATIVE,
                    "index:cn_a:sh:000300",
                ),
            ),
        }
        for name, view in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.recorder.record(view)
        self.assertEqual(self.ledger.list_entries(user_id="user:a"), ())


def _view(
    view_id,
    *,
    claim=ViewClaim("min_level", ClaimComparison.BELOW, Decimal("48")),
    rule=SettlementRule(
        SettlementMethod.METRIC_OBSERVATION,
        "macro:pmi_manufacturing",
    ),
    origin=ViewOrigin.USER,
    falsifiers=(),
):
    return View(
        view_id=view_id,
        subject_type=SubjectType.MACRO,
        subject_id="macro:cn",
        origin=origin,
        user_id="user:a" if origin is ViewOrigin.USER else None,
        proposition="未来半年制造业 PMI 最低跌破 48",
        horizon=ResearchHorizon.MEDIUM_SHORT,
        as_of_date="2026-10-26",
        expires_on="2027-04-24",
        confidence=Confidence.MEDIUM,
        claim=claim,
        falsifiers=falsifiers,
        settlement_rule=rule,
    )


if __name__ == "__main__":
    unittest.main()
