import unittest
from decimal import Decimal

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

    def fund_etf_fund_info_em(self, **kwargs):
        if kwargs != {
            "fund": "510300",
            "start_date": "20250617",
            "end_date": "20250620",
        }:
            raise AssertionError(
                "ETF NAV should use the verified AkShare contract"
            )
        return FakeFrame(
            [
                {
                    "净值日期": "2025-06-18",
                    "单位净值": "4.2",
                    "累计净值": "5.1",
                }
            ]
        )

    def fund_fh_em(self, **kwargs):
        if kwargs != {"year": "2025"}:
            raise AssertionError(
                "fund dividends should query one explicit year"
            )
        return FakeFrame(
            [
                {
                    "基金代码": "510300",
                    "除息日期": "2025-06-18",
                    "分红": "0.088",
                },
                {
                    "基金代码": "000001",
                    "除息日期": "2025-06-18",
                    "分红": "0.1",
                },
            ]
        )

    def fund_cf_em(self, **kwargs):
        if kwargs != {"year": "2025"}:
            raise AssertionError(
                "fund splits should query one explicit year"
            )
        return FakeFrame(
            [
                {
                    "基金代码": "510300",
                    "拆分折算日": "2025-06-19",
                    "拆分折算": "5",
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

    def stock_market_pe_lg(self, **kwargs):
        if kwargs != {"symbol": "上证"}:
            raise AssertionError("Shanghai market PE should request the 上证 series")
        return FakeFrame(
            [
                {"日期": "2026-01-03", "指数": "", "平均市盈率": "15.0"},
                {"日期": "20260101", "指数": "3200", "平均市盈率": "14.5"},
                {"日期": "2026-01-01", "指数": "3210", "平均市盈率": "14.8"},
                {"日期": "2026-01-02", "指数": "3190", "平均市盈率": "0"},
            ]
        )

    def stock_market_pb_lg(self, **kwargs):
        if kwargs != {"symbol": "上证"}:
            raise AssertionError("Shanghai market PB should request 上证")
        return FakeFrame(
            [
                {
                    "日期": "2026-01-01",
                    "指数": "3210",
                    "市净率": "1.5",
                    "等权市净率": "4.2",
                    "市净率中位数": "2.4",
                },
                {
                    "日期": "2026-01-02",
                    "指数": "3200",
                    "市净率": "0",
                    "等权市净率": "4.1",
                    "市净率中位数": "2.3",
                },
            ]
        )

    def stock_index_pe_lg(self, **kwargs):
        if kwargs != {"symbol": "沪深300"}:
            raise AssertionError("index PE symbol should be preserved")
        return FakeFrame(
            [
                {
                    "日期": "2026-08-07",
                    "指数": "4694.44",
                    "静态市盈率": "13.99",
                    "滚动市盈率": "13.71",
                    "等权静态市盈率": "40.23",
                    "等权滚动市盈率": "35.75",
                    "静态市盈率中位数": "21.33",
                    "滚动市盈率中位数": "20.69",
                }
            ]
        )

    def stock_index_pb_lg(self, **kwargs):
        if kwargs != {"symbol": "沪深300"}:
            raise AssertionError("index PB symbol should be preserved")
        return FakeFrame(
            [
                {
                    "日期": "2026-08-07",
                    "指数": "4694.44",
                    "市净率": "1.44",
                    "等权市净率": "4.47",
                    "市净率中位数": "2.30",
                }
            ]
        )

    def stock_zh_index_value_csindex(self, **kwargs):
        if kwargs != {"symbol": "000300"}:
            raise AssertionError("CSI valuation symbol should normalize")
        return FakeFrame(
            [
                {
                    "日期": "2026-07-14",
                    "指数代码": "300",
                    "指数中文简称": "沪深300",
                    "市盈率1": "14.89",
                    "市盈率2": "17.77",
                    "股息率1": "2.63",
                    "股息率2": "2.19",
                }
            ]
        )

    def stock_market_activity_legu(self):
        return FakeFrame(
            [
                {"item": "上涨", "value": 2670},
                {"item": "下跌", "value": 2399},
                {"item": "平盘", "value": 133},
                {"item": "停牌", "value": 4},
                {"item": "涨停", "value": 75},
                {"item": "跌停", "value": 4},
                {"item": "活跃度", "value": "51.29%"},
                {
                    "item": "统计日期",
                    "value": "2026-08-07 15:00:00",
                },
            ]
        )

    def index_analysis_daily_sw(self, **kwargs):
        if (
            kwargs.get("symbol") not in {"市场表征", "一级行业"}
            or kwargs.get("start_date") != "20260803"
            or kwargs.get("end_date") != "20260807"
        ):
            raise AssertionError("SW dates should use compact format")
        industry = kwargs["symbol"] == "一级行业"
        return FakeFrame(
            [
                {
                    "指数代码": "801010" if industry else "801001",
                    "指数名称": "农林牧渔" if industry else "申万50",
                    "发布日期": "2026-08-07",
                    "涨跌幅": "1.56",
                    "换手率": "3.53",
                    "市盈率": "25.29",
                    "市净率": "3.5",
                    "股息率": "1.57",
                    "成交额占比": "18.31",
                    "流通市值": "114914.46",
                }
            ]
        )

    def bond_zh_us_rate(self, **kwargs):
        if kwargs != {"start_date": "20260101"}:
            raise AssertionError("bond yield start date should use compact format")
        return FakeFrame(
            [
                {"日期": "2026-01-03", "中国国债收益率10年": "2.0"},
                {"日期": "20260101", "中国国债收益率10年": "1.8"},
                {"日期": "2026-01-01", "中国国债收益率10年": "1.9"},
                {"日期": "2026-01-02", "中国国债收益率10年": None},
            ]
        )

    def index_option_50etf_qvix(self):
        return FakeFrame(
            [
                {"date": "2026-01-03", "close": "20.0"},
                {"date": "20260101", "close": "18.0"},
                {"date": "2026-01-01", "close": "19.0"},
                {"date": "2026-01-02", "close": ""},
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
        if code not in {"sz.000001", "sh.000001"}:
            raise AssertionError("symbol should be normalized for BaoStock")
        if start_date != "2026-01-01" or end_date != "2026-01-31":
            raise AssertionError("dates should be dashed for BaoStock")
        if frequency != "d" or adjustflag != "3":
            raise AssertionError("unexpected BaoStock query options")
        if "open" not in fields or "close" not in fields:
            raise AssertionError("daily fields should include OHLC")
        return FakeBaoStockResult(
            rows=[
                [
                    "2026-01-05",
                    code,
                    "10.1",
                    "10.5",
                    "10.0",
                    "10.3",
                    "123456",
                    "1234567.8",
                ]
            ]
        )

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

    def test_akshare_etf_nav_distribution_and_split_contracts(self):
        provider = AkShareDataSource(FakeAkShare())
        navs = provider.get_etf_nav_history_records(
            asset_id="asset:etf:510300",
            symbol="510300.SH",
            start_date="2025-06-17",
            end_date="2025-06-20",
        )
        distributions = provider.get_etf_distribution_events(
            symbol="510300",
            start_date="2025-06-17",
            end_date="2025-06-20",
        )
        splits = provider.get_etf_split_events(
            symbol="510300",
            start_date="2025-06-17",
            end_date="2025-06-20",
        )
        distributions_batch = provider.get_etf_distribution_events_batch(
            symbols=("510300", "510210"),
            start_date="2025-06-17",
            end_date="2025-06-20",
        )
        splits_batch = provider.get_etf_split_events_batch(
            symbols=("510300", "510210"),
            start_date="2025-06-17",
            end_date="2025-06-20",
        )

        self.assertEqual(len(navs), 1)
        self.assertEqual(navs[0].date, "2025-06-18")
        self.assertEqual(navs[0].available_at, "2025-06-19")
        self.assertEqual(
            navs[0].source,
            "akshare:fund_etf_fund_info_em",
        )
        self.assertEqual(len(distributions), 1)
        self.assertEqual(
            distributions[0].cash_per_share,
            Decimal("0.088"),
        )
        self.assertEqual(
            distributions[0].available_at,
            distributions[0].ex_date,
        )
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0].ratio, Decimal("5"))
        self.assertEqual(
            splits[0].source,
            "akshare:fund_cf_em",
        )
        self.assertEqual(len(distributions_batch["510300"]), 1)
        self.assertEqual(distributions_batch["510210"], [])
        self.assertEqual(len(splits_batch["510300"]), 1)
        self.assertEqual(splits_batch["510210"], [])

    def test_akshare_etf_nav_failure_never_falls_back_to_prices(self):
        class BrokenETFNavAkShare:
            def fund_etf_fund_info_em(self, **kwargs):
                raise ValueError("upstream schema changed")

        with self.assertRaisesRegex(
            DataSourceError,
            "do not substitute adjusted ETF market prices for NAV",
        ):
            AkShareDataSource(
                BrokenETFNavAkShare()
            ).get_etf_nav_history_records(
                asset_id="asset:etf:510300",
                symbol="510300",
                start_date="2025-06-17",
                end_date="2025-06-20",
            )

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

    def test_akshare_market_timing_histories_are_normalized(self):
        provider = AkShareDataSource(FakeAkShare())

        self.assertEqual(
            provider.get_shanghai_market_pe_history(),
            [
                {
                    "trade_date": "2026-01-01",
                    "market_pe": 14.8,
                    "index_close": 3210.0,
                    "source": "akshare:stock_market_pe_lg",
                },
                {
                    "trade_date": "2026-01-03",
                    "market_pe": 15.0,
                    "index_close": None,
                    "source": "akshare:stock_market_pe_lg",
                },
            ],
        )
        self.assertEqual(
            provider.get_china_ten_year_yield_history("2026-01-01"),
            [
                {
                    "trade_date": "2026-01-01",
                    "ten_year_yield_percent": 1.9,
                    "source": "akshare:bond_zh_us_rate",
                },
                {
                    "trade_date": "2026-01-03",
                    "ten_year_yield_percent": 2.0,
                    "source": "akshare:bond_zh_us_rate",
                },
            ],
        )
        self.assertEqual(
            provider.get_50etf_qvix_history(),
            [
                {
                    "trade_date": "2026-01-01",
                    "qvix_close": 19.0,
                    "source": "akshare:index_option_50etf_qvix",
                },
                {
                    "trade_date": "2026-01-03",
                    "qvix_close": 20.0,
                    "source": "akshare:index_option_50etf_qvix",
                },
            ],
        )
        self.assertEqual(
            provider.get_shanghai_market_pb_history()[0],
            {
                "trade_date": "2026-01-01",
                "index_close": 3210.0,
                "market_pb": 1.5,
                "equal_weight_pb": 4.2,
                "median_pb": 2.4,
                "source": "akshare:stock_market_pb_lg",
            },
        )
        activity = provider.get_market_activity_snapshot()
        self.assertEqual(activity["trade_date"], "2026-08-07")
        self.assertEqual(activity["advance_count"], 2670)
        self.assertEqual(activity["decline_count"], 2399)
        self.assertEqual(activity["activity_ratio"], 0.5129)
        industry = provider.get_sw_market_daily_metrics(
            "2026-08-03",
            "2026-08-07",
        )[0]
        self.assertEqual(industry["index_code"], "801001")
        self.assertEqual(industry["pe"], 25.29)
        self.assertEqual(industry["dividend_yield_percent"], 1.57)
        level_one = provider.get_sw_industry_daily_metrics(
            "2026-08-03",
            "2026-08-07",
        )[0]
        self.assertEqual(level_one["index_code"], "801010")
        self.assertEqual(level_one["index_name"], "农林牧渔")
        valuation = provider.get_index_valuation_history(
            "沪深300"
        )[0]
        self.assertEqual(valuation["trade_date"], "2026-08-07")
        self.assertEqual(valuation["ttm_pe"], 13.71)
        self.assertEqual(valuation["static_pe"], 13.99)
        self.assertEqual(valuation["pb"], 1.44)
        self.assertEqual(valuation["equal_weight_pb"], 4.47)
        official = provider.get_csindex_official_valuation(
            "000300"
        )[0]
        self.assertEqual(official["index_code"], "000300")
        self.assertEqual(official["pe1"], 14.89)
        self.assertEqual(official["dividend_yield1_percent"], 2.63)

    def test_baostock_daily_bars_are_normalized(self):
        fake = FakeBaoStock()
        provider = BaoStockDataSource(fake)
        bars = provider.get_a_share_daily_bars("000001.SZ", "20260101", "20260131")
        index_bars = provider.get_index_daily_bars(
            "000001",
            "20260101",
            "20260131",
            "sh",
        )
        provider.logout()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].source, "baostock:query_history_k_data_plus")
        self.assertEqual(bars[0].symbol, "000001")
        self.assertEqual(bars[0].close, 10.3)
        self.assertEqual(index_bars[0].raw["code"], "sh.000001")
        self.assertTrue(fake.logged_out)

    def test_baostock_reference_and_financial_methods_return_records(self):
        provider = BaoStockDataSource(FakeBaoStock())
        self.assertEqual(provider.get_trade_dates("20260101", "20260131")[0]["is_trading_day"], "1")
        self.assertEqual(provider.get_stock_basic("000001")[0]["code_name"], "平安银行")
        self.assertEqual(provider.get_stock_basic_records("000001")[0].name, "平安银行")
        self.assertEqual(provider.get_profit_data("000001", 2026, 1)[0]["roeAvg"], "10.0")


if __name__ == "__main__":
    unittest.main()
