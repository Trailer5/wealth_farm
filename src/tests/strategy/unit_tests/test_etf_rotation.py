import unittest
from datetime import date, timedelta
from decimal import Decimal

from src.strategy.etf_rotation import (
    ETF_DUAL_MOMENTUM_STRATEGY_ID,
    ETFDualMomentumTrendStrategy,
    ETFPriceSeries,
    ETFRotationParameters,
)


class ETFDualMomentumTrendStrategyTest(unittest.TestCase):
    def setUp(self):
        self.strategy = ETFDualMomentumTrendStrategy()

    def test_ranking_metrics_and_trend_gate_use_hand_calculated_values(self):
        parameters = _parameters(top_n=1)
        series = (
            _series("B", ("100", "105", "108")),
            _series("C", ("100", "110", "100")),
            _series("A", ("100", "100", "110")),
        )

        signal = self.strategy.evaluate(
            series,
            "2026-01-03",
            "2026-01-04",
            parameters,
        )
        by_asset = {metric.asset_id: metric for metric in signal.metrics}

        self.assertEqual(signal.strategy_id, ETF_DUAL_MOMENTUM_STRATEGY_ID)
        self.assertEqual(signal.version, "v1")
        self.assertEqual(signal.maturity, "research_rule")
        self.assertEqual(signal.status, "risk_on")
        self.assertEqual(signal.ranked_assets, ("A", "B"))
        self.assertEqual(signal.target_weights, {"A": Decimal("1")})
        self.assertEqual(by_asset["A"].return_short, Decimal("0.1"))
        self.assertEqual(by_asset["A"].return_long, Decimal("0.1"))
        self.assertEqual(
            by_asset["A"].sma_ratio,
            Decimal("0.047619047619047619047619048"),
        )
        self.assertEqual(
            by_asset["A"].annualized_volatility,
            Decimal("0.05"),
        )
        self.assertEqual(by_asset["A"].relative_score, Decimal("0.10"))
        self.assertTrue(by_asset["A"].eligible)
        self.assertFalse(by_asset["C"].eligible)
        self.assertEqual(by_asset["C"].reason, "failed_trend_filter")

    def test_selected_assets_receive_inverse_volatility_weights(self):
        parameters = ETFRotationParameters(
            short_window=1,
            long_window=3,
            trend_window=2,
            volatility_window=2,
            top_n=2,
            annualization_days=1,
        )
        signal = self.strategy.evaluate(
            (
                _series("B", ("100", "100", "110", "132")),
                _series("A", ("100", "110", "110", "132")),
            ),
            "2026-01-04",
            "2026-01-05",
            parameters,
        )

        self.assertEqual(signal.ranked_assets, ("A", "B"))
        self.assertEqual(
            signal.target_weights["A"],
            Decimal("0.3333333333333333333333333333"),
        )
        self.assertEqual(
            signal.target_weights["B"],
            Decimal("0.6666666666666666666666666667"),
        )
        self.assertEqual(
            sum(signal.target_weights.values(), Decimal("0")),
            Decimal("1"),
        )

    def test_insufficient_history_is_distinct_from_defensive_fallback(self):
        parameters = ETFRotationParameters(
            short_window=1,
            long_window=3,
            trend_window=2,
            volatility_window=2,
            annualization_days=1,
            defensive_asset_id="DEF",
        )
        risk = _series("RISK", ("100", "101", "102"))
        defensive = _series("DEF", ("50", "50", "50"))

        defensive_signal = self.strategy.evaluate(
            (risk, defensive),
            "2026-01-03",
            "2026-01-04",
            parameters,
        )
        cash_signal = self.strategy.evaluate(
            (risk,),
            "2026-01-03",
            "2026-01-04",
            parameters,
        )
        risk_metric = next(
            metric
            for metric in defensive_signal.metrics
            if metric.asset_id == "RISK"
        )

        self.assertFalse(risk_metric.eligible)
        self.assertEqual(risk_metric.reason, "insufficient_history")
        self.assertIsNone(risk_metric.relative_score)
        self.assertEqual(defensive_signal.status, "defensive")
        self.assertEqual(
            defensive_signal.target_weights,
            {"DEF": Decimal("1")},
        )
        self.assertEqual(cash_signal.status, "cash")
        self.assertEqual(cash_signal.target_weights, {})

    def test_prices_after_as_of_are_never_used(self):
        parameters = ETFRotationParameters(
            short_window=1,
            long_window=3,
            trend_window=2,
            volatility_window=2,
            annualization_days=1,
        )
        observed = _series("A", ("100", "110", "110", "132"))
        with_future = ETFPriceSeries(
            asset_id="A",
            dates=observed.dates + ("2026-01-05", "2026-01-06"),
            closes=observed.closes + (Decimal("1"), Decimal("10000")),
        )

        baseline = self.strategy.evaluate(
            (observed,),
            "2026-01-04",
            "2026-01-05",
            parameters,
        )
        future_data_result = self.strategy.evaluate(
            (with_future,),
            "2026-01-04",
            "2026-01-05",
            parameters,
        )

        self.assertEqual(future_data_result, baseline)

    def test_effective_date_and_tie_break_are_strict_and_deterministic(self):
        parameters = ETFRotationParameters(
            short_window=1,
            long_window=3,
            trend_window=2,
            volatility_window=2,
            top_n=2,
            annualization_days=1,
        )
        a = _series("A", ("100", "110", "110", "132"))
        z = _series("Z", ("100", "110", "110", "132"))

        first = self.strategy.evaluate(
            (z, a),
            "2026-01-04",
            "2026-01-05",
            parameters,
        )
        second = self.strategy.evaluate(
            (a, z),
            "2026-01-04",
            "2026-01-05",
            parameters,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.ranked_assets, ("A", "Z"))
        with self.assertRaisesRegex(
            ValueError,
            "effective_date must be later",
        ):
            self.strategy.evaluate(
                (a,),
                "2026-01-04",
                "2026-01-04",
                parameters,
            )

    def test_series_and_parameters_reject_invalid_contract_values(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            ETFPriceSeries(
                asset_id="A",
                dates=("2026-01-01", "2026-01-01"),
                closes=(Decimal("1"), Decimal("2")),
            )
        with self.assertRaisesRegex(ValueError, "positive finite Decimals"):
            ETFPriceSeries(
                asset_id="A",
                dates=("2026-01-01",),
                closes=(1,),
            )
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            ETFRotationParameters(
                short_weight=Decimal("0.6"),
                long_weight=Decimal("0.5"),
            )
        with self.assertRaisesRegex(ValueError, "short_window"):
            ETFRotationParameters(short_window=253)


def _parameters(*, top_n: int) -> ETFRotationParameters:
    return ETFRotationParameters(
        short_window=1,
        long_window=2,
        trend_window=2,
        volatility_window=2,
        top_n=top_n,
        annualization_days=1,
    )


def _series(asset_id: str, closes: tuple[str, ...]) -> ETFPriceSeries:
    start = date(2026, 1, 1)
    return ETFPriceSeries(
        asset_id=asset_id,
        dates=tuple(
            (start + timedelta(days=index)).isoformat()
            for index in range(len(closes))
        ),
        closes=tuple(Decimal(value) for value in closes),
    )


if __name__ == "__main__":
    unittest.main()
