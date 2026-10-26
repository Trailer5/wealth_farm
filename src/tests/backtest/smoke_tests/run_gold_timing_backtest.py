"""Backtest the real gold ETF short-term research rule."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        BacktestEngine,
        BacktestSample,
        BacktestSpec,
        SQLiteBacktestRepository,
        bars_from_records,
    )
    from src.benchmark_analysis import (
        BenchmarkKind,
        BenchmarkMetricCalculator,
    )
    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        DATASET_INDEX_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.factor_engineering import (
        MarketFactorCalculator,
        RiskMetricCalculator,
        RiskMetricParameters,
    )
    from src.master_data import (
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_contracts import (
        PROFILE_EXCHANGE_FUND,
        ResearchHorizon,
    )
    from src.strategy import (
        StrategyExecutor,
        StrategyInputBuilder,
        default_strategy_specs,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "backtest-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        data_access = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master_data,
            ),
        )
        gold = data_access.get(
            DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="518880",
                    name="黄金ETF",
                    asset_type_hint="ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                allow_remote=True,
                remote_sources=("mootdx", "tencent", "sina"),
                provider_timeout_seconds=30,
            )
        )
        index = master_data.register_asset(
            Asset(
                asset_id="asset:smoke-csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_a",
                exchange="sh",
            ),
            source="backtest-smoke:index",
            raw_symbol="000300",
        )
        benchmark = data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=index.asset_id),
                dataset=DATASET_INDEX_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                allow_remote=True,
                provider_timeout_seconds=60,
            )
        )
        gold_records = [dict(record) for record in gold.records]
        benchmark_records = [
            dict(record) for record in benchmark.records
        ]
        trend_spec = next(
            spec
            for spec in default_strategy_specs()
            if spec.strategy_id == "trend_timing"
        )
        strategy_results = []
        market_calculator = MarketFactorCalculator()
        risk_calculator = RiskMetricCalculator()
        benchmark_calculator = BenchmarkMetricCalculator()
        input_builder = StrategyInputBuilder()
        executor = StrategyExecutor()
        for end_index in range(6, len(gold_records) + 1):
            prefix = gold_records[:end_index]
            as_of_date = str(prefix[-1]["trade_date"])
            benchmark_prefix = [
                record
                for record in benchmark_records
                if str(record["trade_date"]) <= as_of_date
            ]
            factors = [
                *market_calculator.calculate(
                    asset_id=gold.asset.asset_id,
                    records=prefix,
                    source_dataset=gold.dataset,
                ),
                *risk_calculator.calculate(
                    asset_id=gold.asset.asset_id,
                    records=prefix,
                    source_dataset=gold.dataset,
                    parameters=RiskMetricParameters(
                        rolling_windows=(5,),
                        price_adjustment="none",
                    ),
                ).values,
                *benchmark_calculator.calculate(
                    asset_id=gold.asset.asset_id,
                    benchmark_asset_id=index.asset_id,
                    benchmark_kind=BenchmarkKind.INDEX,
                    asset_records=prefix,
                    benchmark_records=benchmark_prefix,
                    asset_dataset=gold.dataset,
                    benchmark_dataset=benchmark.dataset,
                ).values,
            ]
            strategy_input = input_builder.build(
                asset_id=gold.asset.asset_id,
                profile_id=PROFILE_EXCHANGE_FUND,
                horizon=ResearchHorizon.SHORT,
                as_of_date=as_of_date,
                factor_values=factors,
                factor_parameter_filters={
                    "max_drawdown": {"window": "input"},
                    "annualized_volatility": {
                        "window": "input"
                    },
                },
            )
            strategy_results.append(
                executor.execute(trend_spec, strategy_input)
            )
        bars = bars_from_records(gold_records)
        benchmark_bars = bars_from_records(benchmark_records)
        split_index = len(bars) // 2
        common_spec = BacktestSpec(
            run_id="backtest:gold:full",
            strategy_id=trend_spec.strategy_id,
            strategy_version=trend_spec.version,
            asset_id=gold.asset.asset_id,
            start_date=bars[0].trade_date,
            end_date=bars[-1].trade_date,
            initial_cash=Decimal("100000"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.001"),
            sample=BacktestSample.FULL,
            benchmark_asset_id=index.asset_id,
            risk_free_rate=0.015,
            price_adjustment="none",
        )
        engine = BacktestEngine()
        full = engine.run(
            common_spec,
            bars=bars,
            strategy_results=strategy_results,
            benchmark_bars=benchmark_bars,
        )
        in_bars = bars[:split_index]
        out_bars = bars[split_index:]
        in_sample = engine.run(
            replace(
                common_spec,
                run_id="backtest:gold:in",
                end_date=in_bars[-1].trade_date,
                sample=BacktestSample.IN_SAMPLE,
            ),
            bars=in_bars,
            strategy_results=[
                result
                for result in strategy_results
                if result.as_of_date <= in_bars[-1].trade_date
            ],
            benchmark_bars=benchmark_bars,
        )
        out_sample = engine.run(
            replace(
                common_spec,
                run_id="backtest:gold:out",
                start_date=out_bars[0].trade_date,
                sample=BacktestSample.OUT_OF_SAMPLE,
            ),
            bars=out_bars,
            strategy_results=[
                result
                for result in strategy_results
                if result.as_of_date >= out_bars[0].trade_date
            ],
            benchmark_bars=benchmark_bars,
        )
        repository = SQLiteBacktestRepository(db_path)
        repository.initialize()
        repository.save(full)
        if repository.get(full.spec.run_id) is None:
            raise RuntimeError("Backtest persistence failed")
        print(
            json.dumps(
                {
                    "asset_id": gold.asset.asset_id,
                    "signal_count": len(strategy_results),
                    "full": _summary(full),
                    "in_sample": _summary(in_sample),
                    "out_of_sample": _summary(out_sample),
                },
                ensure_ascii=True,
                indent=2,
                default=str,
            )
        )
    return 0


def _summary(run):
    return {
        "sample": run.spec.sample.value,
        "trade_count": len(run.trades),
        "skipped_count": len(run.skipped_orders),
        "total_return": run.metrics["total_return"],
        "benchmark_total_return": (
            run.metrics["benchmark_total_return"]
        ),
        "max_drawdown": run.metrics["max_drawdown"],
        "sharpe_ratio": run.metrics["sharpe_ratio"],
        "total_fees": run.metrics["total_fees"],
        "slippage_cost": run.metrics["slippage_cost"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
