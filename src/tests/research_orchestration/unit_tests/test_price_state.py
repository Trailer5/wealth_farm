import unittest

from src.research_orchestration.price_history import price_state


def _bars(closes, levels=None):
    levels = levels or closes
    return [
        {"trade_date": f"d{index:03d}", "unadjusted_close": close, "close": level}
        for index, (close, level) in enumerate(zip(closes, levels))
    ]


class PriceStateTest(unittest.TestCase):
    def test_breakout_compares_with_the_prior_high_not_including_the_last_day(self):
        closes = [10.0] * 58 + [9.0, 10.5]

        state = price_state(_bars(closes))

        self.assertEqual(state["prior_high_20"], 10.0)
        self.assertTrue(state["above_prior_high_20"])
        self.assertAlmostEqual(state["ma_60"], (10.0 * 58 + 9.0 + 10.5) / 60, places=5)
        self.assertTrue(state["above_ma_60"])
        self.assertEqual(state["range_60"], {"low": 9.0, "high": 10.5})
        self.assertTrue(state["at_high_60"])
        self.assertEqual(state["missing"], ["prior_high_60"])

    def test_changes_use_total_return_levels_and_lows_use_traded_prices(self):
        closes = [10.0] * 55 + [9.8, 9.6, 9.4, 9.2, 9.0, 8.9]
        # A dividend shows up only in the total-return levels.
        levels = [10.0] * 55 + [10.3, 10.1, 9.9, 9.7, 9.5, 9.4]

        state = price_state(_bars(closes, levels))

        self.assertTrue(state["at_low_60"])
        self.assertAlmostEqual(state["change_5"], 9.4 / 10.3 - 1, places=6)
        self.assertFalse(state["above_ma_20"])

    def test_short_history_lists_missing_windows_instead_of_guessing(self):
        state = price_state(_bars([1.0, 1.1, 1.2]))

        self.assertEqual(state["close"], 1.2)
        self.assertIn("ma_60", state["missing"])
        self.assertIn("change_5", state["missing"])
        self.assertNotIn("ma_60", state)


if __name__ == "__main__":
    unittest.main()
