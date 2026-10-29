import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.master_data import ASSET_TYPE_STOCK
from src.research_contracts import (
    ClaimComparison,
    InvestmentThesis,
    ResearchHorizon,
    ThesisIndicator,
    ViewOrigin,
    disclosure_deadline,
)
from src.research_ledger import (
    ClaimResult,
    LedgerEntryType,
    LedgerSettlementService,
    MetricPoint,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
    parse_fin_metric_id,
)
from src.research_orchestration.thesis import ThesisService, thesis_views

ASSET = "asset:company-1"
SERIES = {
    "recurring_profit_growth": (
        ("2026-06-30", "2026-08-20", "0.12"),
        ("2026-09-30", "2026-10-25", "0.08"),
        ("2026-12-31", "2027-03-28", "0.15"),
    ),
    "gross_margin": (
        ("2026-06-30", "2026-08-20", "0.90"),
        ("2026-09-30", "2026-10-25", "0.85"),
        ("2026-12-31", "2027-03-28", "0.80"),
    ),
    "indicator.roe": (
        ("2026-06-30", "2026-08-20", "0.05"),
        ("2026-09-30", "2026-10-25", "0.075"),
        ("2026-12-31", "2027-03-28", "0.10"),
        ("2027-03-31", "2027-04-25", "0.03"),
    ),
}


class _Metrics:
    def known(self, metric_id):
        return parse_fin_metric_id(metric_id) is not None

    def series(self, metric_id, start, end):
        _, metric = parse_fin_metric_id(metric_id)
        return tuple(
            MetricPoint(period, published, Decimal(value))
            for period, published, value in SERIES[metric]
            if published <= end and period >= start
        )


class _MasterData:
    def get_asset(self, asset_id):
        return SimpleNamespace(asset_id=asset_id, asset_type=ASSET_TYPE_STOCK)


def _thesis(**changes):
    values = dict(
        thesis_id="thesis:test-1",
        asset_id=ASSET,
        origin=ViewOrigin.USER,
        user_id="user:a",
        summary="高端白酒提价带动利润增长",
        as_of_date="2026-09-01",
        expires_on="2027-06-30",
        horizon=ResearchHorizon.MEDIUM_LONG,
        indicators=(
            ThesisIndicator(
                "growth",
                "recurring_profit_growth",
                "扣非净利润同比（TTM）",
                "2026-12-31",
                expected_low=Decimal("0.10"),
                expected_high=Decimal("0.30"),
                invalidation_comparison=ClaimComparison.BELOW,
                invalidation_threshold=Decimal("0"),
            ),
            ThesisIndicator(
                "margin",
                "gross_margin",
                "毛利率（TTM）",
                "2026-12-31",
                expected_low=Decimal("0.88"),
                invalidation_comparison=ClaimComparison.BELOW,
                invalidation_threshold=Decimal("0.82"),
            ),
        ),
    )
    values.update(changes)
    return InvestmentThesis(**values)


class ThesisContractTest(unittest.TestCase):
    def test_disclosure_deadlines(self):
        self.assertEqual(disclosure_deadline("2026-12-31"), "2027-04-30")
        self.assertEqual(disclosure_deadline("2026-06-30"), "2026-08-31")
        with self.assertRaises(ValueError):
            disclosure_deadline("2026-05-31")

    def test_round_trip_keeps_reconstructed_flag(self):
        thesis = _thesis(reconstructed=True, original_date="2025-03-01")
        self.assertEqual(InvestmentThesis.from_dict(thesis.to_dict()), thesis)

    def test_rejects_untestable_or_inconsistent_theses(self):
        with self.assertRaises(ValueError):
            _thesis(as_of_date="2026-09-01", indicators=(_indicator(observe_period="2026-06-30"),))
        with self.assertRaises(ValueError):
            _thesis(expires_on="2027-03-31")
        with self.assertRaises(ValueError):
            _indicator(invalidation_threshold=Decimal("0.2"))
        with self.assertRaises(ValueError):
            ThesisIndicator("x", "roe", "ROE", "2026-12-31")
        with self.assertRaises(ValueError):
            _thesis(origin=ViewOrigin.SYSTEM, user_id=None, reconstructed=True)
        with self.assertRaises(ValueError):
            _thesis(original_date="2025-01-01")
        with self.assertRaises(ValueError):
            _thesis(indicators=tuple(_indicator(indicator_id=f"i{n}") for n in range(6)))

    def test_each_bound_becomes_a_metric_view_on_the_target_period(self):
        views = thesis_views(_thesis(reconstructed=True))
        self.assertEqual(len(views), 3)
        low = views[0]
        self.assertEqual(low.view_id, "thesis:test-1:growth:low")
        self.assertEqual(low.claim.metric_key, "level_at:2026-12-31")
        self.assertEqual(low.claim.comparison, ClaimComparison.ABOVE)
        self.assertEqual(low.settlement_rule.observed_id, f"fin:{ASSET}:recurring_profit_growth")
        self.assertTrue(low.proposition.startswith("（事后重建）"))
        self.assertIn("10.00%", low.proposition)
        self.assertEqual(views[1].claim.comparison, ClaimComparison.BELOW)
        self.assertIn("即论点失效", views[2].falsifiers[0])


class ThesisServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        repository = SQLiteResearchLedgerRepository(path)
        self.ledger = ResearchLedgerService(repository)
        self.metrics = _Metrics()
        self.service = ThesisService(ledger=self.ledger, metrics=self.metrics, master_data=_MasterData())
        self.settlement = LedgerSettlementService(
            repository=repository,
            prices=None,
            metrics=self.metrics,
            clock=lambda: datetime(2027, 9, 2, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_writes_thesis_and_views_with_latest_known_values(self):
        result = self.service.record(_thesis())
        self.assertEqual(len(result["view_entries"]), 3)
        self.assertEqual(result["latest_known"]["growth"]["period_end"], "2026-06-30")
        self.assertEqual(result["warnings"], [])
        (entry,) = self.ledger.list_entries(user_id="user:a", entry_type=LedgerEntryType.THESIS)
        self.assertEqual(entry.subject_id, ASSET)
        self.assertEqual(entry.scope.value, "personal")
        self.assertEqual(self.ledger.list_entries(user_id="user:b", entry_type=LedgerEntryType.THESIS), ())

    def test_record_rejects_a_target_period_already_published(self):
        thesis = _thesis(
            as_of_date="2026-10-26",
            indicators=(_indicator(observe_period="2026-09-30"),),
        )
        with self.assertRaisesRegex(ValueError, "已披露"):
            self.service.record(thesis)

    def test_check_replays_awaiting_deviating_on_track_and_invalidated(self):
        self.service.record(_thesis())

        def check(as_of):
            (thesis,) = self.service.check(ASSET, as_of_date=as_of, user_id="user:a")["theses"]
            return thesis["status"], {item["indicator_id"]: item for item in thesis["indicators"]}

        status, items = check("2026-10-01")
        self.assertEqual(status, "awaiting_data")
        self.assertEqual(items["growth"]["reference"]["period_end"], "2026-06-30")

        status, items = check("2026-11-01")
        self.assertEqual(status, "deviating")
        self.assertEqual(items["growth"]["basis"], "interim")
        self.assertEqual(items["growth"]["status_label"], "偏离")

        status, items = check("2027-04-01")
        self.assertEqual(status, "invalidated")
        self.assertEqual(items["growth"]["status"], "on_track")
        self.assertEqual(items["growth"]["basis"], "target_period")
        self.assertEqual(items["margin"]["status"], "invalidated")
        self.assertIn("2026-12-31", items["margin"]["note"])

    def test_other_users_and_expired_theses_are_not_checked(self):
        self.service.record(_thesis())
        self.assertEqual(self.service.check(ASSET, as_of_date="2026-11-01", user_id="user:b")["theses"], [])
        result = self.service.check(ASSET, as_of_date="2027-07-01", user_id="user:a")
        self.assertEqual(result["theses"], [])
        self.assertIn("record-thesis", result["note"])

    def test_year_to_date_indicators_compare_only_the_same_quarter(self):
        self.service.record(
            _thesis(
                indicators=(
                    _indicator(
                        indicator_id="roe",
                        metric="indicator.roe",
                        label="披露 ROE",
                        expected_low=Decimal("0.09"),
                        invalidation_threshold=Decimal("0.07"),
                    ),
                )
            )
        )

        def status(as_of):
            (thesis,) = self.service.check(ASSET, as_of_date=as_of, user_id="user:a")["theses"]
            return thesis["indicators"][0]["status"]

        self.assertEqual(status("2026-11-01"), "awaiting_data")
        self.assertEqual(status("2027-05-01"), "on_track")

    def test_views_settle_on_the_target_report_period(self):
        recorded = self.service.record(_thesis())
        self.settlement.settle_due("2027-09-02")
        results = {}
        for item in recorded["view_entries"]:
            settlements = self.ledger.list_settlements(item["entry_id"], user_id="user:a")
            by_date = {s.checkpoint_date: s for s in settlements}
            self.assertEqual(by_date["2027-02-28"].status, SettlementStatus.NOT_SETTLEABLE)
            self.assertIn("2027-04-30", by_date["2027-02-28"].note)
            results[item["proposition"]] = by_date["2027-09-01"]
        outcomes = sorted((s.claim_result for s in results.values()), key=lambda r: r.value)
        self.assertEqual(outcomes, [ClaimResult.MET, ClaimResult.MET, ClaimResult.MISSED])
        missed = next(s for s in results.values() if s.claim_result is ClaimResult.MISSED)
        self.assertEqual(missed.observed_value, Decimal("0.80"))
        self.assertEqual(missed.reference_date, "2026-06-30")


def _indicator(**changes):
    values = dict(
        indicator_id="growth",
        metric="recurring_profit_growth",
        label="扣非净利润同比（TTM）",
        observe_period="2026-12-31",
        expected_low=Decimal("0.10"),
        invalidation_comparison=ClaimComparison.BELOW,
        invalidation_threshold=Decimal("0"),
    )
    values.update(changes)
    return ThesisIndicator(**values)


if __name__ == "__main__":
    unittest.main()
