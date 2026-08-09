import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from src.backtest.market_timing_event_study import (
    MARKET_TIMING_EVENT_STUDY_VERSION,
    MarketTimingEventStudyEngine,
    MarketTimingEventStudyParameters,
)
from src.strategy.sse_market_timing import (
    SSE_MARKET_TIMING_LIMITATIONS,
    HorizonTimingSignal,
    SSEIndexBar,
    SSEIndexSeries,
    SSEMarketTimingResult,
)


class MarketTimingEventStudyEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = MarketTimingEventStudyEngine()
        self.parameters = MarketTimingEventStudyParameters(
            short_forward_sessions=1,
            medium_short_forward_sessions=2,
            medium_long_forward_sessions=3,
            short_cooldown_sessions=1,
            medium_short_cooldown_sessions=1,
            medium_long_cooldown_sessions=1,
            min_confidence=Decimal("0.60"),
        )

    def test_entry_uses_effective_date_close_not_signal_date_close(self):
        series = _series("50", "100", "120")
        result = _result(
            "2026-01-01",
            "2026-01-02",
            short=_signal("short", "low", "confirmed", "0.80"),
        )

        run = self.engine.run(series, [result], self.parameters)

        self.assertEqual(len(run.events), 1)
        event = run.events[0]
        self.assertEqual(event.signal_date, "2026-01-01")
        self.assertEqual(event.effective_date, "2026-01-02")
        self.assertEqual(event.entry_close, Decimal("100"))
        self.assertEqual(event.exit_close, Decimal("120"))
        self.assertEqual(event.forward_return, Decimal("0.2"))

    def test_directions_excursions_and_horizon_windows_are_independent(self):
        series = _series("50", "100", "110", "90", "120")
        result = _result(
            "2026-01-01",
            "2026-01-02",
            short=_signal("short", "low", "confirmed", "0.80"),
            medium_short=_signal(
                "medium_short",
                "high",
                "confirmed",
                "0.90",
            ),
            medium_long=_signal(
                "medium_long",
                "low",
                "confirmed",
                "0.70",
            ),
        )

        run = self.engine.run(series, [result], self.parameters)
        events = {event.horizon: event for event in run.events}

        short = events["short"]
        self.assertEqual(short.evaluation_date, "2026-01-03")
        self.assertEqual(short.signed_forward_return, Decimal("0.1"))
        self.assertEqual(short.max_favorable_excursion, Decimal("0.1"))
        self.assertEqual(short.max_adverse_excursion, Decimal("0"))

        medium_short = events["medium_short"]
        self.assertEqual(medium_short.evaluation_date, "2026-01-04")
        self.assertEqual(medium_short.forward_return, Decimal("-0.1"))
        self.assertEqual(
            medium_short.signed_forward_return,
            Decimal("0.1"),
        )
        self.assertEqual(
            medium_short.max_favorable_excursion,
            Decimal("0.1"),
        )
        self.assertEqual(
            medium_short.max_adverse_excursion,
            Decimal("-0.1"),
        )

        medium_long = events["medium_long"]
        self.assertEqual(medium_long.evaluation_date, "2026-01-05")
        self.assertEqual(medium_long.signed_forward_return, Decimal("0.2"))
        self.assertEqual(
            medium_long.max_favorable_excursion,
            Decimal("0.2"),
        )
        self.assertEqual(
            medium_long.max_adverse_excursion,
            Decimal("-0.1"),
        )
        self.assertEqual(run.metrics["event_count"], 3)
        self.assertEqual(
            run.metrics["medium_short:high"]["hit_rate"],
            Decimal("1"),
        )

    def test_same_horizon_direction_cluster_obeys_cooldown(self):
        series = _series("100", "101", "102", "103", "104", "105")
        results = [
            _result(
                "2026-01-01",
                "2026-01-02",
                short=_signal("short", "low", "confirmed", "0.80"),
            ),
            _result(
                "2026-01-02",
                "2026-01-03",
                short=_signal("short", "low", "confirmed", "0.80"),
            ),
            _result(
                "2026-01-04",
                "2026-01-05",
                short=_signal("short", "low", "confirmed", "0.80"),
            ),
        ]

        run = self.engine.run(
            series,
            results,
            replace(self.parameters, short_cooldown_sessions=2),
        )

        self.assertEqual(
            [event.effective_date for event in run.events],
            ["2026-01-02", "2026-01-05"],
        )
        self.assertEqual(len(run.skipped), 1)
        self.assertEqual(run.skipped[0].reason, "cooldown")

    def test_future_shortage_is_skipped_while_ineligible_signals_are_excluded(
        self,
    ):
        series = _series("100", "101", "102", "103")
        filtered = _result(
            "2026-01-01",
            "2026-01-02",
            short=_signal("short", "neutral", "neutral", "0.90"),
            medium_short=_signal(
                "medium_short",
                "low",
                "watch",
                "0.90",
            ),
            medium_long=_signal(
                "medium_long",
                "high",
                "confirmed",
                "0.50",
            ),
        )
        insufficient_future = _result(
            "2026-01-02",
            "2026-01-03",
            short=_signal("short", "low", "confirmed", "0.80"),
        )

        run = self.engine.run(
            series,
            [filtered, insufficient_future],
            replace(self.parameters, short_forward_sessions=2),
        )

        self.assertEqual(run.events, ())
        self.assertEqual(len(run.skipped), 1)
        self.assertEqual(
            run.skipped[0].reason,
            "insufficient_forward_sessions",
        )
        self.assertEqual(run.metrics["event_count"], 0)
        self.assertEqual(run.metrics["skipped_count"], 1)

    def test_duplicate_effective_date_horizon_is_rejected(self):
        series = _series("100", "101", "102")
        result = _result(
            "2026-01-01",
            "2026-01-02",
            short=_signal("short", "low", "confirmed", "0.80"),
        )

        with self.assertRaisesRegex(
            ValueError,
            "duplicate effective_date/horizon",
        ):
            self.engine.run(series, [result, result], self.parameters)

    def test_parameter_and_event_contracts_are_strict(self):
        defaults = MarketTimingEventStudyParameters()
        self.assertEqual(defaults.short_forward_sessions, 10)
        self.assertEqual(defaults.medium_short_forward_sessions, 60)
        self.assertEqual(defaults.medium_long_forward_sessions, 252)
        self.assertEqual(defaults.short_cooldown_sessions, 5)
        self.assertEqual(defaults.medium_short_cooldown_sessions, 20)
        self.assertEqual(defaults.medium_long_cooldown_sessions, 60)
        self.assertEqual(defaults.min_confidence, Decimal("0.60"))

        with self.assertRaisesRegex(ValueError, "positive integer"):
            replace(defaults, short_forward_sessions=0)
        with self.assertRaisesRegex(ValueError, "finite Decimal"):
            replace(defaults, min_confidence=0.6)

        run = self.engine.run(
            _series("50", "100", "120"),
            [
                _result(
                    "2026-01-01",
                    "2026-01-02",
                    short=_signal(
                        "short",
                        "low",
                        "confirmed",
                        "0.80",
                    ),
                )
            ],
            self.parameters,
        )
        event = run.events[0]
        with self.assertRaisesRegex(ValueError, "direction must be low or high"):
            replace(event, direction="neutral")
        with self.assertRaisesRegex(ValueError, "dates must satisfy"):
            replace(event, evaluation_date=event.effective_date)
        with self.assertRaisesRegex(ValueError, "asset_id does not match"):
            self.engine.run(
                replace(
                    _series("50", "100", "120"),
                    asset_id="index:cn_a:sh:000300",
                ),
                [
                    _result(
                        "2026-01-01",
                        "2026-01-02",
                        short=_signal(
                            "short",
                            "low",
                            "confirmed",
                            "0.80",
                        ),
                    )
                ],
                self.parameters,
            )
        self.assertEqual(run.version, MARKET_TIMING_EVENT_STUDY_VERSION)
        self.assertEqual(
            run.metrics["version"],
            MARKET_TIMING_EVENT_STUDY_VERSION,
        )


