import json
import tempfile
import unittest
from pathlib import Path

from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_workflow import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    AssetResearchProfile,
    ResearchHorizon,
    ResearchOutputDepth,
    ResearchQuestionType,
    ResearchTaskFactory,
    ResearchTaskSpec,
    default_profile_registry,
)


class ResearchContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "research.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.stock = self._register(
            "asset:stock",
            ASSET_TYPE_STOCK,
            "600519",
            "贵州茅台",
        )
        self.etf = self._register(
            "asset:etf",
            ASSET_TYPE_EXCHANGE_FUND,
            "518880",
            "黄金ETF",
        )
        self.open_fund = self._register(
            "asset:open-fund",
            ASSET_TYPE_OPEN_FUND,
            "000001",
            "场外基金样本",
        )
        self.benchmark = self._register(
            "asset:benchmark",
            ASSET_TYPE_EXCHANGE_FUND,
            "510300",
            "沪深300ETF",
        )
        self.peer = self._register(
            "asset:peer",
            ASSET_TYPE_STOCK,
            "000858",
            "五粮液",
        )
        self.factory = ResearchTaskFactory(self.master_data)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _register(
        self,
        asset_id,
        asset_type,
        symbol,
        name,
    ):
        return self.master_data.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=name,
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol=symbol,
        )

    def test_default_registry_has_six_asset_profiles(self):
        registry = default_profile_registry()
        profiles = registry.list_profiles()

        self.assertEqual(len(profiles), 6)
        self.assertEqual(
            registry.default_for_asset(self.stock).profile_id,
            PROFILE_ORDINARY_COMPANY,
        )
        self.assertEqual(
            registry.default_for_asset(self.etf).profile_id,
            PROFILE_EXCHANGE_FUND,
        )
        self.assertEqual(
            registry.default_for_asset(
                self.open_fund
            ).profile_id,
            "open_fund",
        )

    def test_factory_creates_and_restores_stock_task(self):
        task = self.factory.create(
            asset_id=self.stock.asset_id,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.benchmark.asset_id,
            peer_asset_ids=(self.peer.asset_id,),
            output_depth=ResearchOutputDepth.DEEP,
        )
        serialized = json.loads(json.dumps(task.to_dict()))
        restored = self.factory.restore(serialized)

        self.assertEqual(task, restored)
        self.assertTrue(task.task_id.startswith("research:"))
        self.assertEqual(
            task.profile_id,
            PROFILE_ORDINARY_COMPANY,
        )

    def test_financial_profile_is_valid_for_stock(self):
        task = self.factory.create(
            asset_id=self.stock.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            question_type=ResearchQuestionType.RISK_REVIEW,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )

        self.assertEqual(
            task.profile_id,
            PROFILE_FINANCIAL_INSTITUTION,
        )

    def test_etf_profile_forbids_company_metrics(self):
        task = self.factory.create(
            asset_id=self.etf.asset_id,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-03",
        )
        profile = self.factory.profile_registry.require(
            task.profile_id
        )

        self.assertIn("roe", profile.forbidden_metrics)
        self.assertIn("tracking_error", profile.required_metrics)
        with self.assertRaisesRegex(
            ValueError,
            "forbids requested metrics",
        ):
            self.factory.create(
                asset_id=self.etf.asset_id,
                question_type=ResearchQuestionType.UNDERSTANDING,
                horizon=ResearchHorizon.LONG,
                as_of_date="2026-08-03",
                requested_metrics=("roe",),
            )

    def test_question_type_enforces_horizon(self):
        with self.assertRaisesRegex(
            ValueError,
            "timing requires horizon: short",
        ):
            self.factory.create(
                asset_id=self.stock.asset_id,
                question_type=ResearchQuestionType.TIMING,
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
            )

    def test_profile_must_support_asset_type(self):
        with self.assertRaisesRegex(
            ValueError,
            "does not support asset_type",
        ):
            self.factory.create(
                asset_id=self.etf.asset_id,
                profile_id=PROFILE_ORDINARY_COMPANY,
                question_type=(
                    ResearchQuestionType.UNDERSTANDING
                ),
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
            )

    def test_related_assets_must_exist(self):
        with self.assertRaisesRegex(
            KeyError,
            "Unknown related asset_id",
        ):
            self.factory.create(
                asset_id=self.stock.asset_id,
                question_type=ResearchQuestionType.COMPARISON,
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
                benchmark_asset_id="asset:missing",
            )

    def test_task_rejects_invalid_date_and_duplicate_peers(self):
        with self.assertRaisesRegex(
            ValueError,
            "as_of_date must use YYYY-MM-DD",
        ):
            self.factory.create(
                asset_id=self.stock.asset_id,
                question_type=(
                    ResearchQuestionType.UNDERSTANDING
                ),
                horizon=ResearchHorizon.LONG,
                as_of_date="2026/08/03",
            )

        with self.assertRaisesRegex(
            ValueError,
            "peer_asset_ids must not contain duplicates",
        ):
            ResearchTaskSpec(
                task_id="research:test",
                asset_id=self.stock.asset_id,
                profile_id=PROFILE_ORDINARY_COMPANY,
                question_type=(
                    ResearchQuestionType.COMPARISON
                ),
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
                peer_asset_ids=(
                    self.peer.asset_id,
                    self.peer.asset_id,
                ),
            )

    def test_profile_rejects_required_forbidden_overlap(self):
        with self.assertRaisesRegex(
            ValueError,
            "both required and forbidden",
        ):
            AssetResearchProfile(
                profile_id="broken",
                display_name="错误模板",
                supported_asset_types=(ASSET_TYPE_STOCK,),
                required_datasets=("daily_bars",),
                required_metrics=("roe",),
                forbidden_metrics=("roe",),
            )


if __name__ == "__main__":
    unittest.main()
