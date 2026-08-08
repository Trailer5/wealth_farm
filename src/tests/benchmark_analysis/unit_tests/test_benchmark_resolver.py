import tempfile
import unittest
from pathlib import Path

from src.benchmark_analysis import (
    DefaultBenchmarkResolver,
    SQLiteBenchmarkRepository,
)
from src.fund_research import (
    FundAssetRelation,
    FundRelationType,
    SQLiteFundResearchRepository,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_workflow import (
    PROFILE_FINANCIAL_INSTITUTION,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskFactory,
)


class BenchmarkResolverTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "resolver.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
            "sh",
        )
        self.benchmark_repository = SQLiteBenchmarkRepository(
            self.db_path
        )
        self.fund_repository = SQLiteFundResearchRepository(
            self.db_path
        )
        self.resolver = DefaultBenchmarkResolver(
            master_data=self.master,
            benchmark_repository=self.benchmark_repository,
            fund_repository=self.fund_repository,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ordinary_company_uses_unique_csi300_index(self):
        stock = self._register(
            "stock:company",
            ASSET_TYPE_STOCK,
            "600519",
            "sh",
        )
        factory = ResearchTaskFactory(
            self.master,
            benchmark_resolver=self.resolver,
        )
        task = factory.create(
            asset_id=stock.asset_id,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )

        self.assertEqual(
            task.benchmark_asset_id,
            self.index.asset_id,
        )
        self.assertEqual(
            factory.restore(task.to_dict()).benchmark_asset_id,
            self.index.asset_id,
        )

    def test_etf_uses_reviewed_underlying_index_relation(self):
        etf = self._register(
            "fund:csi300",
            ASSET_TYPE_EXCHANGE_FUND,
            "510300",
            "sh",
        )
        self.fund_repository.initialize()
        self.fund_repository.save_relation(
            FundAssetRelation(
                fund_asset_id=etf.asset_id,
                relation_type=FundRelationType.UNDERLYING_INDEX,
                related_asset_id=self.index.asset_id,
                related_name="沪深300",
                source="unit-test",
                effective_date="2026-01-01",
                confidence=1.0,
            )
        )
        task = ResearchTaskFactory(
            self.master,
            benchmark_resolver=self.resolver,
        ).create(
            asset_id=etf.asset_id,
            question_type=ResearchQuestionType.COMPARISON,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )

        self.assertEqual(
            task.benchmark_asset_id,
            self.index.asset_id,
        )

    def test_unresolved_role_or_ambiguous_index_stays_empty(self):
        bank = self._register(
            "stock:bank",
            ASSET_TYPE_STOCK,
            "000001",
            "sz",
        )
        financial_task = ResearchTaskFactory(
            self.master,
            benchmark_resolver=self.resolver,
        ).create(
            asset_id=bank.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            question_type=ResearchQuestionType.RISK_REVIEW,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )
        self.assertIsNone(financial_task.benchmark_asset_id)

        second_index = self._register(
            "index:csi300:other",
            ASSET_TYPE_INDEX,
            "000300",
            "sz",
        )
        stock = self._register(
            "stock:company",
            ASSET_TYPE_STOCK,
            "600519",
            "sh",
        )
        task = ResearchTaskFactory(
            self.master,
            benchmark_resolver=DefaultBenchmarkResolver(
                master_data=self.master,
                benchmark_repository=SQLiteBenchmarkRepository(
                    self.db_path
                ),
            ),
        ).create(
            asset_id=stock.asset_id,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )
        self.assertIsNone(task.benchmark_asset_id)
        self.assertNotEqual(second_index.asset_id, self.index.asset_id)

    def _register(self, asset_id, asset_type, symbol, exchange):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=asset_id,
                market="cn_a",
                exchange=exchange,
            ),
            source=f"unit-test:{asset_id}",
            raw_symbol=symbol,
        )


if __name__ == "__main__":
    unittest.main()
