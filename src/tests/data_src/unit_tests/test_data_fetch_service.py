import tempfile
import unittest
from pathlib import Path

from src.data_access import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
)
from src.data_src.common import DataSourceError
from src.data_src.models import (
    DailyBar,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
    SecurityInfo,
)
from src.data_src.service import DataFetchService, compare_daily_bars
from src.data_store import SQLiteDataStore
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
)


class ExchangeListingSource:
    def __init__(self, listings, *, fail=False):
        self.listings = listings
        self.fail = fail

    def get_exchange_listings(self, symbol):
        if self.fail:
            raise DataSourceError("BaoStock session dropped")
        return [item for item in self.listings if item.symbol.endswith(symbol)]


class FundCatalogSource:
    def __init__(self, funds):
        self.funds = funds

    def get_fund_names(self):
        return self.funds


def _security(symbol, name, security_type, exchange=None):
    return SecurityInfo(
        symbol=symbol,
        name=name,
        security_type=security_type,
        exchange=exchange,
        source="unit-test:identity",
    )


class IdentityLookupTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.listings = [
            _security("sh.000001", "上证综合指数", "2", "sh"),
            _security("sz.000001", "平安银行", "1", "sz"),
            _security("sh.518880", "华安黄金ETF", "5", "sh"),
        ]
        self.funds = [
            _security("000001", "华夏成长混合", "混合型-灵活"),
            _security("518880", "黄金ETF华安", "指数型-其他"),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _service(self, *, fail=False, listing_fetcher=None):
        return DataFetchService(
            store=SQLiteDataStore(Path(self.temp_dir.name) / "ids.sqlite3"),
            akshare_source=FundCatalogSource(self.funds),
            baostock_source=ExchangeListingSource(self.listings, fail=fail),
            listing_fetcher=listing_fetcher or _unavailable_listing,
        )

    def _types(self, service, symbol):
        return sorted(
            item.asset_type
            for item in service.master_data.find_assets(standard_symbol=symbol)
        )

    def test_every_product_sharing_a_code_is_registered(self):
        service = self._service()

        service.fetch_and_store_identities("000001")

        self.assertEqual(
            self._types(service, "000001"),
            sorted([ASSET_TYPE_INDEX, ASSET_TYPE_STOCK, ASSET_TYPE_OPEN_FUND]),
        )

    def test_listed_etf_is_not_duplicated_as_open_fund(self):
        service = self._service()

        service.fetch_and_store_identities("518880")

        self.assertEqual(
            self._types(service, "518880"),
            [ASSET_TYPE_EXCHANGE_FUND],
        )

    def test_requested_type_limits_the_registered_identity(self):
        service = self._service()

        service.fetch_and_store_identities(
            "000001",
            asset_type=ASSET_TYPE_OPEN_FUND,
        )

        self.assertEqual(self._types(service, "000001"), [ASSET_TYPE_OPEN_FUND])

    def test_failed_source_registers_nothing(self):
        service = self._service(fail=True)

        with self.assertRaises(DataSourceError):
            service.fetch_and_store_identities("000001")
        self.assertEqual(self._types(service, "000001"), [])

    def test_exchange_lists_replace_a_failed_identity_source(self):
        calls = []

        def fetch(segment):
            calls.append(segment)
            return _DIRECTORY.get(segment, [_listing_row("999999", "sh", "stock")])

        service = self._service(fail=True, listing_fetcher=fetch)

        service.fetch_and_store_identities("000001")
        service.fetch_and_store_identities("000002", asset_type=ASSET_TYPE_STOCK)

        self.assertEqual(
            self._types(service, "000001"),
            sorted([ASSET_TYPE_INDEX, ASSET_TYPE_STOCK, ASSET_TYPE_OPEN_FUND]),
        )
        stock = service.master_data.find_assets(standard_symbol="000001", asset_type=ASSET_TYPE_STOCK)[0]
        self.assertEqual((stock.exchange, stock.display_name), ("sz", "平安银行"))
        self.assertEqual(self._types(service, "000002"), [ASSET_TYPE_STOCK])
        # Each segment is fetched once and then served from the cached directory.
        self.assertEqual(sorted(calls), sorted(set(calls)))

    def test_incomplete_exchange_lists_keep_the_original_error(self):
        def fetch(segment):
            if segment == "index":
                raise DataSourceError("index list down")
            return _DIRECTORY.get(segment, [_listing_row("999999", "sh", "stock")])

        service = self._service(fail=True, listing_fetcher=fetch)

        with self.assertRaisesRegex(DataSourceError, "BaoStock session dropped"):
            service.fetch_and_store_identities("000001")
        self.assertEqual(self._types(service, "000001"), [])


def _unavailable_listing(segment):
    raise DataSourceError(f"{segment} unavailable in unit tests")


def _listing_row(code, exchange, asset_type, name="x", listed_on=None, delisted_on=None):
    return {
        "code": code,
        "exchange": exchange,
        "asset_type": asset_type,
        "name": name,
        "listed_on": listed_on,
        "delisted_on": delisted_on,
        "board": None,
        "source": "akshare:unit-test",
    }


_DIRECTORY = {
    "sz_stock": [_listing_row("000001", "sz", "stock", "平安银行", "1991-04-03")],
    "sz_delisted": [_listing_row("000002", "sz", "stock", "某退", "1991-01-29", "2020-01-01")],
    "index": [_listing_row("000001", "sh", "index", "上证指数", "1991-07-15")],
}


class FakeDailySource:
    def __init__(self, source, close=10.0, amount=1000.0):
        self.source = source
        self.close = close
        self.amount = amount

    def get_a_share_daily_bars(self, symbol, start_date, end_date):
        return [
            DailyBar(
                symbol="000001" if self.source == "akshare" else "sz.000001",
                trade_date="2026-01-05",
                open=9.8,
                high=10.2,
                low=9.7,
                close=self.close,
                volume=100.0,
                amount=self.amount,
                source=f"{self.source}:test",
                raw={"provider": self.source},
            )
        ]

    def get_etf_daily_bars(self, symbol, start_date, end_date):
        return [
            DailyBar(
                symbol="510300",
                trade_date="2026-01-05",
                open=4.0,
                high=4.1,
                low=3.9,
                close=4.05,
                volume=1000.0,
                amount=4050.0,
                source=f"{self.source}:etf-test",
                raw={"provider": self.source},
            )
        ]

    def get_open_fund_nav_history_records(self, symbol):
        return [
            FundNav(
                symbol="000001",
                nav_date="2026-01-05",
                unit_nav=1.0,
                accumulated_nav=2.0,
                daily_growth_rate=0.5,
                source=f"{self.source}:fund-nav-test",
                estimated=False,
                raw={"provider": self.source},
            )
        ]

    def get_stock_profit_sheet(self, symbol):
        return [{"dataset": "income", "symbol": symbol}]

    def get_stock_balance_sheet(self, symbol):
        return [{"dataset": "balance", "symbol": symbol}]

    def get_stock_cash_flow_sheet(self, symbol):
        return [{"dataset": "cash_flow", "symbol": symbol}]

    def get_stock_financial_indicators(self, symbol):
        return [{"dataset": "indicators", "symbol": symbol}]

    def get_stock_basic_records(self, symbol=None):
        return [
            SecurityInfo(
                symbol="sz.000001",
                name="平安银行",
                security_type="1",
                exchange="sz",
                source=f"{self.source}:security-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_names(self):
        return [
            SecurityInfo(
                symbol="000001",
                name="测试基金",
                security_type="混合型",
                exchange=None,
                source=f"{self.source}:fund-name-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_value_estimates(self):
        return [
            FundEstimate(
                symbol="000001",
                estimate_date="2026-01-05",
                estimate_time="15:00",
                estimated_nav=1.1,
                estimated_growth_rate=0.5,
                source=f"{self.source}:fund-estimate-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_purchase_status_records(self):
        return [
            FundPurchaseStatus(
                symbol="000001",
                name="测试基金",
                fund_type="混合型",
                purchase_status="开放申购",
                redemption_status="开放赎回",
                next_open_date="2026-01-06",
                min_purchase_amount=10.0,
                daily_limit_amount=10000.0,
                fee_rate=0.15,
                source=f"{self.source}:purchase-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_scale_records(self, fund_type):
        return [
            FundReportRecord(
                symbol="000001",
                report_type="fund_scale_open_sina",
                report_date="2026-01-05",
                item_name="测试基金",
                item_value=1000.0,
                ratio=None,
                source=f"{self.source}:scale-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_stock_holdings(self, symbol, year):
        return [
            FundReportRecord(
                symbol="000001",
                report_type="fund_portfolio_hold_em",
                report_date="2026-06-30",
                item_name="平安银行",
                item_value=100.0,
                ratio=1.2,
                source=f"{self.source}:stock-holding-test",
                raw={"provider": self.source},
            )
        ]


class DataFetchServiceTest(unittest.TestCase):
    def test_fetch_stock_financial_records_routes_dataset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = FakeDailySource("akshare")
            service = DataFetchService(
                store=SQLiteDataStore(
                    Path(temp_dir) / "financial.sqlite3"
                ),
                akshare_source=source,
            )

            expected = {
                DATASET_STOCK_INCOME_STATEMENTS: "income",
                DATASET_STOCK_BALANCE_SHEETS: "balance",
                DATASET_STOCK_CASH_FLOW_STATEMENTS: "cash_flow",
                DATASET_STOCK_FINANCIAL_INDICATORS: "indicators",
            }
            for dataset, label in expected.items():
                records = service.fetch_stock_financial_records(
                    "600519",
                    dataset,
                )
                self.assertEqual(records[0]["dataset"], label)

    def test_fetch_and_store_a_share_daily_bars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_a_share_daily_bars(
                "000001", "20260101", "20260131", source="baostock"
            )

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_stock_daily_bars("000001")[0]["close"], 10.0)

    def test_compare_a_share_daily_bars_persists_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare", close=10.0, amount=1000.0),
                baostock_source=FakeDailySource("baostock", close=10.05, amount=1005.0),
            )

            checks = service.compare_a_share_daily_bars("000001", "20260101", "20260131")

            self.assertTrue(checks)
            self.assertTrue(all("field_name" in check for check in checks))
            self.assertTrue(any(check["field_name"] == "close" for check in checks))

    def test_compare_daily_bars_marks_large_difference_failed(self):
        left = [FakeDailySource("left", close=12.0).get_a_share_daily_bars("000001", "", "")[0]]
        right = [FakeDailySource("right", close=10.0).get_a_share_daily_bars("000001", "", "")[0]]

        checks = compare_daily_bars("000001", "left", "right", left, right, tolerance_pct=1.0)
        close_check = next(check for check in checks if check["field_name"] == "close")
        self.assertEqual(close_check["passed"], 0)

    def test_fetch_and_store_etf_daily_bars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_etf_daily_bars("510300", "20260101", "20260131")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_fund_exchange_daily_bars("510300")[0]["close"], 4.05)

    def test_fetch_and_store_open_fund_nav_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_open_fund_nav_history("000001")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_fund_nav_daily("000001")[0]["accumulated_nav"], 2.0)

    def test_fetch_and_store_basic_info_and_fund_estimates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            stock_result = service.fetch_and_store_stock_basic_info("000001")
            fund_result = service.fetch_and_store_fund_basic_info()
            estimate_result = service.fetch_and_store_fund_value_estimates()

            self.assertEqual(stock_result.stored_count, 1)
            self.assertEqual(fund_result.stored_count, 1)
            self.assertEqual(estimate_result.stored_count, 1)
            stock_assets = service.master_data.find_assets(
                source="baostock:security-test",
                raw_symbol="sz.000001",
            )
            fund_assets = service.master_data.find_assets(
                source="akshare:fund-name-test",
                raw_symbol="000001",
            )
            self.assertEqual(stock_assets[0].display_name, "平安银行")
            self.assertEqual(fund_assets[0].display_name, "测试基金")
            self.assertEqual(store.get_fund_estimates("000001")[0]["estimated_nav"], 1.1)

    def test_fund_basic_info_can_be_filtered_by_provider_symbol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = DataFetchService(
                store=SQLiteDataStore(Path(temp_dir) / "test.sqlite3"),
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            records = service.fetch_fund_basic_info(symbol="000001")

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].name, "测试基金")
            with self.assertRaisesRegex(
                ValueError,
                "provider identity",
            ):
                service.fetch_fund_basic_info(symbol="999999")

    def test_fetch_and_store_fund_status_scale_and_portfolio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            status_result = service.fetch_and_store_fund_purchase_status()
            scale_result = service.fetch_and_store_fund_scale_records()
            holding_result = service.fetch_and_store_fund_portfolio_records("000001", "2026", "stock")

            self.assertEqual(status_result.stored_count, 1)
            self.assertEqual(scale_result.stored_count, 1)
            self.assertEqual(holding_result.stored_count, 1)
            self.assertEqual(store.get_fund_purchase_status("000001")[0]["redemption_status"], "开放赎回")
            self.assertEqual(
                store.get_fund_report_records("000001", report_type="fund_portfolio_hold_em")[0]["item_name"],
                "平安银行",
            )


if __name__ == "__main__":
    unittest.main()
