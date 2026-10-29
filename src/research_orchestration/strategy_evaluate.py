"""Complete validation of an archived strategy (stage 9 slice 6): rolling out-of-sample segments, the parameter
neighbourhood and the deflated significance of the full-sample run, stored as the record the lifecycle gate reads.

Design notes: docs/modules/3-分析工具/低频回测模块.md「滚动样本外与参数邻域」.
"""

from __future__ import annotations

from typing import Any

from src.backtest.market_regimes import REGIME_VERSION, classify, state_performance
from src.backtest.strategy_validation import annual_sharpe, neighbourhood_verdict, rolling_segments
from src.strategy.templates import neighbourhood_steps

from .strategy_archive import StrategyArchiveError, StrategyArchiveService
from .strategy_backtest import DEFAULT_START, StrategyBacktestService

TRAIN_YEARS = 3.0
TEST_YEARS = 2.0
GAP_DAYS = 21


class StrategyEvaluateService:
    def __init__(self, *, backtest: StrategyBacktestService, archives: StrategyArchiveService) -> None:
        self.backtest = backtest
        self.archives = archives

    def evaluate(
        self,
        strategy_id: str,
        *,
        as_of_date: str,
        version: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        train_years: float = TRAIN_YEARS,
        test_years: float = TEST_YEARS,
        gap_days: int = GAP_DAYS,
        lots: bool = False,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        archive, _, parameters = self.backtest.resolve(strategy_id, as_of_date=as_of_date, version=version)
        start, end = start_date or DEFAULT_START, end_date or as_of_date
        gaps: list[str] = []
        base = self.backtest.prepare(
            archive, parameters, start=start, end=end, lots=lots, allow_remote=allow_remote, gaps=gaps
        )
        oos = self._rolling(base, train_years, test_years, gap_days)
        regimes = self._regimes(base, allow_remote)
        neighbourhood = self._neighbourhood(archive, parameters, base, start, end, lots, allow_remote, as_of_date)
        # The full-sample run comes last so its deflated significance counts the neighbourhood trials.
        full = self.backtest.run(
            archive.strategy_id, as_of_date=as_of_date, version=archive.version, start_date=start, end_date=end,
            lots=lots, allow_remote=allow_remote,
        )
        flags = {
            "rolling_out_of_sample": len(oos["segments"]) >= 2,
            "parameter_neighborhood": bool(neighbourhood.get("results")),
            "deflated_sharpe": "dsr" in full["significance"],
            "out_of_sample_not_worse_than_benchmark": bool(oos.get("not_worse")),
        }
        payload = {
            "strategy_id": archive.strategy_id,
            "version": archive.version,
            "name": archive.name,
            "settings": {"train_years": train_years, "test_years": test_years, "gap_days": gap_days},
            "full_sample": {
                key: full[key]
                for key in (
                    "sample", "parameters", "metrics", "benchmarks", "comparison", "capacity", "costs", "significance",
                    "selection",
                )
            },
            "rolling_oos": oos,
            "neighbourhood": neighbourhood,
            "regimes": regimes,
            "validation": flags,
            "gaps": list(dict.fromkeys([*gaps, *full["gaps"]])),
            "limitations": full["limitations"],
        }
        evaluation_id = self.backtest.repository.record_evaluation(
            archive, as_of_date=as_of_date, flags=flags, payload=payload
        )
        shown = self.archives.show(archive.strategy_id, as_of_date, archive.version)
        return {
            **payload,
            "evaluation_id": evaluation_id,
            "lifecycle_state": shown["state"],
            "state_label": shown["state_label"],
            "next_state": shown["next_state"],
            "next_blockers": shown["next_blockers"],
        }

    def _regimes(self, base: Any, allow_remote: bool) -> dict[str, Any]:
        gaps: list[str] = []
        closes, valuation = self.backtest.market_series(base.start, base.end, allow_remote=allow_remote, gaps=gaps)
        if not closes:
            return {"version": REGIME_VERSION, "error": "沪深300 行情取不到，无法划分市场状态", "gaps": gaps}
        result = base.run()
        label, signals = self.backtest.simple_benchmark(base, base.start)
        benchmark = base.run(signals)
        performance = state_performance(
            list(result.dates), result.excess_returns(), benchmark.excess_returns(), classify(closes, valuation),
            list(result.exposure),
        )
        return {**performance, "benchmark": label, "gaps": gaps}

    def _rolling(self, base: Any, train_years: float, test_years: float, gap_days: int) -> dict[str, Any]:
        calendar = sorted({bar.trade_date for bar in base.bars if base.start <= bar.trade_date <= base.end})
        segments = rolling_segments(calendar, train_years=train_years, test_years=test_years, gap_days=gap_days)
        rows, pooled_strategy, pooled_benchmark, label = [], [], [], None
        for train_end, test_start, test_end in segments:
            result = base.run(start=test_start, end=test_end)
            label, signals = self.backtest.simple_benchmark(base, test_start)
            benchmark = base.run(signals, start=test_start, end=test_end)
            strategy_returns, benchmark_returns = result.excess_returns(), benchmark.excess_returns()
            pooled_strategy += strategy_returns
            pooled_benchmark += benchmark_returns
            metrics, other = result.metrics(), benchmark.metrics()
            rows.append(
                {
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                    "annual_return": metrics.get("annual_return"),
                    "sharpe": metrics.get("sharpe"),
                    "max_drawdown": metrics.get("max_drawdown"),
                    "benchmark_annual_return": other.get("annual_return"),
                    "benchmark_sharpe": other.get("sharpe"),
                    "benchmark_max_drawdown": other.get("max_drawdown"),
                    "beat_benchmark": _beats(metrics.get("sharpe"), other.get("sharpe")),
                }
            )
        strategy_sharpe, benchmark_sharpe = annual_sharpe(pooled_strategy), annual_sharpe(pooled_benchmark)
        not_worse = (
            strategy_sharpe is not None and benchmark_sharpe is not None and strategy_sharpe >= benchmark_sharpe
        )
        wins = sum(1 for row in rows if row["beat_benchmark"])
        return {
            "benchmark": label,
            "segments": rows,
            "pooled_sharpe": strategy_sharpe,
            "pooled_benchmark_sharpe": benchmark_sharpe,
            "segments_beating_benchmark": wins,
            "not_worse": not_worse,
            "verdict": (
                "样本外段太少（少于 2 段），不能作为完整验证"
                if len(rows) < 2
                else f"样本外合并夏普 {strategy_sharpe} 对基准 {benchmark_sharpe}，{wins}/{len(rows)} 段胜出；"
                + ("不劣于简单基准" if not_worse else "劣于简单基准")
            ),
        }

    def _neighbourhood(
        self,
        archive: Any,
        parameters: dict[str, str],
        base: Any,
        start: str,
        end: str,
        lots: bool,
        allow_remote: bool,
        as_of_date: str,
    ) -> dict[str, Any]:
        variants = _variants(parameters, neighbourhood_steps(archive.strategy_id, archive.template_id))
        if not variants:
            return {"results": [], "skipped": [], "verdict": "这个策略没有可扰动的数值参数"}
        prepared, skipped = {}, []
        for name, value in variants:
            try:
                prepared[(name, value)] = self.backtest.prepare(
                    archive, {**parameters, name: value}, start=start, end=end, lots=lots,
                    allow_remote=allow_remote, gaps=[],
                )
            except (StrategyArchiveError, ValueError, KeyError) as exc:
                skipped.append(f"{name}={value}：{exc}")
        common_start = max([base.start, *(item.start for item in prepared.values())])
        base_result = base.run(start=common_start)
        base_metrics = base_result.metrics()
        results, returns = [], {}
        for (name, value), item in prepared.items():
            result = item.run(start=common_start)
            metrics = result.metrics()
            self.backtest.record_trial(
                archive, run_on=as_of_date, parameters={**parameters, name: value}, start=common_start, end=end,
                metrics=metrics,
            )
            returns[f"{name}={value}"] = result.excess_returns()
            results.append(
                {
                    "parameter": name,
                    "value": value,
                    "base_value": parameters[name],
                    "annual_return": metrics.get("annual_return"),
                    "sharpe": metrics.get("sharpe"),
                    "max_drawdown": metrics.get("max_drawdown"),
                }
            )
        verdict = neighbourhood_verdict(base_result.excess_returns(), returns)
        for row in results:
            row["z_vs_base"] = verdict.get("comparisons", {}).get(f"{row['parameter']}={row['value']}", {}).get("z_vs_base")
        verdict.pop("comparisons", None)
        return {
            "common_start": common_start,
            "base": {
                "annual_return": base_metrics.get("annual_return"),
                "sharpe": base_metrics.get("sharpe"),
                "max_drawdown": base_metrics.get("max_drawdown"),
            },
            "results": results,
            "skipped": skipped,
            **verdict,
        }


def _variants(parameters: dict[str, str], steps: dict[str, float]) -> list[tuple[str, str]]:
    """One step down and up per parameter; counts and windows stay positive, thresholds inside (0, 1) stay inside."""

    variants = []
    for name, step in steps.items():
        text = parameters.get(name)
        if text is None:
            continue
        integer = isinstance(step, int) and text.lstrip("-").isdigit()
        value = int(text) if integer else float(text)
        for candidate in (value - step, value + step):
            if integer:
                if candidate > 0 or value <= 0:
                    variants.append((name, str(candidate)))
                continue
            candidate = round(candidate, 6)
            if 0 < value < 1 and not 0 < candidate < 1:
                continue
            variants.append((name, f"{candidate:g}"))
    if "buy_below" in parameters and "sell_above" in parameters:
        variants = [
            (name, value)
            for name, value in variants
            if float(value if name == "buy_below" else parameters["buy_below"])
            < float(value if name == "sell_above" else parameters["sell_above"])
        ]
    return variants


def _beats(sharpe: float | None, benchmark: float | None) -> bool:
    if sharpe is None:
        return False
    return benchmark is None or sharpe >= benchmark
