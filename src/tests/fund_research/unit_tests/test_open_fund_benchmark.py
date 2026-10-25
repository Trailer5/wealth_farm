import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.benchmark_analysis import (
    BenchmarkComponentKind,
    SQLiteBenchmarkRepository,
)
from src.fund_research import (
    DisclosureExtractionStatus,
    FundRelationType,
    OpenFundBenchmarkParser,
    OpenFundBenchmarkResolver,
    OpenFundProfileSnapshot,
    SQLiteFundResearchRepository,
)
from src.master_data import MasterDataService, SQLiteMasterDataRepository


class OpenFundBenchmarkDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "benchmark.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.benchmark_repository = SQLiteBenchmarkRepository(
            self.db_path
        )
        self.fund_repository = SQLiteFundResearchRepository(
            self.db_path
        )
        self.resolver = OpenFundBenchmarkResolver(
            master_data=self.master,
            benchmark_repository=self.benchmark_repository,
            fund_repository=self.fund_repository,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolves_index_plus_cash_and_saves_underlying_relation(self):
        snapshot = _profile(
            "fund:002236",
            (
                "中证360互联网+大数据100指数收益率*95%"
                "+商业银行活期存款利率(税后)*5%"
            ),
        )

        definition = self.resolver.resolve(snapshot)
        stored = self.benchmark_repository.get_default_definition(
            snapshot.fund_asset_id,
            as_of_date=snapshot.effective_date,
        )
        relations = self.fund_repository.list_relations(
            snapshot.fund_asset_id,
            as_of_date=snapshot.effective_date,
            relation_type=FundRelationType.UNDERLYING_INDEX,
        )

        self.assertTrue(definition.resolved)
        self.assertEqual(stored, definition)
        self.assertEqual(
            [component.weight for component in definition.components],
            [Decimal("0.95"), Decimal("0.05")],
        )
        self.assertEqual(
            definition.components[0].index_asset_id,
            "index:cn_a:csi:930734",
        )
        self.assertEqual(
            definition.components[1].kind,
            BenchmarkComponentKind.CASH_RATE,
        )
        self.assertEqual(
            definition.components[1].annual_rate,
            Decimal("0.0035"),
        )
        self.assertEqual(len(relations), 1)
        self.assertEqual(
            relations[0].related_asset_id,
            "index:cn_a:csi:930734",
        )

    def test_parses_three_component_definition_without_guessing_bond_code(
        self,
    ):
        snapshot = _profile(
            "fund:163417",
            (
                "沪深300指数收益率×60%+恒生指数收益率×20%"
                "+中国债券总指数收益率×20%"
            ),
        )

        definition = self.resolver.resolve(snapshot)

        self.assertEqual(
            [component.label for component in definition.components],
            ["沪深300指数", "恒生指数", "中国债券总指数"],
        )
        self.assertEqual(
            [component.weight for component in definition.components],
            [Decimal("0.6"), Decimal("0.2"), Decimal("0.2")],
        )
        self.assertFalse(definition.resolved)
        self.assertIsNone(definition.components[2].index_asset_id)
        self.assertIn(
            "unresolved_index:中国债券总指数",
            definition.resolution_errors,
        )

    def test_unknown_index_name_is_preserved_as_unresolved(self):
        definition = OpenFundBenchmarkParser().parse(
            _profile(
                "fund:unknown",
                "未经核验指数收益率×100%",
            )
        )

        self.assertFalse(definition.resolved)
        self.assertEqual(
            definition.components[0].label,
            "未经核验指数",
        )
        self.assertIsNone(definition.components[0].index_asset_id)


def _profile(asset_id, benchmark_text):
    return OpenFundProfileSnapshot(
        fund_asset_id=asset_id,
        effective_date="2026-06-02",
        announcement_date="2026-06-30",
        fund_name="测试基金",
        share_class_name="测试基金A",
        share_class_code="002236",
        manager_name="测试管理人",
        custodian_name="测试托管人",
        fund_managers=(),
        benchmark_text=benchmark_text,
        management_fee_rate=Decimal("0.008"),
        custody_fee_rate=Decimal("0.001"),
        sales_service_fee_rate=Decimal("0"),
        source_document_id=f"profile:{asset_id}",
        source_title="测试基金产品资料概要",
        source_url="https://example.test/profile.pdf",
        source="official-unit-test",
        parser_version="unit-test",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
    )


if __name__ == "__main__":
    unittest.main()
