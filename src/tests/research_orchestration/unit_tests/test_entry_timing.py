import unittest
from datetime import date, timedelta

from src.research_orchestration.entry_timing import EntryTimingService, analyse, state_at


def _cycle_rows(cycles=12, tail_down_days=10):
    """Repeating 80 rising and 40 falling days, ending inside a falling leg."""

    levels, level = [], 1.0
    for _ in range(cycles):
        for _ in range(80):
            level *= 1.005
            levels.append(level)
        for _ in range(40):
            level *= 0.995
            levels.append(level)
    for _ in range(80):
        level *= 1.005
        levels.append(level)
    for _ in range(tail_down_days):
        level *= 0.995
        levels.append(level)
    start = date(2015, 1, 1)
    return [{"date": (start + timedelta(days=i)).isoformat(), "close": value, "total_return_level": value}
            for i, value in enumerate(levels)]


class EntryTimingTest(unittest.TestCase):
    def test_state_reads_trend_range_and_year_bucket(self):
        levels = [1.0 + 0.001 * i for i in range(300)] + [1.2 - 0.006 * i for i in range(30)]
        state = state_at(levels, len(levels) - 1)

        self.assertEqual(state["trend"], "below_ma60")
        self.assertEqual(state["range_position"], "near_low")
        self.assertEqual(state["year"], "-20%_to_0")
        self.assertIsNone(state_at(levels, 100))

    def test_falling_state_defers_half_of_a_buy_and_front_loads_a_sale(self):
        rows = _cycle_rows()
        as_of = rows[-1]["date"]

        buy = analyse(rows, as_of_date=as_of, window_days=21, side="buy")
        sell = analyse(rows, as_of_date=as_of, window_days=21, side="sell")

        self.assertEqual(buy["matched_on"], ["trend", "range_position", "year"])
        self.assertGreaterEqual(buy["samples"]["episodes"], 5)
        self.assertLess(buy["forward"]["21"]["state"]["median"], buy["forward"]["21"]["baseline"]["median"])
        self.assertEqual(buy["recommendation"]["plan"], "defer_part")
        self.assertEqual([t["after_trading_days"] for t in buy["recommendation"]["tranches"]], [0, 21])
        self.assertAlmostEqual(sum(t["share"] for t in buy["recommendation"]["tranches"]), 1.0)
        self.assertEqual(sell["recommendation"]["plan"], "front_load")

    def test_rows_after_the_cutoff_are_ignored(self):
        rows = _cycle_rows()
        cutoff = rows[-30]["date"]

        result = analyse(rows, as_of_date=cutoff, window_days=21)

        self.assertEqual(result["last"]["date"], cutoff)
        self.assertEqual(result["history"]["bars"], len(rows) - 29)

    def test_short_history_falls_back_to_time_diversification(self):
        rows = _cycle_rows()[:200]

        result = analyse(rows, as_of_date=rows[-1]["date"], window_days=21)

        self.assertEqual(result["error"], "insufficient_history")
        self.assertEqual(result["recommendation"]["plan"], "split_even")
        self.assertEqual([t["after_trading_days"] for t in result["recommendation"]["tranches"]], [0, 10])

    def test_service_reads_history_through_price_history(self):
        rows = _cycle_rows()

        class FakePrices:
            def resolve(self, symbol, *, market=None, asset_type=None):
                self.resolved = (symbol, asset_type)
                return "asset"

            def history(self, asset, *, start_date, end_date, rows, allow_remote):
                self.window = (start_date, end_date, rows)
                return {"asset": {"symbol": "510300"}, "rows": _cycle_rows(), "sources": ["test"], "stale": False}

        prices = FakePrices()
        result = EntryTimingService(price_history=prices).run(
            "510300", as_of_date=rows[-1]["date"], asset_type="exchange_fund", years=10)

        self.assertEqual(prices.resolved, ("510300", "exchange_fund"))
        self.assertTrue(prices.window[2])
        self.assertEqual(result["asset"]["symbol"], "510300")
        self.assertEqual(result["recommendation"]["plan"], "defer_part")


if __name__ == "__main__":
    unittest.main()
