import math
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.market_analysis import (
    A_SHARE_MARKET_CALCULATION_VERSION,
    AShareMarketFactorCalculator,
    IndustryContributionCalculator,
    IndustryMarketMetric,
    IndexValuationSeriesService,
    MarketAnalysisService,
    MarketCrossSectionRow,
    MarketFactorParameters,
    MarketSeriesObservation,
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

    def test_other_trade_dates_do_not_pollute_exclusion_counts(self):
        baseline = AShareMarketFactorCalculator().calculate(
            _sample_rows(),
            _parameters(),
        )
        with_previous_day = AShareMarketFactorCalculator().calculate(
            (
                *_sample_rows(),
                replace(
                    _sample_rows()[0],
                    trade_date="2026-01-29",
                ),
            ),
            _parameters(),
        )

        self.assertEqual(
            [
                item.parameters["excluded_missing"]
                for item in baseline.values
            ],
            [
                item.parameters["excluded_missing"]
                for item in with_previous_day.values
            ],
        )

    def test_industry_contribution_uses_prior_day_weights(self):
        prior = (
            _industry("A", "行业A", "2026-08-06", "0", "60"),
            _industry("B", "行业B", "2026-08-06", "0", "40"),
        )
        current = (
            _industry("A", "行业A", "2026-08-07", "0.10", "70"),
            _industry("B", "行业B", "2026-08-07", "-0.05", "30"),
        )

        result = IndustryContributionCalculator().calculate(
            prior,
            current,
        )

        self.assertEqual(result[0].prior_weight, Decimal("0.6"))
        self.assertEqual(result[0].contribution, Decimal("0.060"))
        self.assertEqual(result[1].contribution, Decimal("-0.020"))
        self.assertEqual(
            sum(
                (item.contribution for item in result),
                Decimal("0"),
            ),
            Decimal("0.040"),
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
            observation = MarketSeriesObservation(
                scope_asset_id="index:cn_a:sh:000300",
                series_name="ttm_pe",
                observation_date="2026-08-07",
                available_at="2026-08-08",
                value=Decimal("13.71"),
                unit="multiple",
                methodology="market_cap_weighted_ttm",
                source="akshare:stock_index_pe_lg",
                acquired_at="2026-08-09T00:00:00+00:00",
                raw={"index_name": "沪深300"},
            )
            self.assertEqual(
                service.store_series_observations(
                    [observation, observation]
                ),
                2,
            )
            self.assertEqual(
                repository.list_series_observations(
                    observation.scope_asset_id,
                    observation.series_name,
                    available_through="2026-08-07",
                ),
                [],
            )
            self.assertEqual(
                repository.list_series_observations(
                    observation.scope_asset_id,
                    observation.series_name,
                    available_through="2026-08-08",
                ),
                [observation],
            )
            valuation_service = IndexValuationSeriesService(
                repository
            )
            stored_valuations = valuation_service.store(
                scope_asset_id="index:cn_a:sh:000300",
                records=[
                    {
                        "trade_date": "2026-08-07",
                        "index_name": "沪深300",
                        "ttm_pe": 13.71,
                        "static_pe": 13.99,
                        "pb": 1.44,
                        "source": "akshare:index_valuation",
                    }
                ],
                acquired_at="2026-08-09T00:00:00+00:00",
            )
            self.assertEqual(len(stored_valuations), 5)
            pe_methods = {
                item.methodology
                for item in repository.list_series_observations(
                    "index:cn_a:sh:000300",
                    "pe",
                )
            }
            self.assertEqual(
                pe_methods,
                {
                    "market_cap_weighted_static",
                    "market_cap_weighted_ttm",
                },
            )
            official = valuation_service.store(
                scope_asset_id="index:cn_a:sh:000300",
                records=[
                    {
                        "trade_date": "2026-07-14",
                        "index_name": "沪深300",
                        "pe1": 14.89,
                        "pe2": 17.77,
                        "dividend_yield1_percent": 2.63,
                        "dividend_yield2_percent": 2.19,
                        "source": "akshare:csindex",
                    }
                ],
                acquired_at="2026-08-09T00:00:00+00:00",
            )
            self.assertEqual(len(official), 6)
            dividend_values = repository.list_series_observations(
                "index:cn_a:sh:000300",
                "dividend_yield",
            )
            self.assertEqual(
                {item.value for item in dividend_values},
                {Decimal("0.0263"), Decimal("0.0219")},
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


def _industry(code, name, trade_date, return_ratio, market_cap):
    return IndustryMarketMetric(
        industry_code=code,
        industry_name=name,
        trade_date=trade_date,
        return_ratio=Decimal(return_ratio),
        float_market_cap=Decimal(market_cap),
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
