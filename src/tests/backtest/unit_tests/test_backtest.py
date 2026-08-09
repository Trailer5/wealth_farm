import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.backtest import (
    BacktestBar,
    BacktestEngine,
    BacktestSample,
    BacktestSpec,
    SQLiteBacktestRepository,
    TradeSide,
)
from src.research_workflow import ResearchHorizon
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
)


class BacktestEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine()
        self.spec = BacktestSpec(
            run_id="backtest:test",
            strategy_id="trend_timing",
            strategy_version="v1",
            asset_id="asset:test",
            start_date="2026-01-01",
            end_date="2026-01-04",
            initial_cash=Decimal("1000"),
            fee_rate=Decimal("0.01"),
            slippage_rate=Decimal("0.01"),
            sample=BacktestSample.IN_SAMPLE,
            benchmark_asset_id="asset:benchmark",
            annualization_days=4,
            price_adjustment="none",
        )
        self.bars = [
            BacktestBar(
                f"2026-01-0{index}",
                Decimal(str(open_price)),
                Decimal(str(close_price)),
            )
            for index, open_price, close_price in (
                (1, 10, 10),
                (2, 10, 11),
                (3, 12, 12),
                (4, 11, 11),
            )
        ]
        self.benchmark = [
            BacktestBar(
                f"2026-01-0{index}",
                Decimal(str(price)),
                Decimal(str(price)),
            )
            for index, price in (
                (1, 100),
                (2, 101),
                (4, 103),
            )
        ]

    def test_signals_execute_next_session_with_costs(self):
        run = self.engine.run(
            self.spec,
            bars=self.bars,
            strategy_results=[
                _result("2026-01-01", StrategyRunStatus.PASS),
                _result("2026-01-03", StrategyRunStatus.FAIL),
            ],
            benchmark_bars=self.benchmark,
        )

        self.assertEqual(len(run.trades), 2)
        buy, sell = run.trades
        self.assertEqual(buy.trade_date, "2026-01-02")
        self.assertEqual(buy.side, TradeSide.BUY)
        self.assertEqual(buy.execution_price, Decimal("10.10"))
        self.assertEqual(sell.trade_date, "2026-01-04")
        self.assertEqual(sell.side, TradeSide.SELL)
        self.assertEqual(sell.execution_price, Decimal("10.89"))
        self.assertGreater(run.metrics["total_fees"], 0)
        self.assertGreater(run.metrics["slippage_cost"], 0)
        self.assertEqual(run.metrics["sample"], "in_sample")
        self.assertEqual(run.metrics["trade_count"], 2)
        self.assertTrue(
            all(
                trade.trade_date > trade.signal_date
                for trade in run.trades
            )
        )

    def test_zero_signal_delay_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "signal_delay_sessions must be at least 1",
        ):
            replace(self.spec, signal_delay_sessions=0)

    def test_fees_and_slippage_reduce_final_equity(self):
        results = [
            _result("2026-01-01", StrategyRunStatus.PASS),
            _result("2026-01-03", StrategyRunStatus.FAIL),
        ]
        costly = self.engine.run(
            self.spec,
            bars=self.bars,
            strategy_results=results,
        )
        free = self.engine.run(
            replace(
                self.spec,
                run_id="backtest:free",
                fee_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
            ),
            bars=self.bars,
            strategy_results=results,
        )

        self.assertLess(
            costly.metrics["final_equity"],
            free.metrics["final_equity"],
        )

    def test_untradable_session_postpones_execution(self):
        bars = list(self.bars)
        bars[1] = replace(bars[1], tradable=False)
        run = self.engine.run(
            self.spec,
            bars=bars,
            strategy_results=[
                _result("2026-01-01", StrategyRunStatus.PASS)
            ],
        )

        self.assertEqual(run.trades[0].trade_date, "2026-01-03")

    def test_newer_signal_supersedes_blocked_order(self):
        bars = list(self.bars)
        bars[1] = replace(bars[1], limit_up=True)
        run = self.engine.run(
            self.spec,
            bars=bars,
            strategy_results=[
                _result("2026-01-01", StrategyRunStatus.PASS),
                _result("2026-01-02", StrategyRunStatus.FAIL),
            ],
        )

        self.assertEqual(run.trades, ())
        self.assertEqual(
            run.skipped_orders[0].reason,
            "superseded_by_newer_signal",
        )

    def test_insufficient_signal_does_not_change_position(self):
        run = self.engine.run(
            self.spec,
            bars=self.bars,
            strategy_results=[
                _result(
                    "2026-01-01",
                    StrategyRunStatus.INSUFFICIENT_DATA,
                )
            ],
        )

        self.assertEqual(run.trades, ())
        self.assertEqual(
            run.skipped_orders[0].reason,
            "insufficient_strategy_data",
        )
        self.assertEqual(
            run.metrics["final_equity"],
            Decimal("1000"),
        )

    def test_result_without_market_date_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "has no market bar",
        ):
            self.engine.run(
                self.spec,
                bars=[
                    bar
                    for bar in self.bars
                    if bar.trade_date != "2026-01-03"
                ],
                strategy_results=[
                    _result(
                        "2026-01-03",
                        StrategyRunStatus.PASS,
                    )
                ],
            )

    def test_tradable_result_requires_input_provenance(self):
        result = replace(
            _result("2026-01-01", StrategyRunStatus.PASS),
            input_effective_dates={},
        )
        with self.assertRaisesRegex(
            ValueError,
            "requires input provenance",
        ):
            self.engine.run(
                self.spec,
                bars=self.bars,
                strategy_results=[result],
            )

    def test_future_input_date_is_rejected(self):
        result = replace(
            _result("2026-01-01", StrategyRunStatus.PASS),
            input_effective_dates={"roe": "2026-01-02"},
        )
        with self.assertRaisesRegex(
            ValueError,
            "future input dates",
        ):
            self.engine.run(
                self.spec,
                bars=self.bars,
                strategy_results=[result],
            )

    def test_benchmark_uses_only_available_dates(self):
        run = self.engine.run(
            self.spec,
            bars=self.bars,
            strategy_results=[],
            benchmark_bars=self.benchmark,
        )

        self.assertIsNone(run.equity_curve[2].benchmark_value)
        self.assertEqual(
            run.metrics["benchmark_total_return"],
            Decimal("0.03"),
        )

    def test_out_of_sample_label_is_preserved(self):
        run = self.engine.run(
            replace(
                self.spec,
                run_id="backtest:oos",
                sample=BacktestSample.OUT_OF_SAMPLE,
            ),
            bars=self.bars,
            strategy_results=[],
        )

        self.assertEqual(
            run.metrics["sample"],
            "out_of_sample",
        )


