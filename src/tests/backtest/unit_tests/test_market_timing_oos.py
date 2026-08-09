import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from src.backtest.market_timing_oos import (
    MARKET_TIMING_OOS_SEED,
    MARKET_TIMING_OOS_VERSION,
    MarketTimingOOSEngine,
    MarketTimingOOSParameters,
)
from src.backtest.market_timing_validation import (
    MarketTimingHorizonValidation,
    MarketTimingObservation,
    MarketTimingQuantileMetrics,
    MarketTimingWalkForwardFold,
)


ASSET_ID = "index:cn_a:sh:000001"
SAMPLE_COUNT = 2016


class MarketTimingOOSEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observations = _observations(SAMPLE_COUNT)
        cls.validation = _validation(cls.observations)
        cls.oos_run = MarketTimingOOSEngine.run(ASSET_ID, cls.validation)

    def test_final_252_observations_never_enter_walk_forward_folds(self):
        self.assertEqual(len(self.oos_run.folds), 2)
        self.assertEqual(
            [fold.test_count for fold in self.oos_run.folds],
            [252, 252],
        )
        self.assertEqual(
            self.oos_run.folds[-1].test_end_date,
            self.observations[-253].effective_date,
        )
        self.assertEqual(
            self.oos_run.holdout.test_start_date,
            self.observations[-252].effective_date,
        )
        self.assertLess(
            self.oos_run.folds[-1].test_end_date,
            self.oos_run.holdout.test_start_date,
        )

    def test_purge_removes_overlapping_labels_before_each_test(self):
        first = self.oos_run.folds[0]

        self.assertEqual(first.train_count, 1008)
        self.assertEqual(first.purged_count, 252)
        self.assertEqual(
            first.train_end_date,
            self.observations[1007].effective_date,
        )
        self.assertLess(
            self.observations[1007].evaluation_date,
            first.test_start_date,
        )
        self.assertGreaterEqual(
            self.observations[1008].evaluation_date,
            first.test_start_date,
        )

    def test_training_window_expands_and_tests_advance_by_252(self):
        first, second = self.oos_run.folds

        self.assertEqual(first.train_start_date, second.train_start_date)
        self.assertEqual(
            [first.train_count, second.train_count],
            [1008, 1260],
        )
        self.assertEqual(
            [first.purged_count, second.purged_count],
            [252, 252],
        )
        self.assertEqual(
            second.test_start_date,
            self.observations[1512].effective_date,
        )
        self.assertEqual(
            second.test_end_date,
            self.observations[1763].effective_date,
        )

    def test_fold_boundaries_come_only_from_its_purged_training_scores(self):
        changed = list(self.observations)
        for index in range(1260, 1512):
            score = changed[index].directional_score + Decimal("1000")
            changed[index] = replace(
                changed[index],
                low_position_score=score,
                directional_score=score,
            )

        rerun = MarketTimingOOSEngine.run(
            ASSET_ID,
            _validation(tuple(changed)),
        )

        self.assertEqual(
            self.oos_run.folds[0].quantile_boundaries,
            rerun.folds[0].quantile_boundaries,
        )
        self.assertEqual(
            self.oos_run.folds[0].quantile_boundaries,
            (Decimal("20"), Decimal("40"), Decimal("60"), Decimal("80")),
        )
        self.assertNotEqual(
            [item.count for item in self.oos_run.folds[0].quantiles],
            [item.count for item in rerun.folds[0].quantiles],
        )

    def test_quantiles_and_bootstrap_are_test_only_and_reproducible(self):
        first = self.oos_run.folds[0]
        repeated = MarketTimingOOSEngine.run(ASSET_ID, self.validation)

        self.assertEqual(sum(item.count for item in first.quantiles), 252)
        self.assertTrue(all(item.count > 0 for item in first.quantiles))
        self.assertTrue(
            all(
                item.mean_forward_return is not None
                and item.median_forward_return is not None
                and item.hit_rate is not None
                for item in first.quantiles
            )
        )
        self.assertEqual(
            first.q5_minus_q1,
            first.quantiles[4].mean_forward_return
            - first.quantiles[0].mean_forward_return,
        )
        self.assertIsNotNone(first.spearman_correlation)
        self.assertIsNotNone(first.bootstrap_ci)
        self.assertEqual(first.bootstrap_ci, repeated.folds[0].bootstrap_ci)

    def test_holdout_is_independent_and_marked_final_not_for_tuning(self):
        changed = list(self.observations)
        for index in range(SAMPLE_COUNT - 252, SAMPLE_COUNT):
            score = changed[index].directional_score + Decimal("1000")
            changed[index] = replace(
                changed[index],
                low_position_score=score,
                directional_score=score,
            )

        rerun = MarketTimingOOSEngine.run(
            ASSET_ID,
            _validation(tuple(changed)),
        )

        self.assertEqual(self.oos_run.folds, rerun.folds)
        self.assertEqual(self.oos_run.holdout.role, "holdout")
        self.assertEqual(
            self.oos_run.holdout_scope,
            "retrospective",
        )
        self.assertTrue(self.oos_run.holdout.evaluated_once)
        self.assertIsNone(self.oos_run.holdout.fold)
        self.assertEqual(self.oos_run.holdout.train_count, 1512)
        self.assertEqual(self.oos_run.holdout.purged_count, 252)
        self.assertEqual(
            self.oos_run.holdout.quantile_boundaries,
            rerun.holdout.quantile_boundaries,
        )
        self.assertNotEqual(
            self.oos_run.holdout.quantiles,
            rerun.holdout.quantiles,
        )

    def test_parameters_and_version_are_strictly_frozen_at_v1(self):
        parameters = MarketTimingOOSParameters()

        self.assertEqual(parameters.min_train_observations, 1008)
        self.assertEqual(parameters.test_observations, 252)
        self.assertEqual(parameters.step_observations, 252)
        self.assertEqual(parameters.holdout_observations, 252)
        self.assertEqual(parameters.quantile_count, 5)
        self.assertEqual(parameters.bootstrap_iterations, 1000)
        self.assertEqual(parameters.block_length, 20)
        self.assertEqual(parameters.seed, MARKET_TIMING_OOS_SEED)
        self.assertEqual(self.oos_run.version, MARKET_TIMING_OOS_VERSION)
        self.assertFalse(hasattr(self.oos_run, "status"))
        with self.assertRaisesRegex(ValueError, "frozen"):
            replace(parameters, min_train_observations=1007)
        with self.assertRaisesRegex(ValueError, "frozen"):
            replace(parameters, bootstrap_iterations=999)
        with self.assertRaisesRegex(TypeError, "integer"):
            replace(parameters, seed=True)

    def test_insufficient_data_wrong_horizon_and_identity_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "walk-forward fold"):
            MarketTimingOOSEngine.run(
                ASSET_ID,
                _validation(self.observations[:1763]),
            )
        with self.assertRaisesRegex(ValueError, "medium_long"):
            MarketTimingOOSEngine.run(
                ASSET_ID,
                replace(
                    self.validation,
                    horizon="medium_short",
                    forward_sessions=60,
                    observations=(),
                    count=0,
                    quantiles=_empty_quantiles(),
                    walk_forward_folds=_empty_folds(),
                ),
            )
        with self.assertRaisesRegex(ValueError, "exact identity"):
            MarketTimingOOSEngine.run(f" {ASSET_ID}", self.validation)


