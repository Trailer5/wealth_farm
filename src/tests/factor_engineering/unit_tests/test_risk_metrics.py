import math
import tempfile
import unittest
from pathlib import Path

from src.factor_engineering import (
    FactorService,
    RiskMetricCalculator,
    RiskMetricParameters,
    SQLiteFactorRepository,
)
from src.master_data import (
    MasterDataService,
    SQLiteMasterDataRepository,
)


class RiskMetricCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = RiskMetricCalculator()

    def test_fixed_series_metrics_and_drawdown_dates(self):
        closes = [100.0, 110.0, 88.0, 90.0, 111.0]
        result = self.calculator.calculate(
            asset_id="asset:test",
            records=_records(closes),
            source_dataset="stock_daily_bars",
            parameters=RiskMetricParameters(
                annualization_days=4,
                rolling_windows=(2,),
            ),
        )
        metrics = {
            value.factor_name: value
            for value in result.metrics
        }
        returns = [
            0.1,
            -0.2,
            90.0 / 88.0 - 1.0,
            111.0 / 90.0 - 1.0,
        ]
        mean = sum(returns) / 4
        daily_volatility = math.sqrt(
            sum((value - mean) ** 2 for value in returns)
            / 4
        )

        self.assertAlmostEqual(
            metrics["annualized_return"].value,
            0.11,
        )
        self.assertAlmostEqual(
            metrics["annualized_volatility"].value,
            daily_volatility * 2,
        )
        self.assertAlmostEqual(
            metrics["downside_volatility"].value,
            0.2,
        )
        self.assertAlmostEqual(
            metrics["sharpe_ratio"].value,
            mean * 4 / (daily_volatility * 2),
        )
        self.assertAlmostEqual(
            metrics["sortino_ratio"].value,
            mean * 4 / 0.2,
        )
        self.assertAlmostEqual(
            metrics["calmar_ratio"].value,
            0.55,
        )
        self.assertAlmostEqual(
            metrics["max_drawdown"].value,
            -0.2,
        )
        episode = result.drawdown_episode
        self.assertEqual(episode.peak_date, "2026-01-02")
        self.assertEqual(episode.trough_date, "2026-01-03")
        self.assertEqual(episode.recovery_date, "2026-01-05")
        self.assertEqual(episode.duration_days, 3)
        self.assertEqual(episode.recovery_days, 2)
        rolling = [
            value
            for value in result.series
            if value.factor_name == "rolling_return_2d"
        ]
        self.assertEqual(len(rolling), 3)
        self.assertAlmostEqual(rolling[0].value, -0.12)
        rolling_volatility = [
            value
            for value in result.series
            if value.factor_name
            == "rolling_annualized_volatility_2d"
        ]
        rolling_sharpe = [
            value
            for value in result.series
            if value.factor_name == "rolling_sharpe_2d"
        ]
        self.assertEqual(len(rolling_volatility), 3)
        self.assertEqual(len(rolling_sharpe), 3)
        first_window = returns[:2]
        first_mean = sum(first_window) / 2
        first_volatility = math.sqrt(
            sum(
                (value - first_mean) ** 2
                for value in first_window
            )
            / 2
        ) * 2
        last_window = returns[-2:]
        last_mean = sum(last_window) / 2
        last_volatility = math.sqrt(
            sum(
                (value - last_mean) ** 2
                for value in last_window
            )
            / 2
        ) * 2
        self.assertAlmostEqual(
            rolling_volatility[0].value,
            first_volatility,
        )
        self.assertAlmostEqual(
            rolling_sharpe[0].value,
            first_mean * 4 / first_volatility,
        )
        self.assertAlmostEqual(
            rolling_volatility[-1].value,
            last_volatility,
        )
        self.assertAlmostEqual(
            rolling_sharpe[-1].value,
            last_mean * 4 / last_volatility,
        )

    def test_constant_series_omits_undefined_ratios(self):
        result = self.calculator.calculate(
            asset_id="asset:test",
            records=_records([10.0, 10.0, 10.0]),
            source_dataset="stock_daily_bars",
            parameters=RiskMetricParameters(
                annualization_days=2,
                rolling_windows=(),
            ),
        )
        metrics = {
            value.factor_name: value.value
            for value in result.metrics
        }

        self.assertEqual(metrics["annualized_return"], 0)
        self.assertEqual(metrics["annualized_volatility"], 0)
        self.assertEqual(metrics["max_drawdown"], 0)
        self.assertNotIn("sharpe_ratio", metrics)
        self.assertNotIn("sortino_ratio", metrics)
        self.assertNotIn("calmar_ratio", metrics)

    def test_single_price_produces_no_fake_result(self):
        result = self.calculator.calculate(
            asset_id="asset:test",
            records=_records([10.0]),
            source_dataset="stock_daily_bars",
        )

        self.assertEqual(result.series, ())
        self.assertEqual(result.metrics, ())
        self.assertIsNone(result.drawdown_episode)

    def test_invalid_prices_and_duplicate_dates_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "close must be positive",
        ):
            self.calculator.calculate(
                asset_id="asset:test",
                records=_records([10.0, -1.0]),
                source_dataset="stock_daily_bars",
            )
        duplicate = _records([10.0, 11.0])
        duplicate.append(dict(duplicate[-1]))
        with self.assertRaisesRegex(
            ValueError,
            "duplicate trade_date",
        ):
            self.calculator.calculate(
                asset_id="asset:test",
                records=duplicate,
                source_dataset="stock_daily_bars",
            )

    def test_window_and_risk_parameters_are_explicit(self):
        result = self.calculator.calculate(
            asset_id="asset:test",
            records=_records([8, 9, 10, 11, 12]),
            source_dataset="stock_daily_bars",
            parameters=RiskMetricParameters(
                window=2,
                annualization_days=252,
                risk_free_rate=0.02,
                rolling_windows=(1,),
                price_adjustment="none",
            ),
        )

        self.assertEqual(
            result.metrics[0].input_start_date,
            "2026-01-03",
        )
        self.assertEqual(
            result.metrics[0].parameters["risk_free_rate"],
            0.02,
        )
        self.assertEqual(
            result.metrics[0].parameters["price_adjustment"],
            "none",
        )

    def test_invalid_parameter_sets_are_rejected(self):
        with self.assertRaises(ValueError):
            RiskMetricParameters(window=0)
        with self.assertRaises(ValueError):
            RiskMetricParameters(rolling_windows=(5, 5))
        with self.assertRaises(ValueError):
            RiskMetricParameters(risk_free_rate=-1)


