import tempfile
import unittest
from pathlib import Path

from src.benchmark_analysis import (
    DefaultBenchmarkResolver,
    SQLiteBenchmarkRepository,
)
from src.data_src.common import DataSourceError
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
from src.research_contracts import (
    PROFILE_FINANCIAL_INSTITUTION,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import ResearchTaskFactory


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

    def test_sector_role_falls_back_to_broad_market_with_reason(self):
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

        self.assertEqual(
            financial_task.benchmark_asset_id,
            self.index.asset_id,
        )
        relation = self.benchmark_repository.get_default(
            bank.asset_id,
            as_of_date="2026-08-03",
        )
        self.assertEqual(
            relation.source,
            "profile_fallback:financial_sector:broad_market",
        )

    def test_missing_broad_market_index_is_bootstrapped(self):
        empty_path = Path(self.temp_dir.name) / "empty.sqlite3"
        empty = MasterDataService(SQLiteMasterDataRepository(empty_path))
        stock = empty.register_asset(
            Asset(
                asset_id="stock:company",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="stock",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="600519",
        )

        def create(bootstrap):
            return ResearchTaskFactory(
                empty,
                benchmark_resolver=DefaultBenchmarkResolver(
                    master_data=empty,
                    benchmark_repository=SQLiteBenchmarkRepository(
                        empty_path
                    ),
                    identity_bootstrap=bootstrap,
                ),
            ).create(
                asset_id=stock.asset_id,
                question_type=ResearchQuestionType.INVESTMENT,
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
            )

        self.assertIsNone(
            create(_IndexBootstrap(empty, fail=True)).benchmark_asset_id
        )
        bootstrap = _IndexBootstrap(empty)
        self.assertEqual(
            create(bootstrap).benchmark_asset_id,
            "index:bootstrapped",
        )
        self.assertEqual(bootstrap.calls, [("000300", ASSET_TYPE_INDEX)])

    def test_ambiguous_index_stays_empty(self):
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


class _IndexBootstrap:
    def __init__(self, master, *, fail=False):
        self.master = master
        self.fail = fail
        self.calls = []

    def fetch_and_store_identities(self, symbol, *, asset_type=None):
        self.calls.append((symbol, asset_type))
        if self.fail:
            raise DataSourceError("identity source timeout")
        self.master.register_asset(
            Asset(
                asset_id="index:bootstrapped",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol=symbol,
                display_name="沪深300指数",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test:bootstrap",
            raw_symbol=symbol,
        )


if __name__ == "__main__":
    unittest.main()
