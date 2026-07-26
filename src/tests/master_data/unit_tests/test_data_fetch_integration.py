import tempfile
import unittest
from pathlib import Path

from src.data_src.models import SecurityInfo
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeStockBasicSource:
    def get_stock_basic_records(self, symbol=None):
        return [
            SecurityInfo(
                symbol="sz.000001",
                name="平安银行",
                security_type="1",
                exchange="sz",
                source="baostock:query_stock_basic",
                raw={"code": "sz.000001"},
            )
        ]


class FakeFundBasicSource:
    def get_fund_names(self):
        return [
            SecurityInfo(
                symbol="000001",
                name="测试混合基金",
                security_type="混合型",
                exchange=None,
                source="akshare:fund_name_em",
                raw={"基金代码": "000001"},
            )
        ]


class DataFetchMasterDataIntegrationTest(unittest.TestCase):
    def test_basic_info_flows_into_master_data_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                baostock_source=FakeStockBasicSource(),
                akshare_source=FakeFundBasicSource(),
            )

            stock_result = service.fetch_and_store_stock_basic_info("000001")
            fund_result = service.fetch_and_store_fund_basic_info()

            stocks = service.master_data.find_assets(
                source="baostock:query_stock_basic",
                raw_symbol="sz.000001",
            )
            funds = service.master_data.find_assets(
                source="akshare:fund_name_em",
                raw_symbol="000001",
            )
            self.assertEqual(stock_result.stored_count, 1)
            self.assertEqual(fund_result.stored_count, 1)
            self.assertEqual(stocks[0].display_name, "平安银行")
            self.assertEqual(stocks[0].market, "cn_a")
            self.assertEqual(funds[0].display_name, "测试混合基金")
            self.assertEqual(funds[0].market, "cn_fund")
            self.assertNotEqual(stocks[0].asset_id, funds[0].asset_id)


if __name__ == "__main__":
    unittest.main()
