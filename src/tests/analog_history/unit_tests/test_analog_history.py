import unittest
from datetime import date, timedelta

from src.analog_history import analog_distribution, analog_metric, trailing_percentiles


def _business_days(start: str, count: int) -> list[str]:
    days, current = [], date.fromisoformat(start)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class TrailingPercentileTest(unittest.TestCase):
    def test_percentile_only_uses_past_values_within_window(self):
        values = [
            ("2000-01-01", 5.0),
            ("2015-01-01", 1.0),
            ("2016-01-01", 2.0),
            ("2016-06-01", 3.0),
            ("2017-01-01", 1.5),
        ]

        output = dict(trailing_percentiles(values, window_years=10, min_window=2))

        self.assertNotIn("2000-01-01", output)
        self.assertEqual(output["2016-01-01"], 1.0)
        self.assertEqual(output["2017-01-01"], 2 / 4)


class AnalogDistributionTest(unittest.TestCase):
    def setUp(self):
        days = _business_days("2010-01-04", 3000)
        # Metric alternates between a low and a high regime every 100 trading days.
        self.values = [
            (day, 1.0 if (index // 100) % 2 == 0 else 2.0)
            for index, day in enumerate(days)
        ]
        # Price rises only during the 100 days after a low-regime day.
        closes = []
        level = 100.0
        for index, day in enumerate(days):
            level *= 1.001 if (index // 100) % 2 == 0 else 0.9995
            closes.append((day, level))
        self.closes = closes
        self.as_of = days[-1]

    def test_conditional_distribution_differs_from_baseline(self):
        result = analog_distribution(
            analog_metric("csi300_return_60d"),
            self.values,
            self.closes,
            as_of_date=self.as_of,
            band=(0.0, 0.6),
            horizons=(20,),
        )

        (horizon,) = result["horizons"]
        conditional = horizon["conditional"]["non_overlapping"]
        baseline = horizon["baseline_all_dates"]["non_overlapping"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(horizon["status"], "ok")
        self.assertGreater(conditional["median"], baseline["median"])
        self.assertLess(
            conditional["count"],
            horizon["conditional"]["all_samples"]["count"],
        )
        entries = horizon["conditional"]["non_overlapping_entry_dates"]
        self.assertTrue(all(day <= self.as_of for day in entries))

    def test_forward_window_must_end_before_cutoff_and_small_samples_are_flagged(self):
        cutoff = self.closes[1100][0]

        result = analog_distribution(
            analog_metric("csi300_return_60d"),
            self.values,
            self.closes,
            as_of_date=cutoff,
            band=(0.0, 0.6),
            horizons=(250,),
        )

        (horizon,) = result["horizons"]
        last_entry = horizon["conditional"]["all_samples"]["last_entry"]
        index = [day for day, _ in self.closes].index(last_entry)
        self.assertLessEqual(self.closes[index + 250][0], cutoff)
        self.assertEqual(horizon["status"], "insufficient_samples")
        self.assertIsNotNone(horizon["note"])

    def test_short_history_is_reported_not_computed(self):
        result = analog_distribution(
            analog_metric("csi300_pe_ttm"),
            self.values[:10],
            self.closes,
            as_of_date=self.as_of,
            band=None,
            horizons=(20,),
        )

        self.assertEqual(result["status"], "insufficient_history")


if __name__ == "__main__":
    unittest.main()
