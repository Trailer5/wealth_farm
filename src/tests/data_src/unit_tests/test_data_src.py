import unittest

from src.data_src.akshare import AkShareDataSource
from src.data_src.baostock import BaoStockDataSource
from src.data_src.common import (
    DataSourceError,
    infer_a_share_exchange,
    normalize_a_share_code,
    normalize_date_compact,
    normalize_date_dash,
    to_date_string,
    to_optional_float,
    to_akshare_market_symbol,
    to_akshare_symbol,
    to_baostock_symbol,
)


class FakeFrame:
    def __init__(self, records):
        self._records = records

    def to_dict(self, orient):
        self.assert_orient(orient)
        return self._records

    def assert_orient(self, orient):
        if orient != "records":
            raise AssertionError(f"unexpected orient: {orient}")


class FakeAkShare:
    def stock_zh_a_hist(self, **kwargs):
        if kwargs["symbol"] != "000001":
            raise AssertionError("symbol should be normalized for AkShare")
        if kwargs["start_date"] != "20260101" or kwargs["end_date"] != "20260131":
            raise AssertionError("dates should be compact for AkShare")
        return FakeFrame(
            [
                {
                    "日期": "2026-01-05",
                    "股票代码": "000001",
                    "开盘": "10.1",
                    "最高": "10.5",
                    "最低": "10.0",
                    "收盘": "10.3",
                    "成交量": "123456",
                    "成交额": "1234567.8",
                }
            ]
        )

    def stock_zh_a_spot_em(self):
        return FakeFrame([{"代码": "000001", "名称": "平安银行", "最新价": "10.3"}])

    def stock_zh_a_hist_min_em(self, **kwargs):
        if kwargs["start_date"] != "2026-01-05 09:30:00" or kwargs["end_date"] != "2026-01-05 15:00:00":
            raise AssertionError("minute dates should preserve timestamp strings")
        return FakeFrame([{"时间": "2026-01-05 09:35:00", "开盘": "10.1", "收盘": "10.2"}])

    def fund_etf_spot_em(self):
        return FakeFrame([{"代码": "510300", "名称": "沪深300ETF", "最新价": "4.0"}])

    def fund_etf_hist_em(self, **kwargs):
        if kwargs["symbol"] != "510300":
            raise AssertionError("ETF symbol should be normalized for AkShare")
        return FakeFrame(
            [
                {
                    "日期": "2026-01-05",
                    "代码": "510300",
                    "开盘": "4.0",
                    "最高": "4.1",
                    "最低": "3.9",
                    "收盘": "4.05",
                    "成交量": "1000",
                    "成交额": "4050",
                }
            ]
        )

    def index_zh_a_hist(self, **kwargs):
        if kwargs["symbol"] != "000300":
            raise AssertionError("index symbol should be normalized")
        return FakeFrame(
            [
                {
                    "日期": "2026-01-05",
                    "开盘": "4000",
                    "最高": "4050",
                    "最低": "3980",
                    "收盘": "4020",
                    "成交量": "2000",
                    "成交额": "8000000",
                }
            ]
        )

    def index_stock_cons_csindex(self, **kwargs):
        if kwargs["symbol"] != "000300":
            raise AssertionError("index symbol should be normalized")
        return FakeFrame(
            [
                {
                    "日期": "2026-07-31",
                    "指数代码": "000300",
                    "指数名称": "沪深300",
                    "成分券代码": "000001",
                    "成分券名称": "平安银行",
                    "交易所": "深圳证券交易所",
                }
            ]
        )

    def spot_hist_sge(self, **kwargs):
        return FakeFrame(
            [{"date": "2026-07-24", "close": "880"}]
        )

    def futures_zh_daily_sina(self, **kwargs):
        return FakeFrame(
            [{"date": "2026-07-24", "close": "80000"}]
        )

    def fund_open_fund_daily_em(self):
        return FakeFrame([{"基金代码": "000001", "基金简称": "测试基金", "单位净值": "1.0"}])

    def fund_name_em(self):
        return FakeFrame([{"基金代码": "000001", "基金简称": "测试基金", "基金类型": "混合型"}])

    def fund_purchase_em(self):
        return FakeFrame([{"基金代码": "000001", "申购状态": "开放申购", "赎回状态": "开放赎回"}])

    def fund_value_estimation_em(self):
        return FakeFrame([{"基金代码": "000001", "估算日期": "2026-01-05", "估算净值": "1.1", "估算涨幅": "0.5"}])

    def fund_scale_open_sina(self, **kwargs):
        return FakeFrame([{"基金代码": "000001", "基金简称": "测试基金", "最近总份额": "1000", "更新日期": "2026-01-05"}])

    def fund_portfolio_hold_em(self, **kwargs):
        return FakeFrame([{"股票名称": "平安银行", "占净值比例": "1.2", "持仓市值": "100", "截止时间": "2026-06-30"}])

    def fund_portfolio_bond_hold_em(self, **kwargs):
        return FakeFrame([{"债券名称": "测试债", "占净值比例": "2.3", "持仓市值": "200", "季度": "2026-2"}])

    def fund_portfolio_industry_allocation_em(self, **kwargs):
        return FakeFrame([{"行业类别": "制造业", "占净值比例": "3.4", "市值": "300", "截止时间": "2026-06-30"}])

    def fund_open_fund_info_em(self, **kwargs):
        if kwargs["symbol"] != "000001":
            raise AssertionError("fund symbol should be normalized for AkShare")
        return FakeFrame([{"净值日期": "2026-01-05", "单位净值": "1.0"}])

    def stock_profit_sheet_by_report_em(self, **kwargs):
        if kwargs["symbol"] != "SZ000001":
            raise AssertionError("profit sheet symbol should use AkShare prefixed format")
        return FakeFrame([{"股票代码": "000001", "营业总收入": "100"}])

    def stock_balance_sheet_by_report_em(self, **kwargs):
        if kwargs["symbol"] != "SZ000001":
            raise AssertionError("balance sheet symbol should use AkShare prefixed format")
        return FakeFrame([{"股票代码": kwargs["symbol"], "资产总计": "200"}])

    def stock_cash_flow_sheet_by_report_em(self, **kwargs):
        if kwargs["symbol"] != "SZ000001":
            raise AssertionError("cash flow sheet symbol should use AkShare prefixed format")
        return FakeFrame([{"股票代码": kwargs["symbol"], "经营活动现金流量净额": "50"}])

    def stock_financial_analysis_indicator_em(self, **kwargs):
        if kwargs["symbol"] != "000001.SZ":
            raise AssertionError("financial indicator symbol should include market suffix")
        return FakeFrame([{"股票代码": "000001", "净资产收益率": "10"}])

    def stock_report_disclosure(self, **kwargs):
        return FakeFrame([{"市场": kwargs["market"], "报告期": "2026Q1"}])

    def stock_board_industry_name_em(self):
        return FakeFrame([{"板块名称": "银行"}])

    def stock_board_industry_cons_em(self, **kwargs):
        return FakeFrame([{"板块名称": kwargs["symbol"], "代码": "000001"}])

    def stock_board_concept_name_em(self):
        return FakeFrame([{"板块名称": "中特估"}])

    def stock_board_concept_cons_em(self, **kwargs):
        return FakeFrame([{"板块名称": kwargs["symbol"], "代码": "000001"}])

    def stock_margin_detail_sse(self, **kwargs):
        return FakeFrame(
            [
                {
                    "信用交易日期": kwargs["date"],
                    "标的证券代码": "600519",
                    "融资余额": "1000",
                    "融资买入额": "100",
                    "融资偿还额": "80",
                    "融券余量": "20",
                    "融券卖出量": "3",
                    "融券偿还量": "2",
                }
            ]
        )

    def stock_margin_detail_szse(self, **kwargs):
        return FakeFrame(
            [
                {
                    "交易日期": kwargs["date"],
                    "证券代码": "1",
                    "融资余额": "2000",
                    "融资买入额": "200",
                    "融券余额": "10",
                    "融券余量": "5",
                    "融券卖出量": "1",
                    "融资融券余额": "2010",
                }
            ]
        )

    def fund_etf_scale_sse(self, **kwargs):
        return FakeFrame(
            [
                {
                    "基金代码": "510300",
                    "统计日期": "2026-08-07",
                    "基金份额": "1000000",
                }
            ]
        )

    def fund_scale_daily_szse(self, **kwargs):
        return FakeFrame(
            [
                {
                    "基金代码": "159915",
                    "日期": "2026-08-07",
                    "基金份额": "2000000",
                }
            ]
        )

    def fund_etf_fund_daily_em(self):
        return FakeFrame(
            [
                {
                    "基金代码": "510300",
                    "2026-08-07-单位净值": "4.2",
                    "2026-08-06-单位净值": "4.1",
                }
            ]
        )

    def stock_lhb_detail_em(self, **kwargs):
        return FakeFrame(
            [
                {
                    "代码": "600519",
                    "上榜日": "2026-08-07",
                    "龙虎榜净买额": "100",
                    "市场总成交额": "1000",
                    "流通市值": "10000",
                    "上榜原因": "日涨幅偏离",
                }
            ]
        )

    def stock_dzjy_mrmx(self, **kwargs):
        return FakeFrame(
            [
                {
                    "交易日期": "2026-08-07",
                    "证券代码": "000001",
                    "收盘价": "10",
                    "成交价": "9",
                    "成交额": "100",
                    "成交额/流通市值": "1",
                    "折溢率": "-10",
                    "买方营业部": "机构专用",
                    "卖方营业部": "测试营业部",
                }
            ]
        )

    def macro_china_gdp(self):
        return FakeFrame([{"季度": "2026Q1", "国内生产总值": "1"}])


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeBaoStockResult:
    def __init__(self, fields=None, rows=None):
        self.error_code = "0"
        self.error_msg = ""
        self.fields = fields or ["date", "code", "open", "high", "low", "close", "volume", "amount"]
        self._rows = rows or [
            ["2026-01-05", "sz.000001", "10.1", "10.5", "10.0", "10.3", "123456", "1234567.8"]
        ]
        self._idx = -1

    def next(self):
        self._idx += 1
        return self._idx < len(self._rows)

    def get_row_data(self):
        return self._rows[self._idx]


