import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.data_src.eastmoney.fund_dealing import _day_bounds, parse_dealing_info
from src.data_src.models import OpenFundDealingInfo
from src.fund_research import (
    OpenFundDecisionFactsService,
    SQLiteFundResearchRepository,
    UnderlyingValuationService,
    benchmark_components,
    dealing_summary,
    manager_tenure,
    match_index,
    purchase_fee_rate,
    redemption_fee_rate,
    round_trip_cost,
)
from src.master_data import ASSET_TYPE_OPEN_FUND, Asset

SECTIONS = {
    "交易状态": [["申购状态", "开放申购", "赎回状态", "开放赎回", "定投状态", "支持"]],
    "申购与赎回金额": [["申购起点", "10.00元", "定投起点", "10.00元", "日累计申购限额", "无限额"]],
    "交易确认日": [["买入确认日", "T+1", "卖出确认日", "T+2"]],
    "运作费用": [["管理费率", "1.20%（每年）", "托管费率", "0.20%（每年）", "销售服务费率", "0.00%（每年）"]],
    "申购费率（前端）": [
        ["适用金额", "原费率|天天基金优惠费率"],
        ["小于100万元", "1.50% | 0.15%"],
        ["大于等于100万元，小于500万元", "1.00% | 0.10%"],
        ["大于等于500万元", "每笔1000元"],
    ],
    "赎回费率": [
        ["适用期限", "赎回费率"],
        ["小于7天", "1.50%"],
        ["大于等于7天，小于1年", "0.50%"],
        ["大于等于1年，小于2年", "0.25%"],
        ["大于等于2年", "0.00%"],
    ],
}
MANAGERS = [
    {"起始期": "2025-03-07", "截止期": "至今", "基金经理": "甲 乙", "任职回报": "4.47%"},
    {"起始期": "2023-01-30", "截止期": "2025-03-06", "基金经理": "甲 丙", "任职回报": "1.56%"},
    {"起始期": "2021-12-27", "截止期": "2023-01-29", "基金经理": "甲", "任职回报": "-16.05%"},
    {"起始期": "2018-01-23", "截止期": "2020-12-08", "基金经理": "甲", "任职回报": "87.06%"},
]


def _info(**overrides):
    info = parse_dealing_info(
        "163417",
        sections=SECTIONS,
        manager_rows=MANAGERS,
        overview={"业绩比较基准": "沪深300指数收益率×60%+恒生指数收益率×20%+中债总指数收益率×20%", "跟踪标的": "该基金无跟踪标的"},
        fetched_on="2026-10-28",
        source="eastmoney:fundf10",
        source_urls=("https://fundf10.eastmoney.com/jjfl_163417.html",),
    )
    return OpenFundDealingInfo.from_dict({**info.to_dict(), **overrides})


class DealingParserTest(unittest.TestCase):
    def test_parses_rules_fees_and_managers(self):
        info = _info()
        self.assertEqual((info.purchase_confirm_days, info.redemption_confirm_days), (1, 2))
        self.assertEqual(info.management_fee_rate, Decimal("0.012"))
        self.assertEqual(info.purchase_fees[0].discounted_rate, Decimal("0.0015"))
        self.assertEqual(info.purchase_fees[2].fixed_fee, 1000.0)
        self.assertEqual(
            [(tier.min_days, tier.max_days) for tier in info.redemption_fees],
            [(0, 7), (7, 365), (365, 730), (730, None)],
        )
        self.assertEqual(info.manager_terms[0].managers, ("甲", "乙"))
        self.assertIsNone(info.manager_terms[0].end_date)
        self.assertIsNone(info.tracking_target)
        self.assertEqual(OpenFundDealingInfo.from_dict(info.to_dict()), info)

    def test_day_bounds(self):
        self.assertEqual(_day_bounds("小于等于6天"), (0, 7))
        self.assertEqual(_day_bounds("大于等于7天，小于等于364天"), (7, 365))
        self.assertEqual(_day_bounds("大于等于6个月"), (180, None))
        self.assertIsNone(_day_bounds("--"))


