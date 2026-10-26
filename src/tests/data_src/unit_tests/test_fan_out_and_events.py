import time
import unittest

from src.data_src.fan_out import SourceStillRunning, fan_out
from src.data_src.models import DailyBar
from src.data_src.total_return_bars import bars_with_event_preclose


class FanOutTest(unittest.TestCase):
    def test_slow_source_is_cut_off_after_grace_period(self):
        def slow():
            time.sleep(1.0)
            return 3

        def failing():
            raise RuntimeError("down")

        started = time.monotonic()
        outcomes = fan_out(
            {"fast": lambda: 1, "broken": failing, "slow": slow},
            grace_seconds=0.1,
        )

        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(list(outcomes), ["fast", "broken", "slow"])
        self.assertEqual(outcomes["fast"], 1)
        self.assertIsInstance(outcomes["broken"], RuntimeError)
        self.assertIsInstance(outcomes["slow"], SourceStillRunning)

    def test_rejected_outcomes_do_not_start_grace_period(self):
        def later():
            time.sleep(0.3)
            return 5

        outcomes = fan_out(
            {"empty": lambda: 0, "later": later},
            accept=lambda value: value > 0,
            grace_seconds=0.05,
        )

        self.assertEqual(outcomes, {"empty": 0, "later": 5})


class EventPrecloseTest(unittest.TestCase):
    def test_cash_dividend_and_share_split(self):
        bars = [
            DailyBar("512800", day, None, None, None, close, None, None, "ths:kline")
            for day, close in (
                ("2025-07-03", 1.746),
                ("2025-07-04", 1.777),
                ("2025-07-07", 0.894),
                ("2025-07-08", 0.891),
            )
        ]
        events = [
            {
                "ex_date": "2025-07-07",
                "category": 11,
                "cash_per_10": 0.0,
                "bonus_per_10": 0.0,
                "rights_per_10": 0.0,
                "rights_price": 0.0,
                "split_ratio": 2.0,
            },
            {
                "ex_date": "2025-07-08",
                "category": 1,
                "cash_per_10": 0.1,
                "bonus_per_10": 0.0,
                "rights_per_10": 0.0,
                "rights_price": 0.0,
                "split_ratio": 0.0,
            },
        ]

        output = bars_with_event_preclose(bars, events, "2025-07-04")

        self.assertEqual(output[0].trade_date, "2025-07-04")
        self.assertAlmostEqual(output[0].preclose, 1.746)
        self.assertAlmostEqual(output[1].preclose, 0.8885)
        self.assertAlmostEqual(output[2].preclose, 0.884)
        self.assertEqual(output[1].source, "ths:kline:xdxr_preclose")


if __name__ == "__main__":
    unittest.main()