class FakeBaoStock:
    def __init__(self):
        self.logged_out = False

    def login(self):
        return FakeLoginResult()

    def logout(self):
        self.logged_out = True

    def query_history_k_data_plus(self, code, fields, start_date, end_date, frequency, adjustflag):
        if code != "sz.000001":
            raise AssertionError("symbol should be normalized for BaoStock")
        if start_date != "2026-01-01" or end_date != "2026-01-31":
            raise AssertionError("dates should be dashed for BaoStock")
        if frequency != "d" or adjustflag != "3":
            raise AssertionError("unexpected BaoStock query options")
        if "open" not in fields or "close" not in fields:
            raise AssertionError("daily fields should include OHLC")
        return FakeBaoStockResult()

    def query_trade_dates(self, start_date, end_date):
        if start_date != "2026-01-01" or end_date != "2026-01-31":
            raise AssertionError("trade date range should be normalized")
        return FakeBaoStockResult(fields=["calendar_date", "is_trading_day"], rows=[["2026-01-05", "1"]])

    def query_all_stock(self, day):
        if day != "2026-01-05":
            raise AssertionError("all stock day should be normalized")
        return FakeBaoStockResult(fields=["code", "tradeStatus"], rows=[["sz.000001", "1"]])

    def query_stock_basic(self, code=None, code_name=None):
        if code != "sz.000001":
            raise AssertionError("stock basic code should be normalized")
        if code_name is not None:
            raise AssertionError("code_name should not be passed in this test")
        return FakeBaoStockResult(
            fields=["code", "code_name", "ipoDate", "outDate", "type", "status"],
            rows=[["sz.000001", "平安银行", "1991-04-03", "", "1", "1"]],
        )

    def query_profit_data(self, code, year, quarter):
        if code != "sz.000001" or year != 2026 or quarter != 1:
            raise AssertionError("profit data arguments should be normalized")
        return FakeBaoStockResult(fields=["code", "roeAvg"], rows=[["sz.000001", "10.0"]])


