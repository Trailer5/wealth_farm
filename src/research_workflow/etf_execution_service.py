"""Formal ETF research execution over public service boundaries."""

from __future__ import annotations

from decimal import Decimal

from src.benchmark_analysis import (
    BenchmarkAnalysisService,
    BenchmarkKind,
)
from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_quality import DataQualityService
from src.data_src.service import DataFetchService
from src.factor_engineering import (
    CommoditySensitivityService,
    FactorService,
)
from src.fund_research import (
    ETFMarketSnapshot,
    ExchangeFundMetricService,
    official_etf_expense_schedule,
)
from src.master_data import ASSET_TYPE_INDEX, MasterDataService
from src.research_visualization import (
    ChartRenderer,
    ChartType,
    build_factor_series_spec,
    build_price_volume_spec,
)
from src.strategy import (
    StrategyExecutor,
    StrategyInputBuilder,
    StrategyRegistry,
    StrategyRunStatus,
)

from .execution_models import (
    ResearchExecutionGap,
    ResearchExecutionOptions,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)
from .execution_service import (
    _complete_phases,
    _derive_windows,
    _error,
    _status,
)
from .models import ResearchHorizon, ResearchTaskSpec
from .profiles import PROFILE_EXCHANGE_FUND


class ETFResearchExecutionService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        data_fetch: DataFetchService,
        quality_service: DataQualityService,
        factor_service: FactorService,
        etf_metric_service: ExchangeFundMetricService,
        commodity_service: CommoditySensitivityService,
        benchmark_service: BenchmarkAnalysisService,
        strategy_registry: StrategyRegistry,
        chart_renderer: ChartRenderer,
        strategy_builder: StrategyInputBuilder | None = None,
        strategy_executor: StrategyExecutor | None = None,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.data_fetch = data_fetch
        self.quality_service = quality_service
        self.factor_service = factor_service
        self.etf_metric_service = etf_metric_service
        self.commodity_service = commodity_service
        self.benchmark_service = benchmark_service
        self.strategy_registry = strategy_registry
        self.chart_renderer = chart_renderer
        self.strategy_builder = (
            strategy_builder or StrategyInputBuilder()
        )
        self.strategy_executor = (
            strategy_executor or StrategyExecutor()
        )

    def execute(
        self,
        task: ResearchTaskSpec,
        *,
        options: ResearchExecutionOptions | None = None,
    ) -> ResearchExecutionResult:
        config = options or ResearchExecutionOptions()
        asset, strategy = self._validate(task)
        windows = _derive_windows(task, strategy.data_window_days)
        phases = [
            ResearchPhaseResult(
                "task",
                ResearchPhaseStatus.SUCCESS,
                "ETF ResearchTaskSpec validated",
                sources=("research_workflow",),
            )
        ]
        gaps: list[ResearchExecutionGap] = []
        try:
            bars = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=task.asset_id),
                    dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                    start_date=windows.bar_start,
                    end_date=windows.bar_end,
                    as_of_date=task.as_of_date,
                    allow_remote=config.allow_remote,
                    remote_sources=config.bar_remote_sources,
                    provider_timeout_seconds=(
                        config.provider_timeout_seconds
                    ),
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(bars.records)} ETF daily bars",
                    sources=bars.sources,
                )
            )
        except Exception as exc:
            return self._blocked(
                task,
                windows,
                phases,
                gaps,
                "data_access",
                "daily_bars_unavailable",
                exc,
            )

        quality = self.quality_service.evaluate_data_access_response(
            task_name=f"etf-research-execution:{task.task_id}",
            response=bars,
            expected_start=windows.bar_start,
            expected_end=windows.bar_end,
        )
        phases.append(
            ResearchPhaseResult(
                "quality",
                (
                    ResearchPhaseStatus.SUCCESS
                    if quality.can_publish
                    else ResearchPhaseStatus.GAP
                ),
                f"can_publish={quality.can_publish}",
                sources=("data_quality",),
            )
        )
        if not quality.can_publish:
            gaps.append(
                ResearchExecutionGap(
                    "data_quality",
                    "daily_bar_quality_gate",
                    "ETF bars did not pass the quality gate",
                    True,
                )
            )

        expense = None
        factor_values = []
        risk = None
        try:
            factor_values.extend(
                self.factor_service.compute_from_response(bars)
            )
            risk = self.factor_service.compute_risk_from_response(
                bars,
                parameters=config.risk_parameters,
            )
            factor_values.extend(risk.values)
            spot_rows = self.data_fetch.fetch_exchange_fund_spot(
                "akshare",
                symbols=[asset.standard_symbol],
            )
            row = next(
                item
                for item in spot_rows
                if str(item.get("代码") or item.get("symbol"))
                == asset.standard_symbol
            )
            market_date = str(
                row.get("数据日期") or task.as_of_date
            )[:10]
            expense = official_etf_expense_schedule(
                asset_id=asset.asset_id,
                standard_symbol=asset.standard_symbol,
            )
            metric_set = (
                self.etf_metric_service.calculate_and_store(
                    asset_id=asset.asset_id,
                    as_of_date=task.as_of_date,
                    market=ETFMarketSnapshot(
                        asset_id=asset.asset_id,
                        as_of_date=market_date,
                        market_price=Decimal(str(row["最新价"])),
                        iopv=Decimal(str(row["IOPV实时估值"])),
                        source="akshare:fund_etf_spot_em",
                        raw=dict(row),
                    ),
                    expenses=expense,
                )
            )
            factor_values.extend(metric_set.values)
            if task.horizon is ResearchHorizon.LONG:
                gold = [
                    record
                    for record in (
                        self.data_fetch.fetch_gold_spot_history()
                    )
                    if windows.bar_start
                    <= str(record.get("date"))[:10]
                    <= windows.bar_end
                ]
                factor_values.append(
                    self.commodity_service.calculate_and_store(
                        asset_id=asset.asset_id,
                        asset_records=[
                            dict(record) for record in bars.records
                        ],
                        benchmarks={"sge:Au99.99": gold},
                        weights={"sge:Au99.99": 1.0},
                        as_of_date=task.as_of_date,
                    )
                )
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(factor_values)} ETF factor values",
                    sources=(
                        "factor_engineering",
                        "akshare:fund_etf_spot_em",
                        expense.source,
                    ),
                    metric_versions=tuple(
                        sorted(
                            {
                                value.calculation_version
                                for value in factor_values
                            }
                        )
                    ),
                )
            )
        except Exception as exc:
            return self._blocked(
                task,
                windows,
                phases,
                gaps,
                "fund_research",
                "etf_metric_calculation_failed",
                exc,
                bar_response=bars,
                quality_summary=quality,
            )

        phases.append(
            ResearchPhaseResult(
                "fundamental",
                ResearchPhaseStatus.NOT_APPLICABLE,
                "Company fundamental analysis is not applicable to ETF",
            )
        )

        benchmark = None
        try:
            benchmark_asset = self.master_data.get_asset(
                task.benchmark_asset_id
            )
            if (
                benchmark_asset is None
                or benchmark_asset.asset_type != ASSET_TYPE_INDEX
            ):
                raise ValueError("ETF benchmark must be an index")
            self.benchmark_service.set_benchmark(
                asset_id=asset.asset_id,
                benchmark_asset_id=benchmark_asset.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source="etf_research_execution",
                effective_date=windows.bar_start,
                reason="ResearchTaskSpec benchmark",
            )
            benchmark = self.benchmark_service.compare(
                asset_id=asset.asset_id,
                start_date=windows.bar_start,
                end_date=windows.bar_end,
                as_of_date=task.as_of_date,
                benchmark_asset_id=benchmark_asset.asset_id,
                allow_remote=config.allow_remote,
                provider_timeout_seconds=(
                    config.provider_timeout_seconds
                ),
            )
            factor_values.extend(benchmark.values)
            phases.append(
                ResearchPhaseResult(
                    "benchmark",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(benchmark.aligned.dates)} aligned dates",
                    sources=("benchmark_analysis",),
                )
            )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "benchmark_analysis",
                    "benchmark_comparison_failed",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "benchmark",
                    ResearchPhaseStatus.FAILED,
                    "ETF benchmark comparison failed",
                    error=error,
                )
            )

        phases.append(
            ResearchPhaseResult(
                "evidence",
                ResearchPhaseStatus.SUCCESS,
                "ETF market and official expense evidence recorded",
                sources=(
                    "akshare:fund_etf_spot_em",
                    expense.source,
                    *(
                        ("akshare:spot_hist_sge",)
                        if task.horizon is ResearchHorizon.LONG
                        else ()
                    ),
                ),
            )
        )

        artifacts = ()
        try:
            specs = [
                build_price_volume_spec(
                    task=task,
                    asset=asset,
                    records=[dict(item) for item in bars.records],
                    source_dataset=bars.dataset,
                    price_adjustment=(
                        config.risk_parameters.price_adjustment
                    ),
                ),
                build_factor_series_spec(
                    task=task,
                    asset=asset,
                    chart_type=ChartType.CUMULATIVE_RETURN,
                    values=list(risk.series),
                    factor_names=("cumulative_return",),
                    source_dataset="risk_daily_v1",
                ),
                build_factor_series_spec(
                    task=task,
                    asset=asset,
                    chart_type=ChartType.DRAWDOWN,
                    values=list(risk.series),
                    factor_names=("drawdown_series",),
                    source_dataset="risk_daily_v1",
                ),
                build_factor_series_spec(
                    task=task,
                    asset=asset,
                    chart_type=ChartType.ROLLING_RISK,
                    values=list(risk.series),
                    factor_names=(
                        "rolling_annualized_volatility_5d",
                        "rolling_sharpe_5d",
                    ),
                    source_dataset="risk_daily_v1",
                ),
            ]
            artifacts = tuple(
                self.chart_renderer.render(spec) for spec in specs
            )
            phases.append(
                ResearchPhaseResult(
                    "charts",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(artifacts)} ETF chart artifacts",
                    sources=("research_visualization",),
                    artifacts=tuple(
                        f"{item.chart_type.value}:{item.input_digest}"
                        for item in artifacts
                    ),
                )
            )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "research_visualization",
                    "chart_render_failed",
                    error,
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "charts",
                    ResearchPhaseStatus.GAP,
                    "ETF chart rendering incomplete",
                    error=error,
                )
            )

        strategy_result = None
        try:
            strategy_input = self.strategy_builder.build(
                asset_id=asset.asset_id,
                profile_id=task.profile_id,
                horizon=task.horizon,
                as_of_date=task.as_of_date,
                factor_values=factor_values,
                factor_parameter_filters={
                    "max_drawdown": {"window": "input"},
                    "annualized_volatility": {"window": "input"},
                },
            )
            strategy_result = self.strategy_executor.execute(
                strategy,
                strategy_input,
            )
            if (
                strategy_result.status
                is StrategyRunStatus.INSUFFICIENT_DATA
            ):
                gaps.append(
                    ResearchExecutionGap(
                        "strategy",
                        "strategy_input_incomplete",
                        str(strategy_result.missing_metrics),
                        True,
                    )
                )
                phase_status = ResearchPhaseStatus.GAP
            else:
                phase_status = ResearchPhaseStatus.SUCCESS
            phases.append(
                ResearchPhaseResult(
                    "strategy",
                    phase_status,
                    (
                        f"{strategy.strategy_id}:"
                        f"{strategy_result.status.value}"
                    ),
                    sources=("strategy",),
                )
            )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "strategy",
                    "strategy_execution_failed",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "strategy",
                    ResearchPhaseStatus.FAILED,
                    "ETF strategy execution failed",
                    error=error,
                )
            )

        phases = _complete_phases(phases)
        return ResearchExecutionResult(
            task=task,
            windows=windows,
            status=_status(phases, gaps),
            phases=tuple(phases),
            gaps=tuple(
                sorted(gaps, key=lambda item: (item.module, item.code))
            ),
            bar_response=bars,
            quality_summary=quality,
            factor_values=tuple(factor_values),
            risk_result=risk,
            benchmark_result=benchmark,
            chart_artifacts=artifacts,
            strategy_result=strategy_result,
        )

    def _validate(self, task):
        if task.profile_id != PROFILE_EXCHANGE_FUND:
            raise ValueError(
                "ETFResearchExecutionService requires exchange_fund"
            )
        if task.horizon not in {
            ResearchHorizon.MEDIUM_LONG,
            ResearchHorizon.LONG,
        }:
            raise ValueError(
                "ETF execution supports medium_long or long"
            )
        if task.benchmark_asset_id is None:
            raise ValueError("ETF task requires benchmark_asset_id")
        asset = self.master_data.get_asset(task.asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {task.asset_id}")
        compatible = [
            spec
            for spec in self.strategy_registry.list_specs()
            if task.horizon in spec.horizons
            and task.profile_id in spec.supported_profiles
        ]
        if not compatible:
            raise ValueError(
                "No ETF strategy supports task horizon"
            )
        compatible.sort(
            key=lambda spec: (
                len(spec.supported_profiles),
                spec.strategy_id,
            )
        )
        return asset, compatible[0]

    def _blocked(
        self,
        task,
        windows,
        phases,
        gaps,
        module,
        code,
        exc,
        **kwargs,
    ):
        error = _error(exc)
        gaps.append(
            ResearchExecutionGap(module, code, error, True)
        )
        phases.append(
            ResearchPhaseResult(
                (
                    "bars"
                    if code == "daily_bars_unavailable"
                    else "factors"
                ),
                ResearchPhaseStatus.FAILED,
                code,
                error=error,
            )
        )
        return ResearchExecutionResult(
            task=task,
            windows=windows,
            status=ResearchExecutionStatus.BLOCKED,
            phases=tuple(_complete_phases(phases)),
            gaps=tuple(gaps),
            **kwargs,
        )
