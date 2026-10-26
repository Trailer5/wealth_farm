import unittest
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.data_src.csindex import CsIndexDataSource
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
        if "lsjz" in url:
            if params["fundCode"] != "510300":
                raise AssertionError("NAV fund code must be normalized")
            return {
                "TotalCount": 1,
                "Data": {
                    "LSJZList": [
                        {
                            "FSRQ": "2026-07-24",
                            "DWJZ": "4.701",
                            "LJJZ": "2.061",
                        }
                    ]
                },
            }
        if "kline" in url:
            if params["secid"] not in {"1.000001", "2.H00300"}:
                raise AssertionError("index must retain explicit SSE identity")
            return {
                "data": {
                    "klines": [
                        (
                            "2026-07-24,3600,3610,3620,3590,"
                            "100,360000,0.8,0.2,10,1"
                        )
                    ]
                }
            }
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


class FakeCsIndexHttp:
    def get_json(self, url, params=None):
        self.params = params
        return {
            "code": "200",
            "msg": "Success",
            "data": [
                {
                    "tradeDate": "20260725",
                    "indexCode": "930734",
                    "close": 101,
                },
                {
                    "tradeDate": "20260724",
                    "indexCode": "930734",
                    "indexNameCnAll": (
                        "中证360互联网+大数据100指数"
                    ),
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "tradingVol": 1000,
                    "tradingValue": 2000,
                }
            ],
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

    def get_json(self, url, params=None):
        return {
            "data": {
                "sh510300": {
                    "day": [
                        [
                            "2026-07-24",
                            "4.65",
                            "4.701",
                            "4.72",
                            "4.63",
                            "100",
                            "470100",
                        ]
                    ]
                }
            }
        }


class FakeSinaHttp:
    def get_text(self, url, params=None):
        return 'var hq_str_sh510300="沪深300ETF,4.0,3.96,4.05,4.1,3.9,4.0,4.0,100,400,0,0";'

    def get_json(self, url, params=None):
        self.params = params
        return [
            {
                "day": "2026-07-24",
                "open": "4.65",
                "high": "4.72",
                "low": "4.63",
                "close": "4.701",
                "volume": "10000",
            }
        ]


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
    def test_csindex_official_history_uses_compact_dates(self):
        http = FakeCsIndexHttp()
        bars = CsIndexDataSource(http).get_index_daily_bars(
            "930734",
            "2026-07-01",
            "2026-07-24",
            "csi",
        )

        self.assertEqual(http.params["startDate"], "20260701")
        self.assertEqual(http.params["endDate"], "20260724")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].trade_date, "2026-07-24")
        self.assertEqual(bars[0].close, 101.0)
        self.assertEqual(bars[0].source, "csindex:index_perf")

    def test_eastmoney_provider_spot_records(self):
        rows = EastmoneyExchangeFundDataSource(FakeEastmoneyHttp()).get_exchange_fund_spot()
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["fund_type"], EXCHANGE_FUND_ETF)
        self.assertEqual(rows[0]["latest"], 4.0)
        self.assertEqual(rows[0]["volume"], 10000.0)

    def test_eastmoney_index_history_uses_explicit_exchange(self):
        bars = EastmoneyExchangeFundDataSource(
            FakeEastmoneyHttp()
        ).get_index_daily_bars(
            "000001",
            "2026-07-01",
            "2026-07-24",
            "sh",
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "000001")
        self.assertEqual(bars[0].close, 3610.0)
        self.assertEqual(bars[0].raw["secid"], "1.000001")
        self.assertEqual(bars[0].source, "eastmoney:stock_kline")
        total_return = EastmoneyExchangeFundDataSource(
            FakeEastmoneyHttp()
        ).get_index_daily_bars(
            "H00300",
            "2026-07-01",
            "2026-07-24",
            "csi",
        )
        self.assertEqual(total_return[0].raw["secid"], "2.H00300")

    def test_eastmoney_etf_nav_uses_unadjusted_fund_nav_endpoint(self):
        navs = EastmoneyExchangeFundDataSource(
            FakeEastmoneyHttp()
        ).get_etf_nav_history_records(
            asset_id="asset:etf:510300",
            symbol="510300.SH",
            start_date="2026-07-01",
            end_date="2026-07-24",
        )

        self.assertEqual(len(navs), 1)
        self.assertEqual(navs[0].unit_nav, Decimal("4.701"))
        self.assertEqual(navs[0].accumulated_nav, Decimal("2.061"))
        self.assertEqual(navs[0].available_at, "2026-07-25")
        self.assertEqual(navs[0].source, "eastmoney:f10_lsjz")

    def test_tencent_provider_spot_records(self):
        rows = TencentExchangeFundDataSource(FakeTencentHttp()).get_exchange_fund_spot(["510300"])
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["name"], "沪深300ETF")
        self.assertEqual(rows[0]["latest"], 4.0)
        self.assertEqual(rows[0]["volume"], 10000.0)
        self.assertEqual(rows[0]["amount"], 4000000.0)

    def test_tencent_provider_daily_bars(self):
        source = TencentExchangeFundDataSource(FakeTencentHttp())

        bars = source.get_etf_daily_bars(
            "510300",
            "2026-07-01",
            "2026-07-24",
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "510300")
        self.assertEqual(bars[0].close, 4.701)
        self.assertEqual(bars[0].volume, 10000.0)
        self.assertEqual(bars[0].source, "tencent:kline")

    def test_sina_provider_spot_records(self):
        rows = SinaExchangeFundDataSource(FakeSinaHttp()).get_exchange_fund_spot(["510300"])
        self.assertEqual(rows[0]["symbol"], "510300")
        self.assertEqual(rows[0]["name"], "沪深300ETF")
        self.assertEqual(rows[0]["latest"], 4.05)

    def test_sina_provider_daily_bars(self):
        http = FakeSinaHttp()
        source = SinaExchangeFundDataSource(http)

        bars = source.get_etf_daily_bars(
            "510300",
            "2026-07-01",
            "2026-07-24",
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 4.701)
        self.assertEqual(bars[0].volume, 10000)
        self.assertEqual(bars[0].source, "sina:kline")
        # datalen counts back from the latest bar; a past window must still reach its start.
        self.assertGreaterEqual(
            http.params["datalen"],
            int((date.today() - date(2026, 7, 1)).days * 5 / 7),
        )

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