class DataSourceCommonTest(unittest.TestCase):
    def test_normalize_a_share_symbol_formats(self):
        self.assertEqual(normalize_a_share_code("000001"), "000001")
        self.assertEqual(normalize_a_share_code("sz.000001"), "000001")
        self.assertEqual(normalize_a_share_code("000001.SZ"), "000001")

    def test_provider_symbol_formats(self):
        self.assertEqual(to_akshare_symbol("600000.SH"), "600000")
        self.assertEqual(to_akshare_market_symbol("000001"), "000001.SZ")
        self.assertEqual(to_baostock_symbol("600000"), "sh.600000")
        self.assertEqual(to_baostock_symbol("000001"), "sz.000001")
        self.assertEqual(infer_a_share_exchange("920047"), "bj")

    def test_date_formats(self):
        self.assertEqual(normalize_date_compact("2026-01-05"), "20260105")
        self.assertEqual(normalize_date_dash("20260105"), "2026-01-05")
        self.assertEqual(to_date_string("NaT"), "")
        self.assertEqual(to_date_string("1776355200000"), "2026-04-17")

        class FakeNaT:
            def strftime(self, fmt):
                raise ValueError("NaTType does not support strftime")

        self.assertEqual(to_date_string(FakeNaT()), "")
        self.assertIsNone(to_optional_float(float("nan")))


