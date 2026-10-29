import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from src.discovery_funnel import (
    DiscoveryFunnelService,
    DiscoveryScope,
    SQLiteDiscoveryFunnelRepository,
)
from src.discovery_funnel.features import shift_quarter
from src.sector_data import IndustryMember, SwIndustry

AS_OF = "2026-10-23"
INDUSTRY = SwIndustry("801120", "食品饮料", 1, None, 8)
# symbol: (name, level-1 industry, annual ROE %, PE path, market-cap path)
MEMBERS = {
    "000001": ("质量估值", "食品饮料"),
    "000002": ("某银行", "银行"),
    "000003": ("*ST 某某", "食品饮料"),
    "000004": ("盈利拐点", "食品饮料"),
    "000005": ("深度回撤", "食品饮料"),
    "000006": ("估值低位", "食品饮料"),
    "000007": ("平庸甲", "食品饮料"),
    "000008": ("平庸乙", "食品饮料"),
    "000009": ("一次性收益", "食品饮料"),
}


def _days(start: str, end: str) -> list[str]:
    current, last, days = date.fromisoformat(start), date.fromisoformat(end), []
    while current <= last:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


DAYS = _days("2018-01-01", AS_OF)


def _valuation(symbol: str) -> list[dict]:
    rows = []
    total = len(DAYS)
    for index, day in enumerate(DAYS):
        progress = index / total
        pe, cap = 50.0, 1e10 * (1 + progress)
        if symbol == "000001":
            pe = 12.0
        elif symbol == "000009":
            pe = 10.0
        elif symbol == "000004":
            pe = 30.0
        elif symbol == "000005":
            pe = 25.0
            cap = 1e10 * (1 + 2 * progress) if index < total - 260 else 1e10 * 3 * 0.5
        elif symbol == "000006":
            pe = 40 - 25 * progress
        rows.append({"trade_date": day, "close": 10.0, "pe_ttm": pe, "pb": 2.0, "market_cap": cap})
    return rows


def _quarter_profit(symbol: str, period: str) -> float:
    if symbol == "000009":
        return 3e9
    if symbol == "000004" and period == "2026-06-30":
        return 1.3e8
    if symbol == "000004" and period == "2026-03-31":
        return 0.9e8
    return 1e8


def _report_row(symbol: str, period: str) -> dict:
    months = int(period[5:7])
    year = int(period[:4])
    roe = {"000001": 18, "000004": 10, "000005": 20, "000006": 12, "000009": 20}.get(symbol, 3)
    quarters = [f"{year}-{m:02d}-{31 if m in (3, 12) else 30}" for m in (3, 6, 9, 12) if m <= months]
    profit = sum(_quarter_profit(symbol, q) for q in quarters)
    announced = (date.fromisoformat(period) + timedelta(days=30)).isoformat()
    return {
        "symbol": symbol,
        "name": MEMBERS[symbol][0],
        "report_period": period,
        # Year-to-date ROE is seasonal: most of it is earned in the first quarter.
        "roe_percent": roe * {3: 0.4, 6: 0.6, 9: 0.8, 12: 1.0}[months],
        "net_profit": profit,
        "net_profit_yoy_percent": 0.0,
        "revenue": 1e9 * (1 + 0.05 * (year - 2015)) * len(quarters),
        "revenue_yoy_percent": 5.0,
        "debt_ratio_percent": 40.0,
        "operating_cash_flow": -1e8 if months == 3 else 1e8 * months / 3,
        "gross_margin_percent": 40.0,
        "announced_on": announced,
    }


class _CrossSection:
    def __init__(self):
        self.periods = []

    def report_period(self, period, *, as_of_date=None, allow_remote=True):
        self.periods.append(period)
        return {"rows": [_report_row(s, period) for s in MEMBERS], "provider_error": None}

    def valuation_history(self, symbols, *, start_date, end_date, allow_remote=True):
        return {
            "series": {s: [r for r in _valuation(s) if r["trade_date"] <= end_date] for s in symbols},
            "provider_errors": {},
        }

    def market_snapshot(self, *, allow_remote=True):
        return {
            "snapshot_id": "snapshot:1",
            "fetched_at": "2026-10-27T01:00:00+00:00",
            "trade_date": "2026-10-27",
            "rows": [
                {"symbol": s, "name": n, "pe_ttm": 20.0, "pb": 2.0, "quote_date": "2026-10-27"}
                for s, (n, _) in MEMBERS.items()
            ],
        }


class _SectorData:
    def industry(self, code, *, allow_remote=True):
        return INDUSTRY if code == INDUSTRY.code else None

    def members(self, code, *, allow_remote=True):
        return tuple(IndustryMember(s, n, code, None) for s, (n, _) in MEMBERS.items())

    def level1_by_symbol(self, *, allow_remote=True):
        return {
            s: SwIndustry("x", l1, 1, None, 0) for s, (_, l1) in MEMBERS.items()
        }


class DiscoveryFunnelTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = DiscoveryFunnelService(
            cross_section=_CrossSection(),
            sector_data=_SectorData(),
            repository=SQLiteDiscoveryFunnelRepository(Path(self.temp_dir.name) / "f.sqlite3"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_industry_scope_ranks_candidates_with_reasons_and_rule_history(self):
        run = self.service.run(DiscoveryScope.parse("industry:801120"), as_of_date=AS_OF)

        by_symbol = {item["symbol"]: item for item in run["candidates"]}
        self.assertEqual(set(by_symbol), {"000001", "000004", "000005", "000006", "000009"})
        self.assertEqual(run["candidates"][-1]["symbol"], "000009")
        self.assertTrue(by_symbol["000009"]["one_off_suspected"])
        self.assertIn("非经常性损益", by_symbol["000009"]["counter_evidence"][0])
        self.assertNotIn("000009", run["selected_for_base_pack"])
        self.assertIn("valuation_low", by_symbol["000006"]["strategies"])
        self.assertIn("quality_drawdown", by_symbol["000005"]["strategies"])
        self.assertIn("earnings_inflection", by_symbol["000004"]["strategies"])
        self.assertEqual(by_symbol["000004"]["features"]["report_period"], "2026-06-30")
        self.assertAlmostEqual(by_symbol["000004"]["features"]["net_profit_quarter_yoy"], 30.0)
        self.assertAlmostEqual(by_symbol["000001"]["features"]["roe"], 18.0)
        self.assertEqual(by_symbol["000001"]["features"]["roe_basis"], "ttm")
        self.assertGreater(by_symbol["000001"]["features"]["operating_cash_flow"], 0)
        self.assertTrue(by_symbol["000005"]["counter_evidence"])
        self.assertEqual(run["candidates"][0]["rank"], 1)
        self.assertEqual(len(run["selected_for_base_pack"]), 4)
        self.assertEqual(
            set(run["excluded"]),
            {"金融机构：只排除并标注，不参与排序", "ST 或退市整理"},
        )
        quality = next(s for s in run["strategies"] if s["strategy_id"] == "quality_value")
        self.assertEqual(quality["main_elimination_reasons"][0]["condition"], "ROE(TTM) ≥ 10%")
        self.assertGreater(quality["rule_history"]["6m"]["samples"], 5)
        self.assertIn("hit_rate", quality["rule_history"]["12m"])
        self.assertIn("2016-06-30", self.service.cross_section.periods)

    def test_same_inputs_reproduce_the_run_id_and_are_stored(self):
        first = self.service.run(DiscoveryScope.parse("industry:801120"), as_of_date=AS_OF, history=False)
        second = self.service.run(DiscoveryScope.parse("industry:801120"), as_of_date=AS_OF, history=False)

        self.assertEqual(first["run_id"], second["run_id"])
        stored = self.service.get(first["run_id"])
        self.assertEqual(stored["members"]["000007"]["results"]["quality_value"]["passed"], False)
        self.assertIsNone(first["strategies"][0]["rule_history"])

    def test_market_scope_uses_snapshot_and_skips_history_strategies(self):
        with self.assertRaisesRegex(ValueError, "截止日不能早于"):
            self.service.run(DiscoveryScope.parse("market"), as_of_date=AS_OF)

        run = self.service.run(DiscoveryScope.parse("market"), as_of_date="2026-10-27")

        skipped = [s["strategy_id"] for s in run["strategies"] if not s["computed"]]
        self.assertEqual(skipped, ["quality_drawdown", "valuation_low"])
        self.assertEqual(run["data_basis"]["snapshot_id"], "snapshot:1")
        self.assertNotIn("2016-06-30", self.service.cross_section.periods)

    def test_recurring_profit_replaces_the_one_off_heuristic(self):
        calls = []

        def lookup(symbol, as_of_date):
            calls.append(symbol)
            if symbol == "000001":
                return {"report_period": "2026-06-30", "basis": "ttm", "earnings": 4e8,
                        "recurring_earnings": 0.5e8, "non_recurring_share": 0.875}
            if symbol == "000009":
                return {"report_period": "2026-06-30", "basis": "ttm", "earnings": 1.2e10,
                        "recurring_earnings": 1.15e10, "non_recurring_share": 0.04}
            return None

        self.service.recurring_lookup = lookup
        run = self.service.run(
            DiscoveryScope.parse("industry:801120"), as_of_date=AS_OF, base_pack_count=2, history=False
        )

        by_symbol = {item["symbol"]: item for item in run["candidates"]}
        self.assertTrue(by_symbol["000001"]["one_off_suspected"])
        self.assertIn("扣非", by_symbol["000001"]["counter_evidence"][0])
        self.assertFalse(by_symbol["000009"]["one_off_suspected"])
        self.assertEqual(run["candidates"][-1]["symbol"], "000001")
        self.assertNotIn("000001", run["selected_for_base_pack"])
        self.assertIn("000009", calls)
        self.assertTrue(any("逐只" in gap for gap in run["gaps"]))

    def test_scope_parsing_and_quarter_shift(self):
        scope = DiscoveryScope.parse("symbols:600519, 858", label="白酒")
        self.assertEqual(scope.symbols, ("600519", "000858"))
        with self.assertRaises(ValueError):
            DiscoveryScope.parse("index")
        self.assertEqual(shift_quarter("2026-03-31", -1), "2025-12-31")
        self.assertEqual(shift_quarter("2026-06-30", -4), "2025-06-30")


if __name__ == "__main__":
    unittest.main()
