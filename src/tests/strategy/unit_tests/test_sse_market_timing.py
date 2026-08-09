import unittest
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal

from src.strategy.sse_market_timing import (
    SSE_MARKET_TIMING_LIMITATIONS,
    SSE_MARKET_TIMING_STRATEGY_ID,
    HorizonTimingSignal,
    SSEIndexBar,
    SSEIndexSeries,
    SSEMarketAuxPoint,
    SSEMarketAuxSeries,
    SSEMarketTimingParameters,
    SSEMarketTimingResult,
    SSEMarketTimingStrategy,
)


class SSEMarketTimingStrategyTest(unittest.TestCase):
    def setUp(self):
        self.strategy = SSEMarketTimingStrategy()
        self.parameters = _parameters()

    def test_short_high_and_low_regions_are_confirmed(self):
        low = self.strategy.evaluate(
            _series(("100", "90", "80", "70", "60", "50", "40", "30", "35")),
            _qvix_points(9, high=True),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )
        high = self.strategy.evaluate(
            _series(("30", "40", "50", "60", "70", "80", "90", "100", "95")),
            _qvix_points(9, high=False),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )

        low_signal = _signal(low, "short")
        high_signal = _signal(high, "short")
        self.assertEqual((low_signal.direction, low_signal.stage), ("low", "confirmed"))
        self.assertEqual(
            (high_signal.direction, high_signal.stage),
            ("high", "confirmed"),
        )
        self.assertGreaterEqual(low_signal.confidence, Decimal("0.60"))
        self.assertGreaterEqual(high_signal.confidence, Decimal("0.60"))

    def test_medium_short_high_and_low_regions_are_confirmed(self):
        parameters = _parameters(medium_sma=6)
        low = self.strategy.evaluate(
            _series(("120", "110", "100", "100", "100", "50", "40", "60", "60")),
            _breadth_points(9, Decimal("0.10")),
            "2026-01-09",
            "2026-01-10",
            parameters,
        )
        high = self.strategy.evaluate(
            _series(("40", "45", "50", "50", "50", "100", "110", "90", "90")),
            _breadth_points(9, Decimal("0.90")),
            "2026-01-09",
            "2026-01-10",
            parameters,
        )

        self.assertEqual(
            (_signal(low, "medium_short").direction, _signal(low, "medium_short").stage),
            ("low", "confirmed"),
        )
        self.assertEqual(
            (
                _signal(high, "medium_short").direction,
                _signal(high, "medium_short").stage,
            ),
            ("high", "confirmed"),
        )

    def test_medium_long_high_and_low_regions_are_confirmed(self):
        low = self.strategy.evaluate(
            _series(("100", "90", "80", "70", "60", "50", "40", "30", "55")),
            _valuation_points(low=True),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )
        high = self.strategy.evaluate(
            _series(("50", "60", "70", "80", "90", "100", "110", "120", "95")),
            _valuation_points(low=False),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )

        low_signal = _signal(low, "medium_long")
        high_signal = _signal(high, "medium_long")
        self.assertEqual((low_signal.direction, low_signal.stage), ("low", "confirmed"))
        self.assertEqual(
            (high_signal.direction, high_signal.stage),
            ("high", "confirmed"),
        )
        self.assertIn("trend_above_sma", low_signal.metrics)
        self.assertTrue(any("long-trend SMA" in item for item in low_signal.evidence))

    def test_required_history_and_stale_aux_inputs_are_explicit(self):
        too_short = self.strategy.evaluate(
            _series(("100", "99", "98")),
            (),
            "2026-01-03",
            "2026-01-04",
            self.parameters,
        )
        stale_points = (
            SSEMarketAuxPoint(
                trade_date="2026-01-01",
                market_pe=Decimal("10"),
                ten_year_yield_percent=Decimal("2"),
                qvix_close=Decimal("30"),
                above_ma20_ratio=Decimal("0.2"),
            ),
        )
        stale = self.strategy.evaluate(
            _series(("100", "90", "80", "70", "60", "50", "40", "30", "35")),
            stale_points,
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )

        self.assertTrue(
            all(signal.stage == "insufficient" for signal in too_short.signals)
        )
        self.assertIn("qvix_close", _signal(stale, "short").missing_inputs)
        self.assertIn(
            "above_ma20_ratio",
            _signal(stale, "medium_short").missing_inputs,
        )
        self.assertEqual(_signal(stale, "medium_long").stage, "insufficient")
        self.assertIn("market_pe", _signal(stale, "medium_long").missing_inputs)

    def test_future_bars_and_auxiliary_points_are_completely_ignored(self):
        observed = _series(
            ("100", "90", "80", "70", "60", "50", "40", "30", "35")
        )
        future = SSEIndexSeries(
            asset_id=observed.asset_id,
            bars=observed.bars
            + (
                _bar("2026-01-10", "10000"),
                _bar("2026-01-11", "2"),
            ),
        )
        points = _valuation_points(low=True)
        baseline = self.strategy.evaluate(
            observed,
            points,
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )
        with_future = self.strategy.evaluate(
            future,
            SSEMarketAuxSeries(
                points
                + (
                    SSEMarketAuxPoint(
                        "2026-01-10",
                        market_pe=Decimal("999"),
                        qvix_close=Decimal("1"),
                    ),
                )
            ),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )

        self.assertEqual(with_future, baseline)

    def test_equal_high_low_scores_stay_neutral(self):
        result = self.strategy.evaluate(
            _series(("100",) * 9),
            tuple(
                SSEMarketAuxPoint(
                    _day(index),
                    market_pe=Decimal("10"),
                    qvix_close=Decimal("20"),
                    above_ma20_ratio=Decimal("0.5"),
                )
                for index in range(9)
            ),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )

        self.assertTrue(
            all(
                signal.direction == "neutral" and signal.stage == "neutral"
                for signal in result.signals
            )
        )

    def test_only_confirmed_cross_horizon_disagreement_is_a_conflict(self):
        parameters = _parameters(
            short_channel=3,
            rsi_window=2,
            medium_sma=6,
        )
        result = self.strategy.evaluate(
            _series(("220", "210", "200", "200", "200", "200", "50", "100", "90")),
            tuple(
                SSEMarketAuxPoint(
                    _day(index),
                    qvix_close=Decimal(20 - index),
                    above_ma20_ratio=Decimal("0.1"),
                )
                for index in range(9)
            ),
            "2026-01-09",
            "2026-01-10",
            parameters,
        )

        self.assertEqual(
            (_signal(result, "short").direction, _signal(result, "short").stage),
            ("high", "confirmed"),
        )
        self.assertEqual(
            (
                _signal(result, "medium_short").direction,
                _signal(result, "medium_short").stage,
            ),
            ("low", "confirmed"),
        )
        self.assertEqual(len(result.conflicts), 1)
        self.assertIn("short:high", result.conflicts[0])
        self.assertIn("medium_short:low", result.conflicts[0])

    def test_contracts_are_frozen_validated_and_effective_next_session(self):
        bar = _bar("2026-01-01", "10")
        with self.assertRaises(FrozenInstanceError):
            bar.close = Decimal("11")
        with self.assertRaisesRegex(ValueError, "between low and high"):
            SSEIndexBar(
                "2026-01-01",
                Decimal("11"),
                Decimal("10"),
                Decimal("9"),
                Decimal("10"),
            )
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            SSEIndexSeries("000001.SH", (bar, bar))
        with self.assertRaisesRegex(ValueError, "at least one"):
            SSEMarketAuxPoint("2026-01-01")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            SSEMarketTimingParameters(short_channel=0)
        with self.assertRaisesRegex(ValueError, "later than"):
            self.strategy.evaluate(
                _series(("10",) * 9),
                (),
                "2026-01-09",
                "2026-01-09",
                self.parameters,
            )

        result = self.strategy.evaluate(
            _series(("10",) * 9),
            (),
            "2026-01-09",
            "2026-01-10",
            self.parameters,
        )
        self.assertEqual(result.strategy_id, SSE_MARKET_TIMING_STRATEGY_ID)
        self.assertEqual(result.asset_id, "index:cn_a:sh:000001")
        self.assertEqual(result.version, "v1")
        self.assertEqual(result.maturity, "research_rule")
        self.assertEqual(result.limitations, SSE_MARKET_TIMING_LIMITATIONS)
        self.assertEqual(
            {signal.horizon for signal in result.signals},
            {"short", "medium_short", "medium_long"},
        )

        with self.assertRaisesRegex(ValueError, "each horizon exactly once"):
            SSEMarketTimingResult(
                "2026-01-09",
                "2026-01-10",
                (
                    HorizonTimingSignal(
                        "short", "neutral", "neutral", Decimal("0"), (), (), {}
                    ),
                )
                * 3,
                (),
                SSE_MARKET_TIMING_LIMITATIONS,
            )