class BacktestRepositoryTest(unittest.TestCase):
    def test_run_round_trips_with_trades_and_metrics(self):
        engine = BacktestEngine()
        spec = BacktestSpec(
            run_id="backtest:persist",
            strategy_id="trend_timing",
            strategy_version="v1",
            asset_id="asset:test",
            start_date="2026-01-01",
            end_date="2026-01-03",
            initial_cash=Decimal("1000"),
            fee_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            sample=BacktestSample.FULL,
        )
        bars = [
            BacktestBar(
                f"2026-01-0{index}",
                Decimal(str(price)),
                Decimal(str(price)),
            )
            for index, price in ((1, 10), (2, 11), (3, 12))
        ]
        run = engine.run(
            spec,
            bars=bars,
            strategy_results=[
                _result("2026-01-01", StrategyRunStatus.PASS)
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteBacktestRepository(
                Path(temp_dir) / "backtest.sqlite3"
            )
            repository.initialize()
            repository.save(run)
            restored = repository.get(spec.run_id)

        self.assertEqual(restored.spec, run.spec)
        self.assertEqual(restored.trades, run.trades)
        self.assertEqual(
            restored.equity_curve,
            run.equity_curve,
        )
        self.assertEqual(
            restored.metrics["total_return"],
            run.metrics["total_return"],
        )


def _result(trade_date, status):
    return StrategyResult(
        strategy_id="trend_timing",
        strategy_version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        asset_id="asset:test",
        horizon=ResearchHorizon.SHORT,
        as_of_date=trade_date,
        valid_until="2026-01-31",
        status=status,
        signal=status.value,
        score=Decimal("1") if status is StrategyRunStatus.PASS else None,
        confidence=Decimal("1"),
        input_completeness=Decimal("1"),
        matched_conditions=(),
        failed_conditions=(),
        vetoed_conditions=(),
        missing_metrics=(),
        missing_evidence_types=(),
        metric_versions={},
        counterevidence=(),
        limitations=("Not backtested",),
        input_effective_dates={"signal_metric": trade_date},
    )


if __name__ == "__main__":
    unittest.main()