def _observations(count: int) -> tuple[MarketTimingObservation, ...]:
    output = []
    for index in range(count):
        score = Decimal(index % 101)
        forward_return = (score - Decimal("50")) / Decimal("10000")
        entry_close = Decimal("100")
        output.append(
            MarketTimingObservation(
                signal_date=_day(index),
                effective_date=_day(index + 1),
                evaluation_date=_day(index + 253),
                horizon="medium_long",
                low_position_score=score,
                high_position_score=Decimal("0"),
                directional_score=score,
                entry_close=entry_close,
                exit_close=entry_close * (ONE + forward_return),
                forward_return=forward_return,
            )
        )
    return tuple(output)


def _validation(
    observations: tuple[MarketTimingObservation, ...],
) -> MarketTimingHorizonValidation:
    count = len(observations)
    quantiles = _empty_quantiles()
    if observations:
        returns = tuple(item.forward_return for item in observations)
        quantiles = (
            MarketTimingQuantileMetrics(
                quantile=1,
                count=count,
                score_min=min(item.directional_score for item in observations),
                score_max=max(item.directional_score for item in observations),
                mean_forward_return=sum(returns, Decimal("0"))
                / Decimal(count),
                median_forward_return=_median(returns),
                hit_rate=Decimal(sum(value > 0 for value in returns))
                / Decimal(count),
            ),
            *_empty_quantiles()[1:],
        )
    fold_counts = _group_counts(count, 4)
    offset = 0
    folds = []
    for fold, fold_count in enumerate(fold_counts, start=1):
        folds.append(
            MarketTimingWalkForwardFold(
                fold=fold,
                start_date=(
                    observations[offset].effective_date
                    if fold_count
                    else None
                ),
                end_date=(
                    observations[offset + fold_count - 1].effective_date
                    if fold_count
                    else None
                ),
                count=fold_count,
                spearman_correlation=None,
            )
        )
        offset += fold_count
    return MarketTimingHorizonValidation(
        horizon="medium_long",
        forward_sessions=252,
        count=count,
        observations=observations,
        spearman_correlation=None,
        quantiles=quantiles,
        bootstrap_ci=None,
        walk_forward_folds=tuple(folds),
    )


def _empty_quantiles() -> tuple[MarketTimingQuantileMetrics, ...]:
    return tuple(
        MarketTimingQuantileMetrics(
            quantile=index,
            count=0,
            score_min=None,
            score_max=None,
            mean_forward_return=None,
            median_forward_return=None,
            hit_rate=None,
        )
        for index in range(1, 6)
    )


def _empty_folds() -> tuple[MarketTimingWalkForwardFold, ...]:
    return tuple(
        MarketTimingWalkForwardFold(
            fold=index,
            start_date=None,
            end_date=None,
            count=0,
            spearman_correlation=None,
        )
        for index in range(1, 5)
    )


def _group_counts(count: int, groups: int) -> tuple[int, ...]:
    quotient, remainder = divmod(count, groups)
    return tuple(
        quotient + (1 if index < remainder else 0)
        for index in range(groups)
    )


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _day(index: int) -> str:
    return (date(2010, 1, 1) + timedelta(days=index)).isoformat()


ONE = Decimal("1")


if __name__ == "__main__":
    unittest.main()