class RiskMetricServiceTest(unittest.TestCase):
    def test_multiple_windows_are_persisted_by_parameters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "risk.sqlite3"
            master = MasterDataService(
                SQLiteMasterDataRepository(db_path)
            )
            asset = master.resolve_identifier(
                source="unit-test",
                raw_symbol="518880",
                name="黄金ETF",
                asset_type_hint="ETF",
            ).asset
            service = FactorService(
                SQLiteFactorRepository(db_path),
                master,
            )
            records = _records(
                [10, 11, 10, 12, 11, 13, 14]
            )

            results = service.compute_risk_windows_and_store(
                asset_id=asset.asset_id,
                records=records,
                source_dataset="fund_exchange_daily_bars",
                windows=(3, 5, None),
                parameters=RiskMetricParameters(
                    rolling_windows=(2,),
                    risk_free_rate=0.015,
                ),
            )
            stored = service.list_values(
                asset.asset_id,
                factor_name="sharpe_ratio",
            )

            self.assertEqual(len(results), 3)
            self.assertEqual(len(stored), 3)
            self.assertEqual(
                {
                    value.parameters["window"]
                    for value in stored
                },
                {3, 5, "input"},
            )


def _records(closes):
    return [
        {
            "symbol": "TEST",
            "trade_date": f"2026-01-{index:02d}",
            "close": close,
            "source": "unit-test",
        }
        for index, close in enumerate(closes, start=1)
    ]


if __name__ == "__main__":
    unittest.main()