class ProviderAdapterTest(unittest.TestCase):
    def test_akshare_daily_bars_are_normalized(self):
        bars = AkShareDataSource(FakeAkShare()).get_a_share_daily_bars(
            "sz.000001", "2026-01-01", "2026-01-31"
        )
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].source, "akshare:stock_zh_a_hist")
        self.assertEqual(bars[0].symbol, "000001")
        self.assertEqual(bars[0].close, 10.3)
        self.assertEqual(bars[0].amount, 1234567.8)
        index_bars = AkShareDataSource(
            FakeAkShare()
        ).get_index_daily_bars(
            "000300",
            "2026-01-01",
            "2026-01-31",
        )
        self.assertEqual(index_bars[0].close, 4020.0)
        self.assertEqual(
            index_bars[0].source,
            "akshare:index_zh_a_hist",
        )
        constituents = AkShareDataSource(
            FakeAkShare()
        ).get_index_constituents("000300.CSI")
        self.assertEqual(len(constituents), 1)
        self.assertEqual(
            constituents[0].constituent_symbol,
            "000001",
        )
        self.assertEqual(constituents[0].exchange, "sz")
        self.assertEqual(
            constituents[0].effective_date,
            "2026-07-31",
        )
        self.assertEqual(
            AkShareDataSource(
                FakeAkShare()
            ).get_gold_spot_history()[0]["benchmark_symbol"],
            "Au99.99",
        )

    def test_akshare_market_and_fund_methods_return_records(self):
        provider = AkShareDataSource(FakeAkShare())
        self.assertEqual(provider.get_etf_spot()[0]["代码"], "510300")
        self.assertEqual(provider.get_etf_daily_bars("510300", "20260101", "20260131")[0].close, 4.05)
        self.assertEqual(provider.get_open_fund_daily_navs()[0]["基金代码"], "000001")
        self.assertEqual(provider.get_open_fund_nav_history("000001")[0]["单位净值"], "1.0")
        self.assertEqual(provider.get_open_fund_nav_history_records("000001")[0].unit_nav, 1.0)
        self.assertEqual(provider.get_fund_names()[0].name, "测试基金")
        self.assertEqual(provider.get_fund_purchase_status()[0]["申购状态"], "开放申购")
        self.assertEqual(provider.get_fund_purchase_status_records()[0].purchase_status, "开放申购")
        self.assertEqual(provider.get_fund_value_estimates()[0].estimated_nav, 1.1)
        self.assertEqual(provider.get_fund_scale_records()[0].item_value, 1000.0)
        self.assertEqual(provider.get_fund_stock_holdings("000001", "2026")[0].item_name, "平安银行")
        self.assertEqual(provider.get_fund_bond_holdings("000001", "2026")[0].item_name, "测试债")
        self.assertEqual(provider.get_fund_industry_allocation("000001", "2026")[0].ratio, 3.4)

    def test_disabled_akshare_non_baseline_methods_raise_clear_errors(self):
        provider = AkShareDataSource(FakeAkShare())
        with self.assertRaises(DataSourceError):
            provider.get_a_share_spot()
        with self.assertRaises(DataSourceError):
            provider.get_a_share_minute_bars("000001", "2026-01-05 09:30:00", "2026-01-05 15:00:00")
        with self.assertRaises(DataSourceError):
            provider.get_industry_board_constituents("银行")

    def test_akshare_financial_and_board_methods_return_records(self):
        provider = AkShareDataSource(FakeAkShare())
        self.assertEqual(provider.get_stock_profit_sheet("000001")[0]["营业总收入"], "100")
        self.assertEqual(provider.get_stock_balance_sheet("000001")[0]["资产总计"], "200")
        self.assertEqual(provider.get_stock_cash_flow_sheet("000001")[0]["经营活动现金流量净额"], "50")
        self.assertEqual(provider.get_stock_financial_indicators("000001")[0]["净资产收益率"], "10")
        self.assertEqual(provider.get_stock_report_disclosure()[0]["市场"], "沪深京")
        self.assertEqual(provider.get_industry_board_names()[0]["板块名称"], "银行")
        self.assertEqual(provider.get_concept_board_names()[0]["板块名称"], "中特估")
        self.assertEqual(provider.get_concept_board_constituents("中特估")[0]["代码"], "000001")
        self.assertEqual(provider.call_api("macro_china_gdp")[0]["季度"], "2026Q1")

    def test_akshare_a_share_signal_methods_normalize_records(self):
        provider = AkShareDataSource(FakeAkShare())
        margin = provider.get_stock_margin_details(
            exchange="sh",
            trade_date="2026-08-07",
        )[0]
        sz_margin = provider.get_stock_margin_details(
            exchange="sz",
            trade_date="2026-08-07",
        )[0]
        sh_share = provider.get_exchange_fund_share_records(
            exchange="sh",
            start_date="2026-08-07",
            end_date="2026-08-07",
        )[0]
        sz_share = provider.get_exchange_fund_share_records(
            exchange="sz",
            start_date="2026-08-07",
            end_date="2026-08-07",
        )[0]
        nav_records = provider.get_exchange_fund_nav_records()
        lhb = provider.get_lhb_activity_records(
            start_date="2026-08-07",
            end_date="2026-08-07",
        )[0]
        block = provider.get_block_trade_activity_records(
            start_date="2026-08-07",
            end_date="2026-08-07",
        )[0]

        self.assertEqual(margin["margin_repay_amount"], 80.0)
        self.assertEqual(sz_margin["symbol"], "000001")
        self.assertEqual(sh_share["shares"], "1000000")
        self.assertEqual(sz_share["symbol"], "159915")
        self.assertEqual(len(nav_records), 2)
        self.assertEqual(nav_records[0]["symbol"], "510300")
        self.assertEqual(lhb["net_buy_amount"], 100.0)
        self.assertFalse(lhb["metadata"]["identity_inference"])
        self.assertEqual(block["float_market_cap"], 10000.0)
        self.assertFalse(block["metadata"]["identity_inference"])

    def test_baostock_daily_bars_are_normalized(self):
        fake = FakeBaoStock()
        provider = BaoStockDataSource(fake)
        bars = provider.get_a_share_daily_bars("000001.SZ", "20260101", "20260131")
        provider.logout()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].source, "baostock:query_history_k_data_plus")
        self.assertEqual(bars[0].symbol, "000001")
        self.assertEqual(bars[0].close, 10.3)
        self.assertTrue(fake.logged_out)

    def test_baostock_reference_and_financial_methods_return_records(self):
        provider = BaoStockDataSource(FakeBaoStock())
        self.assertEqual(provider.get_trade_dates("20260101", "20260131")[0]["is_trading_day"], "1")
        self.assertEqual(provider.get_stock_basic("000001")[0]["code_name"], "平安银行")
        self.assertEqual(provider.get_stock_basic_records("000001")[0].name, "平安银行")
        self.assertEqual(provider.get_profit_data("000001", 2026, 1)[0]["roeAvg"], "10.0")

    def test_disabled_baostock_all_stocks_raises_clear_error(self):
        provider = BaoStockDataSource(FakeBaoStock())
        with self.assertRaises(DataSourceError):
            provider.get_all_stocks("2026-01-05")


if __name__ == "__main__":
    unittest.main()
