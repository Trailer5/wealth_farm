import unittest

from src.data_src.models import SecurityInfo
from src.master_data import (
    ASSET_TYPE_STOCK,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CREATED,
    RESOLUTION_MATCHED,
    RESOLUTION_UNRESOLVED,
    Asset,
    MasterDataResolver,
)


class MasterDataResolverTest(unittest.TestCase):
    def test_ingest_security_info_creates_asset_from_first_observation(self):
        resolver = MasterDataResolver()

        resolution = resolver.ingest_security_info(
            SecurityInfo(
                symbol="sh.600519",
                name="贵州茅台",
                security_type="1",
                exchange="sh",
                source="baostock:query_stock_basic",
                raw={"code": "sh.600519"},
            )
        )

        self.assertEqual(resolution.status, RESOLUTION_CREATED)
        self.assertIsNotNone(resolution.asset)
        self.assertTrue(resolution.asset.asset_id.startswith("asset:"))
        self.assertEqual(resolution.asset.asset_type, ASSET_TYPE_STOCK)
        self.assertEqual(resolution.asset.standard_symbol, "600519")
        self.assertEqual(resolution.asset.display_name, "贵州茅台")

    def test_resolve_identifier_matches_existing_asset_from_new_source(self):
        resolver = MasterDataResolver(
            assets=[
                Asset(
                    asset_id="stock:cn_a:sh:600519",
                    asset_type=ASSET_TYPE_STOCK,
                    standard_symbol="600519",
                    display_name="贵州茅台",
                    market="cn_a",
                    exchange="sh",
                )
            ]
        )

        resolution = resolver.resolve_identifier(
            source="cninfo:security_search",
            raw_symbol="600519.SH",
            name="贵州茅台",
            asset_type_hint="股票",
        )

        self.assertEqual(resolution.status, RESOLUTION_MATCHED)
        self.assertIsNotNone(resolution.identifier)
        self.assertEqual(resolution.asset.asset_id, "stock:cn_a:sh:600519")
        self.assertEqual(resolution.identifier.source, "cninfo:security_search")

    def test_resolve_identifier_returns_unresolved_for_blank_symbol(self):
        resolver = MasterDataResolver()

        resolution = resolver.resolve_identifier(
            source="manual",
            raw_symbol="",
            name="未知标的",
            create=True,
        )

        self.assertEqual(resolution.status, RESOLUTION_UNRESOLVED)
        self.assertTrue(resolution.needs_review)

    def test_resolve_identifier_marks_multiple_strong_candidates_ambiguous(self):
        resolver = MasterDataResolver(
            assets=[
                Asset(
                    asset_id="stock:cn_a:sh:510300",
                    asset_type=ASSET_TYPE_STOCK,
                    standard_symbol="510300",
                    display_name="测试资产",
                    market="cn_a",
                    exchange="sh",
                ),
                Asset(
                    asset_id="exchange_fund:cn_a:sh:510300",
                    asset_type="exchange_fund",
                    standard_symbol="510300",
                    display_name="测试资产",
                    market="cn_a",
                    exchange="sh",
                ),
            ]
        )

        resolution = resolver.resolve_identifier(
            source="manual",
            raw_symbol="510300",
            name="测试资产",
            asset_type_hint=None,
            create=True,
        )

        self.assertEqual(resolution.status, RESOLUTION_AMBIGUOUS)
        self.assertTrue(resolution.needs_review)
        self.assertEqual(len(resolution.candidates), 2)

    def test_open_fund_code_does_not_infer_stock_exchange(self):
        resolver = MasterDataResolver()

        resolution = resolver.ingest_security_info(
            SecurityInfo(
                symbol="000001",
                name="测试混合基金",
                security_type="混合型",
                exchange=None,
                source="akshare:fund_name_em",
            )
        )

        self.assertEqual(resolution.status, RESOLUTION_CREATED)
        self.assertEqual(resolution.asset.asset_type, "open_fund")
        self.assertEqual(resolution.asset.market, "cn_fund")
        self.assertIsNone(resolution.asset.exchange)


if __name__ == "__main__":
    unittest.main()
