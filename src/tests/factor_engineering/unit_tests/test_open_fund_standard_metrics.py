import unittest

from src.factor_engineering import OpenFundStandardMetricCalculator


class OpenFundStandardMetricCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = OpenFundStandardMetricCalculator()

    def test_uses_latest_observation_on_or_before_calendar_target(self):
        result = self.calculator.calculate(
            asset_id="fund:test",
            records=[
                _nav("2025-12-31", 1.00),
                _nav("2026-07-17", 1.10),
                _nav("2026-08-21", 1.21),
            ],
            source_dataset="open_fund_nav",
            as_of_date="2026-08-22",
        )

        by_name = {
            value.factor_name: value for value in result.values
        }
        one_month = by_name["open_fund_standard_return_1m"]
        self.assertAlmostEqual(one_month.value, 0.1)
        self.assertEqual(one_month.input_start_date, "2026-07-17")
        self.assertEqual(
            one_month.parameters["target_start_date"],
            "2026-07-21",
        )
        ytd = by_name["open_fund_standard_return_ytd"]
        self.assertAlmostEqual(ytd.value, 0.21)
        self.assertEqual(ytd.input_start_date, "2025-12-31")

    def test_short_history_and_large_start_gap_are_unavailable(self):
        result = self.calculator.calculate(
            asset_id="fund:test",
            records=[
                _nav("2026-06-01", 1.00),
                _nav("2026-07-01", 1.05),
                _nav("2026-08-21", 1.10),
            ],
            source_dataset="open_fund_nav",
            as_of_date="2026-08-22",
        )

        gaps = {item.key: item.reason for item in result.unavailable_windows}
        self.assertEqual(
            gaps["1m"],
            "no_observation_near_window_start",
        )
        self.assertEqual(
            gaps["1y"],
            "history_shorter_than_window",
        )
        self.assertIn(
            "open_fund_standard_return_since_inception",
            {value.factor_name for value in result.values},
        )

    def test_accumulated_nav_is_not_mislabeled_as_reinvested_return(self):
        result = self.calculator.calculate(
            asset_id="fund:test",
            records=[
                _nav("2025-08-21", 1.00, accumulated=1.00),
                _nav("2026-08-21", 0.95, accumulated=1.05),
            ],
            source_dataset="open_fund_nav",
            as_of_date="2026-08-22",
        )

        one_year = next(
            value
            for value in result.values
            if value.factor_name == "open_fund_standard_return_1y"
        )
        self.assertAlmostEqual(one_year.value, 0.05)
        self.assertEqual(result.nav_basis, "accumulated_nav")
        self.assertEqual(
            result.return_semantics,
            "accumulated_nav_change_not_reinvested_total_return",
        )


def _nav(value_date, unit, *, accumulated=None):
    return {
        "nav_date": value_date,
        "unit_nav": unit,
        "accumulated_nav": accumulated,
        "source": "unit-test:nav",
    }


if __name__ == "__main__":
    unittest.main()
