import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from src.backtest.market_timing_validation import (
    MARKET_TIMING_VALIDATION_SEED,
    MARKET_TIMING_VALIDATION_VERSION,
    MarketTimingValidationEngine,
    MarketTimingValidationParameters,
)
from src.strategy.sse_market_timing import (
    SSE_MARKET_TIMING_LIMITATIONS,
    HorizonTimingSignal,
    SSEIndexBar,
    SSEIndexSeries,
    SSEMarketTimingResult,
)


class MarketTimingValidationEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = MarketTimingValidationEngine()

    def test_continuous_scores_are_layered_into_equal_count_quantiles(self):
        closes = ["100"] * 21
        for index in range(10):
            closes[index + 11] = str(101 + index)
        series = _series(closes)
        results = [
            _result(
                index,
                short=_signal(
                    "short",
                    str(Decimal(index + 1) / Decimal("10")),
                    "0",
                    stage="neutral",
                    direction="neutral",
                ),
            )
            for index in range(10)
        ]

        run = self.engine.run(series, results)

        validation = run.short
        self.assertEqual(validation.count, 10)
        self.assertEqual(
            [item.count for item in validation.quantiles],
            [2, 2, 2, 2, 2],
        )
        self.assertEqual(
            validation.quantiles[0].score_min,
            Decimal("0.1"),
        )
        self.assertEqual(
            validation.quantiles[0].score_max,
            Decimal("0.2"),
        )
        self.assertEqual(
            validation.quantiles[0].mean_forward_return,
            Decimal("0.015"),
        )
        self.assertEqual(
            validation.quantiles[0].median_forward_return,
            Decimal("0.015"),
        )
        self.assertEqual(validation.quantiles[0].hit_rate, Decimal("1"))
        self.assertEqual(
            validation.quantiles[-1].mean_forward_return,
            Decimal("0.095"),
        )
        self.assertEqual(validation.spearman_correlation, Decimal("1"))
        self.assertEqual(
            validation.observations[0].directional_score,
            Decimal("0.1"),
        )
        self.assertEqual(
            validation.observations[0].evaluation_date,
            _day(11),
        )

    def test_future_shortage_is_skipped_without_a_sample_threshold(self):
        series = _series(["100"] * 12)
        result = _result(
            5,
            short=_signal("short", "0.8", "0.1"),
        )

        run = self.engine.run(series, [result])

        self.assertEqual(run.short.count, 0)
        short_skipped = [
            item for item in run.skipped if item.horizon == "short"
        ]
        self.assertEqual(len(short_skipped), 1)
        self.assertEqual(
            short_skipped[0].reason,
            "incomplete_forward_window",
        )
        self.assertIsNone(run.short.spearman_correlation)
        self.assertIsNone(run.short.bootstrap_ci)

    def test_ties_use_average_ranks_and_deterministic_date_order(self):
        closes = ["100"] * 15
        closes[11:15] = ["110", "120", "130", "140"]
        series = _series(closes)
        scores = ("0", "0", "1", "1")
        results = [
            _result(
                index,
                short=_signal("short", score, "0"),
            )
            for index, score in enumerate(scores)
        ]

        run = self.engine.run(series, results)

        correlation = run.short.spearman_correlation
        self.assertIsNotNone(correlation)
        assert correlation is not None
        self.assertAlmostEqual(float(correlation * correlation), 0.8)
        self.assertEqual(
            run.short.quantiles[0].mean_forward_return,
            Decimal("0.1"),
        )
        self.assertEqual(
            run.short.quantiles[1].mean_forward_return,
            Decimal("0.2"),
        )

    def test_bootstrap_is_reproducible_with_the_frozen_seed(self):
        returns = (
            "0.02",
            "-0.01",
            "0.04",
            "0.01",
            "0.03",
            "-0.02",
            "0.05",
            "0.00",
        ) * 3
        series, results = _spaced_short_sample(returns)

        first = self.engine.run(series, results)
        second = self.engine.run(series, tuple(reversed(results)))

        self.assertIsNotNone(first.short.bootstrap_ci)
        self.assertEqual(first.short.bootstrap_ci, second.short.bootstrap_ci)

    def test_walk_forward_uses_four_chronological_test_segments(self):
        series, results = _spaced_short_sample(
            ("0.01", "0.02", "0.01", "0.02", "0.01", "0.02", "0.01", "0.02")
        )

        run = self.engine.run(series, results)

        folds = run.short.walk_forward_folds
        self.assertEqual([fold.count for fold in folds], [2, 2, 2, 2])
        self.assertEqual(
            [fold.spearman_correlation for fold in folds],
            [Decimal("1")] * 4,
        )
        self.assertEqual(folds[0].start_date, _day(1))
        self.assertEqual(folds[0].end_date, _day(12))
        self.assertEqual(folds[-1].start_date, _day(67))
        self.assertEqual(folds[-1].end_date, _day(78))

    def test_sparse_two_observation_sample_is_still_reported(self):
        series, results = _spaced_short_sample(("0.01", "0.02"))

        run = self.engine.run(series, results)

        self.assertEqual(run.short.count, 2)
        self.assertEqual(run.short.spearman_correlation, Decimal("1"))
        self.assertEqual(run.short.bootstrap_ci, (Decimal("1"), Decimal("1")))
        self.assertEqual(
            [item.count for item in run.short.quantiles],
            [1, 1, 0, 0, 0],
        )
        self.assertEqual(
            [item.count for item in run.short.walk_forward_folds],
            [1, 1, 0, 0],
        )
        self.assertFalse(hasattr(run, "status"))

    def test_singleton_and_constant_scores_have_no_correlation_or_ci(self):
        singleton_series, singleton_results = _spaced_short_sample(("0.01",))
        singleton = self.engine.run(singleton_series, singleton_results)
        self.assertEqual(singleton.short.count, 1)
        self.assertIsNone(singleton.short.spearman_correlation)
        self.assertIsNone(singleton.short.bootstrap_ci)

        series, results = _spaced_short_sample(
            ("0.01", "0.02", "0.03"),
            scores=("0.5", "0.5", "0.5"),
        )
        constant = self.engine.run(series, results)
        self.assertEqual(constant.short.count, 3)
        self.assertIsNone(constant.short.spearman_correlation)
        self.assertIsNone(constant.short.bootstrap_ci)

    def test_asset_mismatch_and_duplicate_results_are_rejected(self):
        series, results = _spaced_short_sample(("0.01",))
        mismatch = replace(
            results[0],
            asset_id="index:cn_a:sh:000300",
        )

        with self.assertRaisesRegex(ValueError, "asset_id does not match"):
            self.engine.run(series, [mismatch])
        with self.assertRaisesRegex(ValueError, "duplicate result"):
            self.engine.run(series, [results[0], results[0]])

    def test_parameters_and_version_are_strictly_frozen_at_v1(self):
        parameters = MarketTimingValidationParameters()

        self.assertEqual(parameters.quantile_count, 5)
        self.assertEqual(parameters.short_forward_sessions, 10)
        self.assertEqual(parameters.medium_short_forward_sessions, 60)
        self.assertEqual(parameters.medium_long_forward_sessions, 252)
        self.assertEqual(parameters.bootstrap_iterations, 1000)
        self.assertEqual(parameters.block_length, 20)
        self.assertEqual(parameters.walk_forward_folds, 4)
        self.assertEqual(parameters.seed, MARKET_TIMING_VALIDATION_SEED)
        with self.assertRaisesRegex(ValueError, "frozen"):
            replace(parameters, quantile_count=4)
        with self.assertRaisesRegex(TypeError, "integer"):
            replace(parameters, seed=True)

        series, results = _spaced_short_sample(("0.01",))
        run = self.engine.run(series, results, parameters)
        self.assertEqual(run.version, MARKET_TIMING_VALIDATION_VERSION)
        self.assertEqual(set(run.horizons), {
            "short",
            "medium_short",
            "medium_long",
        })