def _parameters(**overrides) -> SSEMarketTimingParameters:
    values = {
        "short_channel": 4,
        "rsi_window": 3,
        "short_reversal_window": 1,
        "qvix_window": 4,
        "medium_window": 6,
        "medium_sma": 3,
        "medium_reversal_window": 2,
        "long_window": 8,
        "long_trend_sma": 4,
        "long_confirmation_sma": 3,
        "long_drawdown_window": 8,
        "valuation_lookback_days": 365,
        "min_valuation_observations": 8,
        "max_valuation_staleness_days": 2,
        "max_aux_staleness_days": 2,
    }
    values.update(overrides)
    return SSEMarketTimingParameters(**values)


def _series(closes: tuple[str, ...]) -> SSEIndexSeries:
    return SSEIndexSeries(
        asset_id="index:cn_a:sh:000001",
        bars=tuple(_bar(_day(index), value) for index, value in enumerate(closes)),
    )


def _bar(trade_date: str, close: str) -> SSEIndexBar:
    value = Decimal(close)
    return SSEIndexBar(
        trade_date=trade_date,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
    )


def _day(index: int) -> str:
    return (date(2026, 1, 1) + timedelta(days=index)).isoformat()


def _qvix_points(count: int, *, high: bool) -> tuple[SSEMarketAuxPoint, ...]:
    values = range(10, 10 + count) if high else range(30, 30 - count, -1)
    return tuple(
        SSEMarketAuxPoint(_day(index), qvix_close=Decimal(value))
        for index, value in enumerate(values)
    )


def _breadth_points(
    count: int,
    latest: Decimal,
) -> tuple[SSEMarketAuxPoint, ...]:
    return tuple(
        SSEMarketAuxPoint(_day(index), above_ma20_ratio=latest)
        for index in range(count)
    )


def _valuation_points(*, low: bool) -> tuple[SSEMarketAuxPoint, ...]:
    values = range(20, 11, -1) if low else range(12, 21)
    return tuple(
        SSEMarketAuxPoint(
            _day(index),
            market_pe=Decimal(value),
            ten_year_yield_percent=Decimal("3"),
        )
        for index, value in enumerate(values)
    )


def _signal(
    result: SSEMarketTimingResult,
    horizon: str,
) -> HorizonTimingSignal:
    return next(signal for signal in result.signals if signal.horizon == horizon)


if __name__ == "__main__":
    unittest.main()
