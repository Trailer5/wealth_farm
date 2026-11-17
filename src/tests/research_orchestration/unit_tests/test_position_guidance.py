import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.data_src.models import SecurityInfo
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.research_ledger import ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.position_guidance import PositionGuidanceService


class PositionGuidanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        master = MasterDataService(SQLiteMasterDataRepository(path))
        self.amd = master.register_us_listing(
            SecurityInfo(symbol="AMD", name="AMD", security_type="stock", exchange="nasdaq", source="nasdaq_us")
        ).asset_id
        self.qqq = master.register_us_listing(
            SecurityInfo(symbol="QQQ", name="QQQ", security_type="exchange_fund", exchange="nasdaq", source="nasdaq_us")
        ).asset_id
        self.service = PositionGuidanceService(
            ledger=ResearchLedgerService(SQLiteResearchLedgerRepository(path)), master_data=master
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _record(self, as_of, target, **kwargs):
        return self.service.record(
            user_id="user:a",
            asset_id=self.amd,
            as_of_date=as_of,
            target=Decimal(target),
            low=Decimal("0"),
            high=Decimal("0.10"),
            review_on=kwargs.pop("review_on", "2027-02-15"),
            basis="concentration cap and stress budget",
            benchmark_id=self.qqq,
            **kwargs,
        )

    def test_conclusions_compare_with_the_previous_recommendation(self):
        first = self._record("2026-11-15", "0.05")
        cases = (("2027-02-15", "0.05", "hold"), ("2027-05-15", "0.03", "reduce"), ("2027-08-15", "0.06", "add"), ("2027-11-15", "0", "exit"))

        self.assertEqual(first["conclusion_label"], "首期基准")
        for as_of, target, expected in cases:
            with self.subTest(as_of):
                result = self._record(as_of, target, review_on="2028-01-01")
                self.assertEqual(result["conclusion"], expected)
        history = self.service.history(user_id="user:a", asset_id=self.amd)
        self.assertEqual([row["target"] for row in history], ["0.05", "0.05", "0.03", "0.06", "0"])

    def test_dry_run_does_not_record_and_bad_ranges_are_rejected(self):
        result = self._record("2026-11-15", "0.05", dry_run=True)

        self.assertFalse(result["recorded"])
        self.assertEqual(self.service.history(user_id="user:a", asset_id=self.amd), [])
        with self.assertRaises(ValueError):
            self._record("2026-11-15", "0.2")

    def test_due_lists_assets_past_their_review_date(self):
        self._record("2026-11-15", "0.05")

        self.assertEqual(self.service.due(user_id="user:a", as_of_date="2027-02-14"), [])
        (row,) = self.service.due(user_id="user:a", as_of_date="2027-02-15")
        self.assertEqual(row["symbol"], "AMD")

    def test_first_recommendation_against_current_share_opens_a_position(self):
        result = self._record("2026-11-17", "0.02", current=Decimal("0"))

        self.assertEqual(result["conclusion"], "add")
        self.assertEqual(result["conclusion_label"], "首期：建仓")
        reduce = self._record("2026-11-17", "0.03", current=Decimal("0.185"))
        self.assertEqual(reduce["conclusion"], "reduce")

    def test_same_day_record_revises_that_day(self):
        first = self._record("2026-11-15", "0.05")
        revised = self._record("2026-11-15", "0", current=Decimal("0.185"))

        self.assertEqual(revised["revises"], first["ledger_entry_id"])
        history = self.service.history(user_id="user:a", asset_id=self.amd)
        self.assertEqual([(row["as_of_date"], row["target"]) for row in history], [("2026-11-15", "0")])
        later = self._record("2026-12-01", "0")
        self.assertEqual(later["previous"]["target"], "0")


if __name__ == "__main__":
    unittest.main()