def _spaced_short_sample(
    forward_returns: tuple[str, ...],
    *,
    scores: tuple[str, ...] | None = None,
) -> tuple[SSEIndexSeries, list[SSEMarketTimingResult]]:
    if scores is None:
        scores = tuple(str(index + 1) for index in range(len(forward_returns)))
    if len(scores) != len(forward_returns):
        raise ValueError("scores and returns must have the same length")
    last_evaluation_index = 11 * (len(forward_returns) - 1) + 11
    closes = ["100"] * (last_evaluation_index + 1)
    results = []
    for index, (score, forward_return) in enumerate(
        zip(scores, forward_returns)
    ):
        effective_index = 11 * index + 1
        evaluation_index = effective_index + 10
        closes[evaluation_index] = str(
            Decimal("100") * (Decimal("1") + Decimal(forward_return))
        )
        results.append(
            _result(
                effective_index - 1,
                effective_index=effective_index,
                short=_signal("short", score, "0"),
            )
        )
    return _series(closes), results


def _series(closes: list[str]) -> SSEIndexSeries:
    bars = tuple(
        SSEIndexBar(
            trade_date=_day(index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
        )
        for index, close in enumerate(closes)
    )
    return SSEIndexSeries(
        asset_id="index:cn_a:sh:000001",
        bars=bars,
    )


def _result(
    as_of_index: int,
    *,
    effective_index: int | None = None,
    short: HorizonTimingSignal | None = None,
    medium_short: HorizonTimingSignal | None = None,
    medium_long: HorizonTimingSignal | None = None,
) -> SSEMarketTimingResult:
    effective_index = (
        as_of_index + 1 if effective_index is None else effective_index
    )
    return SSEMarketTimingResult(
        as_of_date=_day(as_of_index),
        effective_date=_day(effective_index),
        signals=(
            short or _signal("short", None, None),
            medium_short or _signal("medium_short", None, None),
            medium_long or _signal("medium_long", None, None),
        ),
        conflicts=(),
        limitations=SSE_MARKET_TIMING_LIMITATIONS,
    )


def _signal(
    horizon: str,
    low_score: str | None,
    high_score: str | None,
    *,
    stage: str = "watch",
    direction: str = "low",
) -> HorizonTimingSignal:
    metrics = {
        "low_position_score": (
            Decimal(low_score) if low_score is not None else None
        ),
        "high_position_score": (
            Decimal(high_score) if high_score is not None else None
        ),
    }
    if low_score is None or high_score is None:
        stage = "insufficient"
        direction = "insufficient"
    return HorizonTimingSignal(
        horizon=horizon,
        direction=direction,
        stage=stage,
        confidence=Decimal("0"),
        evidence=(),
        missing_inputs=(),
        metrics=metrics,
    )


def _day(index: int) -> str:
    return (date(2026, 1, 1) + timedelta(days=index)).isoformat()


if __name__ == "__main__":
    unittest.main()
