import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.data_src import StockIndustryInfo
from src.data_src.eastmoney import EastmoneyStockIndustryDataSource
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
)
from src.research_workflow import (
    ResearchProfileAssignmentService,
    SQLiteResearchProfileAssignmentRepository,
    high_growth_unprofitable_reason,
)


class _Http:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {
            "data": {
                "diff": [
                    {
                        "f12": "000001",
                        "f14": "平安银行",
                        "f100": "银行Ⅱ",
                    },
                    {
                        "f12": "600519",
                        "f14": "贵州茅台",
                        "f100": "白酒Ⅱ",
                    },
                ]
            }
        }


class ProfileAssignmentTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "profiles.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.assets = {
            symbol: self.master.register_asset(
                Asset(
                    asset_id=f"stock:{symbol}",
                    asset_type=ASSET_TYPE_STOCK,
                    standard_symbol=symbol,
                    display_name=name,
                    market="cn_a",
                    exchange=(
                        "sh" if symbol.startswith("6") else "sz"
                    ),
                ),
                source="unit-test",
                raw_symbol=symbol,
            )
            for symbol, name in (
                ("000001", "平安银行"),
                ("600519", "贵州茅台"),
                ("601899", "紫金矿业"),
            )
        }
        self.repository = (
            SQLiteResearchProfileAssignmentRepository(db_path)
        )
        self.service = ResearchProfileAssignmentService(
            master_data=self.master,
            repository=self.repository,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_batch_source_uses_market_secids(self):
        http = _Http()
        records = EastmoneyStockIndustryDataSource(
            http
        ).get_stock_industries(["000001", "600519"])

        self.assertEqual(len(records), 2)
        self.assertEqual(
            http.calls[0][1]["secids"],
            "0.000001,1.600519",
        )

    def test_rules_and_manual_override_are_persisted(self):
        records = [
            StockIndustryInfo(
                "000001",
                "平安银行",
                "银行Ⅱ",
                "eastmoney:ulist_industry",
            ),
            StockIndustryInfo(
                "600519",
                "贵州茅台",
                "白酒Ⅱ",
                "eastmoney:ulist_industry",
            ),
            StockIndustryInfo(
                "601899",
                "紫金矿业",
                "工业金属",
                "eastmoney:ulist_industry",
            ),
        ]
        assignments = self.service.classify_industries(
            records,
            asset_by_symbol={
                symbol: asset.asset_id
                for symbol, asset in self.assets.items()
            },
            effective_date="2026-08-03",
        )
        by_asset = {
            item.asset_id: item for item in assignments
        }

        self.assertEqual(
            by_asset[self.assets["000001"].asset_id].profile_id,
            PROFILE_FINANCIAL_INSTITUTION,
        )
        self.assertEqual(
            by_asset[self.assets["600519"].asset_id].profile_id,
            PROFILE_ORDINARY_COMPANY,
        )
        self.assertEqual(
            by_asset[self.assets["601899"].asset_id].profile_id,
            PROFILE_CYCLICAL_RESOURCE,
        )
        self.service.assign_manual(
            asset_id=self.assets["601899"].asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            industry_name="人工复核",
            effective_date="2026-08-03",
            reason="Unit-test override",
        )
        profile_map = self.service.profile_map(
            [asset.asset_id for asset in self.assets.values()],
            as_of_date="2026-08-03",
        )
        self.assertEqual(
            profile_map[self.assets["601899"].asset_id],
            PROFILE_ORDINARY_COMPANY,
        )

    def test_csrc_industry_names_use_the_same_rules(self):
        records = [
            StockIndustryInfo(
                symbol,
                name,
                industry,
                "baostock:query_stock_industry",
            )
            for symbol, name, industry in (
                ("000001", "平安银行", "J66货币金融服务"),
                ("600519", "贵州茅台", "C15酒、饮料和精制茶制造业"),
                ("601899", "紫金矿业", "B09有色金属矿采选业"),
            )
        ]

        assignments = self.service.classify_industries(
            records,
            asset_by_symbol={
                symbol: asset.asset_id
                for symbol, asset in self.assets.items()
            },
            effective_date="2026-08-03",
        )

        self.assertEqual(
            [item.profile_id for item in assignments],
            [
                PROFILE_FINANCIAL_INSTITUTION,
                PROFILE_ORDINARY_COMPANY,
                PROFILE_CYCLICAL_RESOURCE,
            ],
        )

    def test_high_growth_unprofitable_rule(self):
        loss, profit = Decimal("-5"), Decimal("5")
        self.assertEqual(
            high_growth_unprofitable_reason(
                earnings_ttm=loss, revenue_growth=None, display_name="某某生物-U", symbol="688123"
            ),
            "financial_rule:star_unprofitable_listing",
        )
        self.assertEqual(
            high_growth_unprofitable_reason(
                earnings_ttm=loss, revenue_growth=Decimal("0.25"), display_name="某某科技", symbol="300123"
            ),
            "financial_rule:ttm_loss_revenue_growth",
        )
        for earnings, growth, name, symbol in (
            (loss, Decimal("0.05"), "某某科技", "300123"),
            (profit, Decimal("0.50"), "某某生物-U", "688123"),
            (loss, None, "某某生物-U", "600123"),
            (None, Decimal("0.50"), "某某科技", "300123"),
        ):
            self.assertIsNone(
                high_growth_unprofitable_reason(
                    earnings_ttm=earnings, revenue_growth=growth, display_name=name, symbol=symbol
                )
            )


if __name__ == "__main__":
    unittest.main()
