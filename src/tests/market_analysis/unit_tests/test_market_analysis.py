import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.market_analysis import (
    A_SHARE_MARKET_CALCULATION_VERSION,
    AShareMarketFactorCalculator,
    MarketAnalysisService,
    MarketCrossSectionRow,
    MarketFactorParameters,
    SQLiteMarketFactorRepository,
)


class AShareMarketFactorCalculatorTest(unittest.TestCase):
    def test_independent_sample_matches_all_six_factors_and_exclusions(
        self,
    ):
        result = AShareMarketFactorCalculator().calculate(
            _sample_rows(),
            _parameters(),
        )
        values = {
            value.factor_name: value for value in result.values
        }
        expected_names = {
            "advance_ratio",
            "limit_up_down_spread",
            "above_ma20_ratio",
            "market_turnover_amount_zscore",
            "equal_weight_excess",
            "small_cap_relative_strength",
        }
        historical_mean = (400 + 600 + 800) / 3
        historical_std = math.sqrt(
            (
                (400 - historical_mean) ** 2
                + (600 - historical_mean) ** 2
                + (800 - historical_mean) ** 2
            )
            / 3
        )

        self.assertEqual(set(values), expected_names)
        self.assertAlmostEqual(values["advance_ratio"].value, 2 / 4)
        self.assertAlmostEqual(
            values["limit_up_down_spread"].value,
            0,
        )
        self.assertAlmostEqual(
            values["above_ma20_ratio"].value,
            2 / 3,
        )
        self.assertAlmostEqual(
            values["market_turnover_amount_zscore"].value,
            (1000 - historical_mean) / historical_std,
        )
        self.assertAlmostEqual(
            values["equal_weight_excess"].value,
            0.03,
        )
        self.assertAlmostEqual(
            values["small_cap_relative_strength"].value,
            -0.10,
        )
        for name, value in values.items():
            self.assertEqual(
                value.calculation_version,
                A_SHARE_MARKET_CALCULATION_VERSION,
            )
            self.assertEqual(value.parameters["scope_id"], "cn_a")
            self.assertEqual(
                value.parameters["universe_id"],
                "csi-test",
            )
            self.assertEqual(
                value.parameters["universe_version"],
                "constituents_v1",
            )
            self.assertEqual(
                value.parameters["excluded_non_tradable"],
                1,
            )
            self.assertGreater(value.parameters["sample_count"], 0)
            self.assertGreaterEqual(
                value.parameters["excluded_missing"],
                1,
                name,
            )

    def test_future_universe_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "as_of_date must not be earlier",
        ):
            MarketFactorParameters(
                scope_id="cn_a",
                universe_id="csi-test",
                universe_version="constituents_v1",
                universe_effective_date="2026-02-01",
                as_of_date="2026-01-31",
                benchmark_return=0,
            )

    def test_zero_turnover_variance_omits_zscore(self):
        parameters = MarketFactorParameters(
            scope_id="cn_a",
            universe_id="csi-test",
            universe_version="constituents_v1",
            universe_effective_date="2026-01-01",
            as_of_date="2026-01-30",
            benchmark_return=0,
            historical_turnover_amounts=(1000, 1000, 1000),
        )
        result = AShareMarketFactorCalculator().calculate(
            _sample_rows(),
            parameters,
        )

        self.assertNotIn(
            "market_turnover_amount_zscore",
            {value.factor_name for value in result.values},
        )


class SQLiteMarketFactorRepositoryTest(unittest.TestCase):
    def test_compute_and_store_is_idempotent_without_asset_fk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "market.sqlite3"
            repository = SQLiteMarketFactorRepository(db_path)
            service = MarketAnalysisService(repository)

            first = service.compute_and_store(
                _sample_rows(),
                _parameters(),
            )
            second = service.compute_and_store(
                _sample_rows(),
                _parameters(),
            )
            stored = repository.list_values(
                "cn_a",
                universe_id="csi-test",
                universe_version="constituents_v1",
            )
            connection = sqlite3.connect(db_path)
            try:
                foreign_keys = connection.execute(
                    "PRAGMA foreign_key_list(market_factor_values)"
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(len(first.values), 6)
            self.assertEqual(len(second.values), 6)
            self.assertEqual(len(stored), 6)
            self.assertEqual(foreign_keys, [])
            self.assertEqual(
                {value.factor_name for value in stored},
                {value.factor_name for value in first.values},
            )


def _parameters():
    return MarketFactorParameters(
        scope_id="cn_a",
        universe_id="csi-test",
        universe_version="constituents_v1",
        universe_effective_date="2026-01-01",
        as_of_date="2026-01-30",
        benchmark_return=0.02,
        historical_turnover_amounts=(400, 600, 800),
    )


def _sample_rows():
    return [
        _row(
            "small-up",
            current=110,
            previous=100,
            amount=100,
            limit_state=1,
            history=(100,) * 20,
            size_bucket="small",
        ),
        _row(
            "small-down",
            current=90,
            previous=100,
            amount=200,
            limit_state=-1,
            history=(100,) * 20,
            size_bucket="small",
        ),
        _row(
            "large-up",
            current=120,
            previous=100,
            amount=300,
            history=(110,) * 20,
            size_bucket="large",
        ),
        _row(
            "large-flat",
            current=100,
            previous=100,
            amount=400,
            history=(100,) * 19,
            size_bucket="large",
        ),
        _row(
            "suspended",
            current=150,
            previous=100,
            amount=500,
            tradable=False,
            limit_state=1,
            history=(100,) * 20,
            size_bucket="small",
        ),
        _row(
            "missing",
            current=None,
            previous=100,
            amount=None,
            history=(),
            size_bucket=None,
        ),
    ]


def _row(
    symbol,
    *,
    current,
    previous,
    amount,
    tradable=True,
    limit_state=0,
    history,
    size_bucket,
):
    return MarketCrossSectionRow(
        symbol=symbol,
        trade_date="2026-01-30",
        current_close=current,
        previous_close=previous,
        amount=amount,
        tradable=tradable,
        limit_state=limit_state,
        history_closes=history,
        size_bucket=size_bucket,
    )


if __name__ == "__main__":
    unittest.main()