def _series(*closes: str) -> SSEIndexSeries:
    start = date(2026, 1, 1)
    bars = tuple(
        SSEIndexBar(
            trade_date=(start + timedelta(days=index)).isoformat(),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
        )
        for index, close in enumerate(closes)
    )
    return SSEIndexSeries(asset_id="index:cn_a:sh:000001", bars=bars)


def _result(
    as_of_date: str,
    effective_date: str,
    *,
    short: HorizonTimingSignal | None = None,
    medium_short: HorizonTimingSignal | None = None,
    medium_long: HorizonTimingSignal | None = None,
) -> SSEMarketTimingResult:
    return SSEMarketTimingResult(
        as_of_date=as_of_date,
        effective_date=effective_date,
        signals=(
            short or _signal("short", "neutral", "neutral", "0"),
            medium_short
            or _signal("medium_short", "neutral", "neutral", "0"),
            medium_long
            or _signal("medium_long", "neutral", "neutral", "0"),
        ),
        conflicts=(),
        limitations=SSE_MARKET_TIMING_LIMITATIONS,
    )


def _signal(
    horizon: str,
    direction: str,
    stage: str,
    confidence: str,
) -> HorizonTimingSignal:
    return HorizonTimingSignal(
        horizon=horizon,
        direction=direction,
        stage=stage,
        confidence=Decimal(confidence),
        evidence=(),
        missing_inputs=(),
        metrics={},
    )


if __name__ == "__main__":
    unittest.main()
