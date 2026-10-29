import unittest
from datetime import date
from types import SimpleNamespace

from src.data_src import AkShareDataSource


class _Frame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        return list(self.records)


def _module():
    return SimpleNamespace(
        stock_info_sz_name_code=lambda symbol: _Frame(
            [{"板块": "主板", "A股代码": "000002", "A股简称": "万  科Ａ", "A股上市日期": "1991-01-29"}]
        ),
        stock_info_sh_delist=lambda symbol: _Frame(
            [{"公司代码": "600001", "公司简称": "邯郸钢铁", "上市日期": date(1998, 1, 22), "暂停上市日期": date(2009, 12, 29)}]
        ),
        fund_etf_category_sina=lambda symbol: _Frame(
            [{"代码": "sh510300", "名称": "沪深300ETF"}, {"代码": "bad", "名称": "x"}]
        ),
        index_stock_info=lambda: _Frame(
            [
                {"index_code": "000905", "display_name": "中证500", "publish_date": "2007-01-15"},
                {"index_code": "399006", "display_name": "创业板指", "publish_date": "2010-06-01"},
            ]
        ),
    )


class ExchangeListingSegmentTest(unittest.TestCase):
    def setUp(self):
        self.provider = AkShareDataSource(_module())

    def test_stock_rows_carry_exchange_and_dates(self):
        (row,) = self.provider.get_exchange_listing_segment("sz_stock")
        self.assertEqual(
            (row["code"], row["exchange"], row["asset_type"], row["name"], row["listed_on"], row["board"]),
            ("000002", "sz", "stock", "万科Ａ", "1991-01-29", "主板"),
        )
        (delisted,) = self.provider.get_exchange_listing_segment("sh_delisted")
        self.assertEqual((delisted["listed_on"], delisted["delisted_on"]), ("1998-01-22", "2009-12-29"))

    def test_funds_take_the_exchange_from_the_code_prefix(self):
        rows = self.provider.get_exchange_listing_segment("etf")
        self.assertEqual([(row["code"], row["exchange"], row["asset_type"]) for row in rows], [("510300", "sh", "exchange_fund")])

    def test_index_exchange_by_code_family(self):
        rows = self.provider.get_exchange_listing_segment("index")
        self.assertEqual([(row["code"], row["exchange"]) for row in rows], [("000905", "sh"), ("399006", "sz")])

    def test_unknown_segment_is_rejected(self):
        with self.assertRaises(ValueError):
            self.provider.get_exchange_listing_segment("hk_stock")


class SinaIndexHistoryTest(unittest.TestCase):
    def test_full_history_is_used_and_cut_to_the_range(self):
        calls = []

        def sina(symbol):
            calls.append(symbol)
            return _Frame(
                [
                    {"date": date(2010, 6, 1), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                    {"date": date(2010, 6, 2), "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": 12},
                ]
            )

        def eastmoney(**kwargs):
            raise AssertionError("Eastmoney should not be asked when Sina answers")

        provider = AkShareDataSource(SimpleNamespace(stock_zh_index_daily=sina, index_zh_a_hist=eastmoney))
        bars = provider.get_index_daily_bars("399006", "2005-01-01", "2010-06-01", exchange="sz")

        self.assertEqual(calls, ["sz399006"])
        self.assertEqual([(bar.trade_date, bar.close) for bar in bars], [("2010-06-01", 1.5)])


if __name__ == "__main__":
    unittest.main()
