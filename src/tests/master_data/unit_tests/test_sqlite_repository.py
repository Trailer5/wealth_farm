import tempfile
import unittest
from pathlib import Path

from src.data_src.models import SecurityInfo
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFIRMED,
    RESOLUTION_CREATED,
    RESOLUTION_DISMISSED,
    RESOLUTION_MATCHED,
    RESOLUTION_UNRESOLVED,
    Asset,
    AssetIdentifier,
    AssetNameRecord,
    AssetResolution,
    AssetSourceObservation,
    IdentityMappingConflictError,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class SQLiteMasterDataRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "master-data.sqlite3"
        self.repository = SQLiteMasterDataRepository(self.db_path)
        self.repository.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_service_reuses_persisted_asset_across_restarts(self):
        first_service = MasterDataService(self.repository)
        created = first_service.ingest_security_info(
            SecurityInfo(
                symbol="sh.600519",
                name="贵州茅台",
                security_type="1",
                exchange="sh",
                source="baostock:query_stock_basic",
                raw={"code": "sh.600519"},
            )
        )

        second_service = MasterDataService(SQLiteMasterDataRepository(self.db_path))
        matched = second_service.resolve_identifier(
            source="cninfo:security_search",
            raw_symbol="600519.SH",
            name="贵州茅台",
            asset_type_hint="股票",
        )

        self.assertEqual(created.status, RESOLUTION_CREATED)
        self.assertEqual(matched.status, RESOLUTION_MATCHED)
        self.assertEqual(matched.asset.asset_id, created.asset.asset_id)
        self.assertEqual(self.repository.count_observations(), 2)
        self.assertEqual(
            second_service.find_assets(
                source="cninfo:security_search",
                raw_symbol="600519.SH",
            ),
            [created.asset],
        )

    def test_unresolved_observation_is_persisted_for_review(self):
        service = MasterDataService(self.repository)

        resolution = service.resolve_identifier(
            source="manual",
            raw_symbol="",
            name="未知标的",
        )

        unresolved = service.list_unresolved_observations()
        self.assertEqual(resolution.status, RESOLUTION_UNRESOLVED)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].observation.name, "未知标的")
        self.assertEqual(unresolved[0].reason, "missing_or_invalid_symbol")

    def test_repository_rejects_source_identifier_rebinding(self):
        first_asset = Asset(
            asset_id="asset:first",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600000",
            display_name="浦发银行",
            market="cn_a",
            exchange="sh",
        )
        first_identifier = AssetIdentifier(
            asset_id=first_asset.asset_id,
            source="provider:basic",
            raw_symbol="sh.600000",
            standard_symbol="600000",
        )
        self.repository.save_resolution(
            _created_resolution(first_asset, first_identifier)
        )

        second_asset = Asset(
            asset_id="asset:second",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600001",
            display_name="冲突标的",
            market="cn_a",
            exchange="sh",
        )
        conflicting_identifier = AssetIdentifier(
            asset_id=second_asset.asset_id,
            source="provider:basic",
            raw_symbol="SH.600000",
            standard_symbol="600001",
        )

        with self.assertRaises(IdentityMappingConflictError):
            self.repository.save_resolution(
                _created_resolution(second_asset, conflicting_identifier)
            )

        self.assertIsNone(self.repository.get_asset(second_asset.asset_id))
        self.assertEqual(self.repository.count_observations(), 1)

    def test_in_memory_repository_keeps_schema_across_operations(self):
        service = MasterDataService(SQLiteMasterDataRepository(":memory:"))

        resolution = service.resolve_identifier(
            source="manual",
            raw_symbol="600519.SH",
            name="贵州茅台",
            asset_type_hint="股票",
        )

        self.assertEqual(
            service.get_asset(resolution.asset.asset_id),
            resolution.asset,
        )

    def test_asset_name_history_is_point_in_time(self):
        service = MasterDataService(self.repository)
        asset = service.ingest_security_info(
            SecurityInfo(
                symbol="002253",
                name="川大智胜",
                security_type="stock",
                exchange="sz",
                source="unit-test",
            )
        ).asset
        service.register_asset_name(
            AssetNameRecord(
                asset_id=asset.asset_id,
                name="旧简称",
                name_type="official",
                effective_from="2020-01-01",
                effective_to="2023-12-31",
                source="exchange:announcement",
            )
        )
        service.register_asset_name(
            AssetNameRecord(
                asset_id=asset.asset_id,
                name="川大智胜",
                name_type="official",
                effective_from="2024-01-01",
                effective_to=None,
                source="exchange:announcement",
            )
        )
        service.register_asset_name(
            AssetNameRecord(
                asset_id=asset.asset_id,
                name="川普概念",
                name_type="reviewed_alias",
                effective_from="2024-11-01",
                effective_to="2024-11-30",
                source="manual:review",
                confidence=0.8,
            )
        )

        old = service.list_asset_names(
            asset_id=asset.asset_id,
            as_of_date="2023-06-01",
        )
        current = service.list_asset_names(
            asset_id=asset.asset_id,
            as_of_date="2024-11-06",
        )

        self.assertEqual([item.name for item in old], ["旧简称"])
        self.assertEqual(
            {item.name for item in current},
            {"川大智胜", "川普概念"},
        )

    def test_ambiguous_observation_can_be_confirmed(self):
        stock_asset = Asset(
            asset_id="asset:stock",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="510300",
            display_name="测试资产",
            market="cn_a",
            exchange="sh",
        )
        fund_asset = Asset(
            asset_id="asset:fund",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            standard_symbol="510300",
            display_name="测试资产",
            market="cn_a",
            exchange="sh",
        )
        self.repository.save_resolution(
            _created_resolution(
                stock_asset,
                AssetIdentifier(
                    asset_id=stock_asset.asset_id,
                    source="seed:stock",
                    raw_symbol="stock-510300",
                    standard_symbol="510300",
                ),
            )
        )
        self.repository.save_resolution(
            _created_resolution(
                fund_asset,
                AssetIdentifier(
                    asset_id=fund_asset.asset_id,
                    source="seed:fund",
                    raw_symbol="fund-510300",
                    standard_symbol="510300",
                ),
            )
        )
        service = MasterDataService(self.repository)

        resolution = service.resolve_identifier(
            source="manual:import",
            raw_symbol="510300",
            name="测试资产",
            asset_type_hint=None,
        )
        unresolved = service.list_unresolved_observations()
        identifier = service.confirm_observation(
            unresolved[0].observation_id,
            fund_asset.asset_id,
            reason="confirmed_as_exchange_fund",
        )

        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)
        self.assertEqual(identifier.asset_id, fund_asset.asset_id)
        self.assertEqual(service.list_unresolved_observations(), [])
        self.assertEqual(
            service.get_observation(unresolved[0].observation_id).resolution_status,
            RESOLUTION_CONFIRMED,
        )
        self.assertEqual(
            service.find_assets(source="manual:import", raw_symbol="510300"),
            [fund_asset],
        )

    def test_identifier_can_be_explicitly_rebound(self):
        first_asset = Asset(
            asset_id="asset:first",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600000",
            display_name="原标的",
            market="cn_a",
            exchange="sh",
        )
        second_asset = Asset(
            asset_id="asset:second",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600001",
            display_name="正确标的",
            market="cn_a",
            exchange="sh",
        )
        self.repository.save_resolution(
            _created_resolution(
                first_asset,
                AssetIdentifier(
                    asset_id=first_asset.asset_id,
                    source="provider:basic",
                    raw_symbol="sh.600000",
                    standard_symbol="600000",
                ),
            )
        )
        self.repository.save_resolution(
            _created_resolution(
                second_asset,
                AssetIdentifier(
                    asset_id=second_asset.asset_id,
                    source="seed:second",
                    raw_symbol="sh.600001",
                    standard_symbol="600001",
                ),
            )
        )
        service = MasterDataService(self.repository)

        service.rebind_identifier(
            source="provider:basic",
            raw_symbol="sh.600000",
            new_asset_id=second_asset.asset_id,
            reason="manual_correction",
        )

        self.assertEqual(
            service.find_assets(source="provider:basic", raw_symbol="sh.600000"),
            [second_asset],
        )
        self.assertEqual(len(service.list_rebindings()), 1)
        self.assertEqual(service.list_rebindings()[0].old_asset_id, first_asset.asset_id)
        self.assertEqual(service.list_rebindings()[0].new_asset_id, second_asset.asset_id)

    def test_unresolved_observation_can_be_dismissed(self):
        service = MasterDataService(self.repository)
        service.resolve_identifier(
            source="manual",
            raw_symbol="",
            name="无效输入",
        )
        observation = service.list_unresolved_observations()[0]

        dismissed = service.dismiss_observation(
            observation.observation_id,
            reason="invalid_input",
        )

        self.assertEqual(dismissed.resolution_status, RESOLUTION_DISMISSED)
        self.assertFalse(dismissed.needs_review)
        self.assertEqual(service.list_unresolved_observations(), [])


def _created_resolution(asset, identifier):
    return AssetResolution(
        status=RESOLUTION_CREATED,
        observation=AssetSourceObservation(
            source=identifier.source,
            raw_symbol=identifier.raw_symbol,
            standard_symbol=identifier.standard_symbol,
            name=asset.display_name,
            asset_type_hint=asset.asset_type,
            market_hint=asset.market,
            exchange_hint=asset.exchange,
        ),
        asset=asset,
        identifier=identifier,
        confidence=identifier.confidence,
        reason="test_created",
    )


if __name__ == "__main__":
    unittest.main()
