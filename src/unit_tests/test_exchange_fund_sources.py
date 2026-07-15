import unittest
import tempfile
from pathlib import Path

from src.data_src.eastmoney import EastmoneyExchangeFundDataSource
from src.data_src.exchange_fund import (
    EXCHANGE_FUND_CLOSED,
    EXCHANGE_FUND_ETF,
    EXCHANGE_FUND_LOF,
    identify_exchange_fund,
    to_exchange_symbol,
)
from src.data_src.service import DataFetchService, compare_exchange_fund_spot_records
from src.data_src.sina import SinaExchangeFundDataSource
from src.data_src.tencent import TencentExchangeFundDataSource
from src.data_store import SQLiteDataStore


class FakeEastmoneyHttp:
    def get_json(self, url, params=None):
        return {
            "data": {
                "diff": [
                    {
                        "f12": "510300",
                        "f14": "沪深300ETF",
                        "f2": "4.0",
                        "f3": "1.0",
                        "f4": "0.04",
                        "f5": "100",
                        "f6": "400",
                        "f15": "4.1",
                        "f16": "3.9",
                        "f17": "4.0",
                        "f18": "3.96",
                    }
                ]
            }
        }


class FakeTencentHttp:
    def get_text(self, url, params=None):
        fields = [""] * 38
        fields[1] = "沪深300ETF"
        fields[2] = "510300"
        fields[3] = "4.0"
        fields[4] = "3.96"
        fields[5] = "3.98"
        fields[6] = "100"
        fields[37] = "400"
        return f'v_sh510300="{"~".join(fields)}";'


class FakeSinaHttp:
    def get_text(self, url, params=None):
        return 'var hq_str_sh510300="沪深300ETF,4.0,3.96,4.05,4.1,3.9,4.0,4.0,100,400,0,0";'


class FailingExchangeFundSource:
    def get_exchange_fund_spot(self):
        raise RuntimeError("source unavailable")


class EmptyExchangeFundSource:
    def get_exchange_fund_spot(self):
        return []


class ExchangeFundRuleTest(unittest.TestCase):
    def test_identify_exchange_fund_by_name_and_prefix(self):
        self.assertEqual(identify_exchange_fund("510300", name="沪深300ETF").fund_type, EXCHANGE_FUND_ETF)
        self.assertEqual(identify_exchange_fund("160706", name="嘉实300LOF").fund_type, EXCHANGE_FUND_LOF)
        self.assertEqual(identify_exchange_fund("500058", name="基金银丰封闭").fund_type, EXCHANGE_FUND_CLOSED)
        self.assertEqual(to_exchange_symbol("510300"), "sh510300")
        self.assertEqual(to_exchange_symbol("159915"), "sz159915")


class ExchangeFundProviderTest(unittest.TestCase):
    def test_eastmoney_provider_spot_records(self):
        rows = EastmoneyExchangeFundDataSource(FakeEastmoneyHttp()).get_exchange_fund_spot()
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["fund_type"], EXCHANGE_FUND_ETF)
        self.assertEqual(rows[0]["latest"], 4.0)
        self.assertEqual(rows[0]["volume"], 10000.0)

    def test_tencent_provider_spot_records(self):
        rows = TencentExchangeFundDataSource(FakeTencentHttp()).get_exchange_fund_spot(["510300"])
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["name"], "沪深300ETF")
        self.assertEqual(rows[0]["latest"], 4.0)
        self.assertEqual(rows[0]["volume"], 10000.0)
        self.assertEqual(rows[0]["amount"], 4000000.0)

    def test_sina_provider_spot_records(self):
        rows = SinaExchangeFundDataSource(FakeSinaHttp()).get_exchange_fund_spot(["510300"])
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["name"], "沪深300ETF")
        self.assertEqual(rows[0]["latest"], 4.05)

    def test_service_routes_exchange_fund_spot_sources(self):
        service = DataFetchService(
            store=SQLiteDataStore(":memory:"),
            eastmoney_source=EastmoneyExchangeFundDataSource(FakeEastmoneyHttp()),
            tencent_source=TencentExchangeFundDataSource(FakeTencentHttp()),
            sina_source=SinaExchangeFundDataSource(FakeSinaHttp()),
        )
        self.assertEqual(service.fetch_exchange_fund_spot("eastmoney")[0]["symbol"], "510300")
        self.assertEqual(service.fetch_exchange_fund_spot("tencent", ["510300"])[0]["symbol"], "510300")
        self.assertEqual(service.fetch_exchange_fund_spot("sina", ["510300"])[0]["symbol"], "510300")

    def test_compare_exchange_fund_spot_records(self):
        records_by_source = {
            "eastmoney": [
                {"symbol": "510300", "latest": 4.0, "open": 3.98, "pre_close": 3.96, "volume": 10000, "amount": 4000000}
            ],
            "tencent": [
                {"symbol": "510300", "latest": 4.01, "open": 3.98, "pre_close": 3.96, "volume": 10000, "amount": 4000000}
            ],
        }

        checks = compare_exchange_fund_spot_records(records_by_source, ["510300"], tolerance_pct=1.0)

        self.assertTrue(checks)
        self.assertTrue(any(check["field_name"] == "latest" for check in checks))
        self.assertTrue(all(check["check_type"] == "exchange_fund_spot" for check in checks))

    def test_assess_exchange_fund_sources_records_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                eastmoney_source=EastmoneyExchangeFundDataSource(FakeEastmoneyHttp()),
                tencent_source=FailingExchangeFundSource(),
            )

            statuses = service.assess_exchange_fund_sources(
                sources=["eastmoney", "tencent"],
                symbols=["510300"],
            )
            stored = store.get_data_source_status("exchange_fund_spot")

            self.assertEqual(statuses[0].status, "ok")
            self.assertEqual(statuses[1].status, "failed")
            self.assertEqual(len(stored), 2)
            self.assertEqual(next(row for row in stored if row["source"] == "eastmoney")["success_count"], 1)
            self.assertEqual(next(row for row in stored if row["source"] == "tencent")["failure_count"], 1)

    def test_choose_exchange_fund_spot_source_prefers_healthiest_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                eastmoney_source=EastmoneyExchangeFundDataSource(FakeEastmoneyHttp()),
                tencent_source=TencentExchangeFundDataSource(FakeTencentHttp()),
            )

            service.assess_exchange_fund_sources(sources=["eastmoney", "tencent"], symbols=["510300"])

            self.assertEqual(service.choose_exchange_fund_spot_source(["eastmoney", "tencent"]), "eastmoney")

    def test_fetch_exchange_fund_spot_with_fallback_uses_backup_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                eastmoney_source=EmptyExchangeFundSource(),
                tencent_source=TencentExchangeFundDataSource(FakeTencentHttp()),
            )

            rows = service.fetch_exchange_fund_spot_with_fallback(["510300"], sources=["eastmoney", "tencent"])

            self.assertEqual(rows[0]["symbol"], "510300")
            self.assertEqual(rows[0]["source"], "tencent")


if __name__ == "__main__":
    unittest.main()
