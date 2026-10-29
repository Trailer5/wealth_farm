import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.data_src.eastmoney.fund_portfolio import parse_portfolio
from src.fund_research import (
    FundPortfolioService,
    IndexWeightService,
    SQLiteFundResearchRepository,
    fund_asset_class,
    look_through,
    tracked_index_code,
)
from src.master_data import ASSET_TYPE_OPEN_FUND, Asset
from src.research_contracts import (
    ASSET_CLASS_BOND,
    ASSET_CLASS_CASH,
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_OVERSEAS,
)


def _box(period: str, rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        f"<tr><td>{rank}</td><td><a href='//quote.eastmoney.com/unify/r/{prefix}.{code}'>{code}</a></td>"
        f"<td class='tol'><a>{name}</a></td><td class='tor'><span></span></td><td class='tor'><span></span></td>"
        f"<td class='xglj'><a>变动详情</a></td><td class='tor'>{ratio}</td><td class='tor'>1.00</td><td class='tor'>2.00</td></tr>"
        for rank, (prefix, code, name, ratio) in enumerate(rows, start=1)
    )
    return (
        f"<div class='boxitem w790'><h4 class='t'><label class='left'>股票投资明细</label>"
        f"<label class='right'>截止至：<font class='px12'>{period}</font></label></h4>"
        "<table class='w782 comm tzxq'><thead><tr><th class='first'>序号</th><th>股票代码</th><th>股票名称</th>"
        "<th>最新价</th><th>涨跌幅</th><th class='xglj'>相关资讯</th><th>占净值<br />比例</th>"
        "<th class='cgs'>持股数<br />（万股）</th><th class='last ccs'>持仓市值<br />（万元）</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


HOLDINGS = (
    'var apidata={ content:"<div class=\'box\'>'
    + _box("2026-09-30", [("116", "00981", "中芯国际", "6.15%"), ("1", "688099", "晶晨股份", "5.74%")])
    + _box("2026-06-30", [("0", "002475", "立讯精密", "4.77%")])
    + '",arryear:[2026,2025],curyear:2026};'
)
ALLOCATION = (
    '<table class="w782 comm tzxq"><thead><tr><th>报告期</th><th>股票占净比</th><th>债券占净比</th>'
    "<th>现金占净比</th><th>净资产（亿元）</th></tr></thead><tbody>"
    '<tr><td>2026-09-30</td><td class="tor">90.34%</td><td class="tor">5.47%</td><td class="tor">7.31%</td><td class="tor">107.67</td></tr>'
    '<tr><td>2026-06-30</td><td class="tor">92.58%</td><td class="tor">---</td><td class="tor">2.97%</td><td class="tor">131.28</td></tr>'
    "</tbody></table>"
)
INDUSTRIES = json.dumps(
    {
        "Data": {
            "QuarterInfos": [
                {
                    "JZRQ": "2026-09-30",
                    "HYPZInfo": [
                        {"FSRQ": "2026-09-30", "HYDM": "C", "HYMC": "制造业", "ZJZBL": "34.17"},
                        {"FSRQ": "2026-09-30", "HYDM": "35", "HYMC": "医疗保健", "ZJZBL": "25.56"},
                    ],
                }
            ]
        }
    },
    ensure_ascii=False,
)


def _disclosure():
    return parse_portfolio(
        "163417",
        holdings_pages=[HOLDINGS],
        allocation_html=ALLOCATION,
        industry_pages=[INDUSTRIES, "not json"],
        fetched_on="2026-10-28",
        source="eastmoney:fundf10",
        source_urls=("u",),
    )


class FundPortfolioParserTest(unittest.TestCase):
    def test_parses_holdings_markets_and_allocation(self):
        disclosure = _disclosure()

        latest = [item for item in disclosure.holdings if item.period_end == "2026-09-30"]
        self.assertEqual([(item.code, item.market) for item in latest], [("00981", "hk"), ("688099", "sh")])
        self.assertEqual(latest[0].net_asset_ratio, Decimal("0.0615"))
        self.assertEqual(disclosure.allocations[0].stock_ratio, Decimal("0.9034"))
        self.assertIsNone(disclosure.allocations[1].bond_ratio)
        schemes = {item.name: (item.scheme, item.net_asset_ratio) for item in disclosure.industries}
        self.assertEqual(schemes["制造业"], ("csrc", Decimal("0.3417")))
        self.assertEqual(schemes["医疗保健"][0], "hk")
        self.assertIn("industry_allocation_not_json", disclosure.parse_issues)
        self.assertEqual(type(disclosure).from_dict(disclosure.to_dict()), disclosure)


class IndexWeightTest(unittest.TestCase):
    def test_tracked_index_prefers_the_longest_name(self):
        self.assertEqual(tracked_index_code("中证A500指数"), ("中证A500", "000510"))
        self.assertEqual(tracked_index_code("沪深300指数"), ("沪深300", "000300"))
        self.assertIsNone(tracked_index_code("纳斯达克100指数"))

    def test_weights_are_cached_for_a_week(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            today = [date(2026, 10, 28)]
            rows = [{"weight_date": "2026-09-30", "index_name": "沪深300", "code": "600519", "name": "贵州茅台", "exchange": "", "weight": "0.031"}]
            service = IndexWeightService(
                repository=SQLiteFundResearchRepository(Path(temp_dir) / "fund.sqlite3"),
                fetcher=lambda code, timeout: calls.append(code) or rows,
                today=lambda: today[0],
            )

            first, _ = service.weights("000300")
            second, _ = service.weights("000300")
            today[0] = date(2026, 11, 10)
            service.weights("000300")

            self.assertEqual(first, second)
            self.assertEqual(calls, ["000300", "000300"])


class LookThroughTest(unittest.TestCase):
    def test_only_reports_published_by_the_cutoff_are_used(self):
        disclosure = _disclosure()

        before = look_through(disclosure, as_of_date="2026-10-20")
        after = look_through(disclosure, as_of_date="2026-10-28")

        self.assertEqual(before["allocation"]["period_end"], "2026-06-30")
        self.assertEqual(before["holdings_period"], "2026-06-30")
        self.assertEqual(after["holdings_period"], "2026-09-30")
        self.assertEqual(len(after["holdings"]), 2)
        self.assertEqual(before["industries"], [])
        self.assertEqual(after["industry_period"], "2026-09-30")

    def test_whole_fund_classes(self):
        self.assertEqual(fund_asset_class("货币型-普通货币", None, "余额宝"), ASSET_CLASS_CASH)
        self.assertEqual(fund_asset_class("指数型-海外股票", "纳斯达克100指数", None), ASSET_CLASS_OVERSEAS)
        self.assertEqual(fund_asset_class("指数型-其他", "黄金9999", None), ASSET_CLASS_COMMODITY)
        self.assertEqual(fund_asset_class("指数型-固收", "上证5年期国债指数", None), ASSET_CLASS_BOND)
        self.assertIsNone(fund_asset_class("混合型-灵活", None, None))
        self.assertIsNone(fund_asset_class("指数型-股票", "沪深300指数", None))

    def test_cache_is_reused_within_a_week(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            service = FundPortfolioService(
                repository=SQLiteFundResearchRepository(Path(temp_dir) / "fund.sqlite3"),
                fetcher=lambda symbol, timeout: calls.append(symbol) or _disclosure(),
                today=lambda: date(2026, 10, 30),
            )
            asset = Asset("fund:163417", ASSET_TYPE_OPEN_FUND, "163417", "兴全合宜", "cn", None)

            first, error = service.disclosure(asset)
            second, _ = service.disclosure(asset)

            self.assertIsNone(error)
            self.assertEqual(first, second)
            self.assertEqual(calls, ["163417"])


if __name__ == "__main__":
    unittest.main()
