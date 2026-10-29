import tempfile
import unittest
from pathlib import Path

from src.research_ledger import ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.strategy_archive import StrategyArchiveError, StrategyArchiveService
from src.strategy import (
    STRATEGY_TEMPLATES,
    SQLiteStrategyArchiveRepository,
    TemplateError,
    archive_from_template,
    assess_template,
)
from src.strategy.signals import SIGNAL_GENERATORS

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class StrategyTemplateTest(unittest.TestCase):
    def test_every_template_passes_the_source_gate_and_has_a_generator(self):
        for template_id, template in STRATEGY_TEMPLATES.items():
            with self.subTest(template_id):
                self.assertTrue(assess_template(template_id).implementation_ready, assess_template(template_id))
                self.assertTrue((PROJECT_ROOT / template.source_ref).is_file())
                if template.kind != "selection":
                    self.assertIn(template_id, SIGNAL_GENERATORS)

    def test_ma200_description_maps_to_ma_timing(self):
        archive = archive_from_template(
            "ma_timing", strategy_id="csi300_ma200", universe_assets=("510300:exchange_fund",), parameters={"window": "200"}
        )
        self.assertEqual(archive.family, "ma_timing:510300")
        self.assertEqual(archive.template_id, "ma_timing")
        self.assertIn("200 日均线", archive.mechanism)
        self.assertEqual(archive.source_refs, ("docs/evidence/strategy_sources/ma_timing.md",))

    def test_selection_family_is_the_scope(self):
        archive = archive_from_template(
            "factor_selection", strategy_id="food_qv", parameters={"scope": "sw:801120", "rank_by": "book_yield"}
        )
        self.assertEqual(archive.family, "factor_selection:sw:801120")
        self.assertEqual(archive.parameters["top_n"], "10")

    def test_invalid_requests_are_refused_with_a_reason(self):
        cases = [
            ("no_such", {}, ()),
            ("ma_timing", {}, ("510300:exchange_fund", "510500:exchange_fund")),
            ("ma_timing", {"stop_loss": "0.1"}, ("510300:exchange_fund",)),
            ("ma_timing", {"window": "abc"}, ("510300:exchange_fund",)),
            ("factor_selection", {"scope": "sw:801120", "rank_by": "momentum"}, ()),
            ("factor_selection", {}, ()),
            ("fixed_weight_rebalance", {"weights": "510300=0.6,600000=0.4"}, ("510300:exchange_fund", "511010:exchange_fund")),
            ("valuation_percentile_timing", {"buy_below": "0.8"}, ("510300:exchange_fund",)),
            ("staged_entry", {}, ("510300:exchange_fund:defensive",)),
        ]
        for template_id, parameters, assets in cases:
            with self.subTest(template_id=template_id, parameters=parameters):
                with self.assertRaises(TemplateError):
                    archive_from_template(template_id, strategy_id="s", universe_assets=assets, parameters=parameters)


class TemplateRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = Path(self.temp.name) / "db.sqlite3"
        self.service = StrategyArchiveService(
            repository=SQLiteStrategyArchiveRepository(db),
            ledger=ResearchLedgerService(SQLiteResearchLedgerRepository(db)),
            project_root=PROJECT_ROOT,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_registered_template_strategy_moves_to_validating(self):
        result = self.service.register_from_template(
            "ma_timing", strategy_id="csi300_ma200", as_of_date="2026-10-29", universe_assets=("510300:exchange_fund",)
        )
        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["state"], "validating")
        again = self.service.register_from_template(
            "ma_timing", strategy_id="csi300_ma200", as_of_date="2026-10-29", universe_assets=("510300:exchange_fund",)
        )
        self.assertEqual(again["status"], "unchanged")
        with self.assertRaises(StrategyArchiveError):
            self.service.register_from_template("ma_timing", strategy_id="x", as_of_date="2026-10-29")

    def test_template_listing_names_what_cannot_be_expressed(self):
        listing = self.service.templates()
        self.assertEqual(len(listing["templates"]), len(STRATEGY_TEMPLATES))
        self.assertTrue(listing["not_expressible"])


if __name__ == "__main__":
    unittest.main()
