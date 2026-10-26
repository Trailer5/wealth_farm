import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.data_src.macro_sources import _period_end
from src.data_src.models import MacroObservation
from src.macro_data import MacroDataService, SQLiteMacroDataRepository
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
    MacroMetricSeriesSource,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
)


def _cpi(period, value):
    return MacroObservation(
        series_id="cpi_yoy",
        period=period,
        period_end=_period_end(period),
        value=value,
        unit="%",
        source="akshare:macro_china_cpi",
    )


class MetricSettlementTest(unittest.TestCase):
    """CPI releases are inferred on the 15th of the next month."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        self.observations = [
            _cpi("2025-11", 0.5),
            _cpi("2025-12", -0.3),
            _cpi("2026-01", -1.0),
        ]
        macro = MacroDataService(
            SQLiteMacroDataRepository(path),
            fetcher=self._fetch,
        )
        repository = SQLiteResearchLedgerRepository(path)
        self.ledger = ResearchLedgerService(repository)
        self.settlement = LedgerSettlementService(
            repository=repository,
            prices=None,
            metrics=MacroMetricSeriesSource(macro_data=macro),
            clock=lambda: datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _fetch(self, source, series_id, start_date, end_date, timeout):
        if source != "akshare":
            return []
        return [
            item
            for item in self.observations
            if start_date <= item.period_end <= end_date
        ]

    def _settle(self, view, as_of_date):
        (entry,) = self.ledger.record(
            (view,),
            scope=LedgerScope.PERSONAL,
            user_id="user:a",
            source_ref="test",
        )
        self.settlement.settle_due(as_of_date)
        (settled,) = self.ledger.list_settlements(entry.entry_id, user_id="user:a")
        return settled

    def test_reference_is_known_value_and_only_releases_by_checkpoint_count(self):
        settled = self._settle(
            _view(ViewClaim("min_level", ClaimComparison.BELOW, Decimal("0"))),
            "2026-02-01",
        )

        self.assertEqual(settled.status, SettlementStatus.SETTLED)
        self.assertEqual(settled.claim_result, ClaimResult.MET)
        self.assertEqual(settled.reference_date, "2025-11-30")
        self.assertEqual(settled.reference_value, Decimal("0.5"))
        # 2026-01 is released on 2026-02-15, after the checkpoint.
        self.assertEqual(settled.observed_date, "2025-12-31")
        self.assertEqual(settled.observed_value, Decimal("-0.3"))
        self.assertIsNone(settled.asset_return)
        self.assertIn("1 期新数据", settled.note)

    def test_change_claim_compares_latest_minus_reference(self):
        settled = self._settle(
            _view(ViewClaim("change", ClaimComparison.ABOVE, Decimal("0"))),
            "2026-02-01",
        )

        self.assertEqual(settled.claim_result, ClaimResult.MISSED)
        self.assertIn("change = -0.8", settled.note)

    def test_level_at_settles_one_named_period(self):
        cases = {
            "2025-12-31": SettlementStatus.SETTLED,
            "2026-01-31": SettlementStatus.DATA_INSUFFICIENT,
            "2025-11-30": SettlementStatus.NOT_SETTLEABLE,
        }
        for period, status in cases.items():
            with self.subTest(period=period):
                settled = self._settle(
                    _view(
                        ViewClaim(
                            f"level_at:{period}",
                            ClaimComparison.BELOW,
                            Decimal("0"),
                        )
                    ),
                    "2026-02-01",
                )
                self.assertEqual(settled.status, status)
                if status is SettlementStatus.SETTLED:
                    self.assertEqual(settled.claim_result, ClaimResult.MET)
                    self.assertEqual(settled.observed_value, Decimal("-0.3"))

    def test_no_release_after_the_view_is_data_insufficient(self):
        self.observations = self.observations[:2]

        settled = self._settle(
            _view(
                ViewClaim("level", ClaimComparison.BELOW, Decimal("0")),
                as_of="2026-01-20",
                expires="2026-02-19",
            ),
            "2026-02-19",
        )

        self.assertEqual(settled.status, SettlementStatus.DATA_INSUFFICIENT)
        self.assertIn("没有发布新一期", settled.note)

    def test_unknown_metric_id_is_data_insufficient_with_hint(self):
        settled = self._settle(
            _view(None, metric_id="cpi_yoy"),
            "2026-02-01",
        )

        self.assertEqual(settled.status, SettlementStatus.DATA_INSUFFICIENT)
        self.assertIn("macro:cpi_yoy", settled.note)


def _view(claim, *, metric_id="macro:cpi_yoy", as_of="2026-01-02", expires="2026-02-01"):
    return View(
        view_id=f"view:test:{claim.metric_key if claim else 'none'}:{as_of}:{metric_id}",
        subject_type=SubjectType.MACRO,
        subject_id="macro:cn",
        origin=ViewOrigin.USER,
        user_id="user:a",
        proposition="CPI 一个月内转负",
        horizon=ResearchHorizon.SHORT,
        as_of_date=as_of,
        expires_on=expires,
        confidence=Confidence.MEDIUM,
        claim=claim,
        settlement_rule=SettlementRule(
            SettlementMethod.METRIC_OBSERVATION,
            metric_id,
        ),
    )


if __name__ == "__main__":
    unittest.main()
