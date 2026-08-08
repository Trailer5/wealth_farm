import unittest

from src.factor_engineering.margin import (
    MarginFactorCalculator,
    MarginFactorParameters,
    MarginTradingRecord,
)


class MarginFactorCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = MarginFactorCalculator()

    def test_formulas_and_metadata_are_independently_calculated(self):
        records = [
            _record(
                "2026-08-03",
                margin_balance=150,
                margin_buy_amount=40,
                source="exchange-b",
            ),
            _record(
                "2026-08-01",
                margin_balance=100,
                margin_buy_amount=20,
                source="exchange-a",
            ),
            _record(
                "2026-08-02",
                margin_balance=120,
                margin_buy_amount=30,
                source="exchange-a",
            ),
        ]

        values = self.calculator.calculate(
            asset_id="asset:stock:TEST",
            records=records,
            daily_amount_by_date={"2026-08-03": 100},
            as_of_date="2026-08-03",
            parameters=MarginFactorParameters(window=2),
        )
        by_name = {value.factor_name: value for value in values}

        self.assertAlmostEqual(
            by_name["margin_balance_change"].value,
            (150 - 100) / 100,
        )
        self.assertAlmostEqual(
            by_name["margin_buy_ratio"].value,
            40 / 100,
        )
        self.assertAlmostEqual(
            by_name["margin_balance_to_amount"].value,
            150 / 100,
        )
        for value in values:
            self.assertEqual(value.calculation_version, "margin_daily_v1")
            self.assertEqual(
                value.parameters,
                {"window": 2, "unit": "CNY"},
            )
            self.assertEqual(
                value.source_dataset,
                "exchange-a,exchange-b",
            )
            self.assertEqual(value.input_start_date, "2026-08-01")
            self.assertEqual(value.input_end_date, "2026-08-03")
            self.assertEqual(value.as_of_date, "2026-08-03")

    def test_future_records_are_excluded(self):
        values = self.calculator.calculate(
            asset_id="asset:stock:TEST",
            records=[
                _record("2026-08-01", margin_balance=100),
                _record("2026-08-02", margin_balance=110),
                _record(
                    "2026-08-03",
                    margin_balance=999,
                    source="future-source",
                ),
            ],
            daily_amount_by_date={
                "2026-08-02": 220,
                "2026-08-03": 1,
            },
            as_of_date="2026-08-02",
            parameters=MarginFactorParameters(window=1),
        )
        by_name = {value.factor_name: value for value in values}

        self.assertAlmostEqual(
            by_name["margin_balance_change"].value,
            0.1,
        )
        self.assertAlmostEqual(
            by_name["margin_balance_to_amount"].value,
            0.5,
        )
        self.assertEqual(
            by_name["margin_balance_change"].source_dataset,
            "unit-test",
        )
        self.assertEqual(
            by_name["margin_balance_change"].as_of_date,
            "2026-08-02",
        )

    def test_duplicate_trade_dates_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "duplicate trade_date",
        ):
            self.calculator.calculate(
                asset_id="asset:stock:TEST",
                records=[
                    _record("2026-08-01"),
                    _record("2026-08-01"),
                ],
                daily_amount_by_date={},
                as_of_date="2026-08-01",
            )

    def test_negative_values_and_invalid_windows_are_rejected(self):
        numeric_fields = (
            "margin_balance",
            "margin_buy_amount",
            "margin_repay_amount",
            "short_balance",
            "short_volume",
            "short_sell_volume",
            "short_repay_volume",
            "total_margin_balance",
        )
        for field_name in numeric_fields:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValueError,
                    "must be non-negative",
                ):
                    _record("2026-08-01", **{field_name: -1})

        with self.assertRaisesRegex(ValueError, "window must be positive"):
            MarginFactorParameters(window=0)

    def test_missing_fields_and_zero_denominator_are_omitted(self):
        records = [
            _record("2026-08-01", margin_balance=100),
            _record(
                "2026-08-02",
                margin_balance=110,
                margin_buy_amount=None,
            ),
        ]
        zero_amount_values = self.calculator.calculate(
            asset_id="asset:stock:TEST",
            records=records,
            daily_amount_by_date={"2026-08-02": 0},
            as_of_date="2026-08-02",
            parameters=MarginFactorParameters(window=1),
        )
        positive_amount_values = self.calculator.calculate(
            asset_id="asset:stock:TEST",
            records=records,
            daily_amount_by_date={"2026-08-02": 220},
            as_of_date="2026-08-02",
            parameters=MarginFactorParameters(window=1),
        )

        self.assertEqual(
            [value.factor_name for value in zero_amount_values],
            ["margin_balance_change"],
        )
        self.assertEqual(
            {
                value.factor_name
                for value in positive_amount_values
            },
            {
                "margin_balance_change",
                "margin_balance_to_amount",
            },
        )

    def test_missing_history_keeps_current_day_factors(self):
        values = self.calculator.calculate(
            asset_id="asset:stock:TEST",
            records=[
                _record(
                    "2026-08-02",
                    margin_balance=40,
                    margin_buy_amount=10,
                )
            ],
            daily_amount_by_date={"2026-08-02": 100},
            as_of_date="2026-08-02",
        )
        by_name = {value.factor_name: value.value for value in values}

        self.assertEqual(
            set(by_name),
            {"margin_buy_ratio", "margin_balance_to_amount"},
        )
        self.assertAlmostEqual(by_name["margin_buy_ratio"], 0.1)
        self.assertAlmostEqual(
            by_name["margin_balance_to_amount"],
            0.4,
        )


def _record(trade_date, **overrides):
    values = {
        "symbol": "TEST",
        "trade_date": trade_date,
        "margin_balance": 100,
        "margin_buy_amount": 10,
        "source": "unit-test",
        "scope": "security",
    }
    values.update(overrides)
    return MarginTradingRecord(**values)


if __name__ == "__main__":
    unittest.main()