class DealingCostTest(unittest.TestCase):
    def test_round_trip_and_fee_drag(self):
        info = _info()
        self.assertEqual(purchase_fee_rate(info), Decimal("0.0015"))
        self.assertEqual(purchase_fee_rate(info, amount=2_000_000, discounted=False), Decimal("0.01"))
        self.assertEqual(redemption_fee_rate(info, 6), Decimal("0.015"))
        self.assertEqual(redemption_fee_rate(info, 730), Decimal("0"))
        cost = round_trip_cost(Decimal("0.0015"), Decimal("0.005"))
        self.assertAlmostEqual(float(cost), 1 - 0.995 / 1.0015, places=10)

        summary = dealing_summary(info)
        self.assertEqual(summary["fee_free_from_days"], 730)
        self.assertEqual(summary["ongoing_fee_rate"], "0.014")
        ten = next(item for item in summary["fee_drag"] if item["years"] == 10)
        self.assertAlmostEqual(float(ten["ongoing_drag"]), 1 - 0.986**10, places=4)
        self.assertEqual(len(summary["round_trips"]), 6)

    def test_manager_tenure_follows_continuous_runs(self):
        info = _info()
        nav = [("2018-01-22", 1.0), ("2020-01-02", 1.5), ("2025-03-07", 2.0), ("2026-03-09", 2.2), ("2026-08-21", 2.1)]
        tenure = manager_tenure(info.manager_terms, nav, as_of_date="2026-08-21")
        periods = {item["label"]: item for item in tenure["periods"]}
        self.assertEqual(tenure["lineup_start"], "2025-03-07")
        self.assertAlmostEqual(periods["最近一次经理变动以来"]["total_return"], 0.05)
        # 2020-12-08 → 2021-12-27 is a gap of more than a week, so 甲's run starts 2021-12-27.
        self.assertEqual(periods["甲 连续管理"]["start_date"], "2021-12-27")
        self.assertNotIn("乙 连续管理", periods)
        self.assertIsNone(manager_tenure(info.manager_terms, nav, as_of_date="2017-01-01"))


class _Macro:
    def __init__(self, series):
        self.series = series
        self.calls = []

    def get(self, series_id, *, start_date, end_date, as_of_date, allow_remote):
        self.calls.append(series_id)
        points = tuple(
            SimpleNamespace(period_end=day, value=value) for day, value in self.series.get(series_id, [])
        )
        return SimpleNamespace(points=points, provider_errors={})


class UnderlyingValuationTest(unittest.TestCase):
    def setUp(self):
        pe = [(f"{year}-12-31", float(value)) for year, value in zip(range(2017, 2026), (10, 11, 12, 13, 14, 15, 16, 17, 18))]
        self.macro = _Macro({"csi300_pe_ttm": pe + [("2026-08-21", 12.5)], "csi300_pb": [("2025-12-31", 1.3), ("2026-08-21", 1.4)]})
        self.service = UnderlyingValuationService(macro_data=self.macro)

    def test_matching(self):
        self.assertEqual(match_index("中证1000指数")[1], "csi1000_pe_ttm")
        self.assertEqual(match_index("中证100指数")[1], "csi100_pe_ttm")
        self.assertIsNone(match_index("中证360互联网+大数据100指数"))
        self.assertEqual(
            benchmark_components("沪深300指数收益率×60% + 中债总指数收益率×40%"),
            [("沪深300指数", 0.6), ("中债总指数", 0.4)],
        )

    def test_active_fund_uses_heaviest_equity_component(self):
        result = self.service.assess(
            benchmark_text="沪深300指数收益率×60%+恒生指数收益率×20%+中债总指数收益率×20%",
            tracking_target=None,
            as_of_date="2026-08-21",
        )
        self.assertEqual((result["basis"], result["component_weight"]), ("benchmark_component", 0.6))
        self.assertEqual(result["index"]["pe_ttm"]["value"], 12.5)
        self.assertAlmostEqual(result["index"]["pe_ttm"]["percentile"], 0.4)
        self.assertEqual(result["equity_weight"], 0.8)

    def test_index_without_series_falls_back_to_market_context(self):
        result = self.service.assess(
            benchmark_text="中证360互联网+大数据100指数收益率*95%+商业银行税后活期存款利率*5%",
            tracking_target="中证360互联网+大数据100指数",
            as_of_date="2026-08-21",
        )
        self.assertEqual(result["basis"], "none")
        self.assertIn("没有可用的长期估值序列", result["note"])
        self.assertEqual(result["market_context"]["pe_ttm"]["value"], 12.5)


class DecisionFactsTest(unittest.TestCase):
    def test_cache_freshness_and_fetch_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteFundResearchRepository(Path(temp_dir) / "fund.sqlite3")
            calls = []
            today = [date(2026, 10, 28)]

            def fetcher(symbol, timeout):
                calls.append(symbol)
                if len(calls) > 1:
                    raise OSError("offline")
                return _info()

            service = OpenFundDecisionFactsService(
                repository=repository,
                underlying_valuation=UnderlyingValuationService(macro_data=_Macro({})),
                fetcher=fetcher,
                today=lambda: today[0],
            )
            asset = Asset("fund:163417", ASSET_TYPE_OPEN_FUND, "163417", "兴全合宜", "cn", None)
            facts = service.build(asset, as_of_date="2026-08-21", nav_points=[], allow_remote=True)
            self.assertIn("dealing", facts)
            self.assertTrue(any("2026-10-28 抓取的当前值" in gap for gap in facts["gaps"]))
            service.build(asset, as_of_date="2026-10-28", nav_points=[])
            self.assertEqual(len(calls), 1)

            today[0] = date(2026, 11, 20)
            facts = service.build(asset, as_of_date="2026-11-20", nav_points=[])
            self.assertEqual(len(calls), 2)
            self.assertIn("dealing", facts)
            self.assertTrue(any("使用缓存" in gap for gap in facts["gaps"]))


if __name__ == "__main__":
    unittest.main()
