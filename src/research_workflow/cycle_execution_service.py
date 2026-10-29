"""Formal long-horizon cyclical-resource research execution."""

from __future__ import annotations

from decimal import Decimal

from src.benchmark_analysis import (
    BenchmarkAnalysisService,
    BenchmarkKind,
)
from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
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
from src.fundamental_analysis import DividendHistoryService, FundamentalAnalysisService, ValuationModelService
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    MasterDataService,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    ResearchHorizon,
    ResearchTaskSpec,
)
from src.research_evidence import ResearchEvidenceService
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
from src.valuation_position import ValuationPositionService

from .execution_models import (
    CycleResearchExecutionOptions,
    ResearchExecutionGap,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)
from .execution_service import (
    _complete_phases,
    _derive_windows,
    _error,
    _rolling_risk_factor_names,
    _status,
)
from .industry_context import collect_industry_context
from .valuation_context import collect_dividends, collect_valuation_model
from .return_bars import fetch_total_return_bars, risk_parameters_for


class CyclicalResourceResearchExecutionService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        data_fetch: DataFetchService,
        quality_service: DataQualityService,
        factor_service: FactorService,
        fundamental_service: FundamentalAnalysisService,
        commodity_service: CommoditySensitivityService,
        benchmark_service: BenchmarkAnalysisService,
        evidence_service: ResearchEvidenceService,
        strategy_registry: StrategyRegistry,
        chart_renderer: ChartRenderer,
        strategy_builder: StrategyInputBuilder | None = None,
        strategy_executor: StrategyExecutor | None = None,
        valuation_position: ValuationPositionService | None = None,
        valuation_models: ValuationModelService | None = None,
        dividends: DividendHistoryService | None = None,
    ) -> None:
        self.dividends = dividends
        self.master_data = master_data
        self.data_access = data_access
        self.data_fetch = data_fetch
        self.quality_service = quality_service
        self.factor_service = factor_service
        self.fundamental_service = fundamental_service
        self.commodity_service = commodity_service
        self.benchmark_service = benchmark_service
        self.evidence_service = evidence_service
        self.strategy_registry = strategy_registry
        self.chart_renderer = chart_renderer
        self.valuation_position = valuation_position
        self.valuation_models = valuation_models
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
        options: CycleResearchExecutionOptions,
    ) -> ResearchExecutionResult:
        asset, strategy = self._validate(task)
        windows = _derive_windows(task, strategy.data_window_days)
        phases = [
            ResearchPhaseResult(
                "task",
                ResearchPhaseStatus.SUCCESS,
                "Cyclical-resource task validated",
                sources=("research_workflow",),
            )
        ]
        gaps: list[ResearchExecutionGap] = []
        try:
            bars = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset.asset_id),
                    dataset=DATASET_STOCK_DAILY_BARS,
                    start_date=windows.bar_start,
                    end_date=windows.bar_end,
                    as_of_date=task.as_of_date,
                    allow_remote=options.allow_remote,
                    remote_sources=options.bar_remote_sources,
                    provider_timeout_seconds=(
                        options.provider_timeout_seconds
                    ),
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(bars.records)} resource-stock bars",
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
            task_name=f"cycle-research-execution:{task.task_id}",
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
                    "Resource-stock bars failed quality gate",
                    True,
                )
            )

        factor_values = []
        risk = None
        try:
            return_bars, basis_gap = fetch_total_return_bars(
                self.data_access,
                bars,
                start_date=windows.bar_start,
                end_date=windows.bar_end,
                as_of_date=task.as_of_date,
                options=options,
            )
            if basis_gap is not None:
                gaps.append(basis_gap)
            factor_values.extend(
                self.factor_service.compute_from_response(return_bars)
            )
            risk = self.factor_service.compute_risk_from_response(
                return_bars,
                parameters=risk_parameters_for(
                    return_bars,
                    options.risk_parameters,
                ),
            )
            factor_values.extend(risk.values)
            weights = dict(options.commodity_weights)
            benchmarks = {}
            if "sina:CU0" in weights:
                benchmarks["sina:CU0"] = [
                    row
                    for row in (
                        self.data_fetch
                        .fetch_copper_continuous_history()
                    )
                    if windows.bar_start
                    <= str(row.get("date"))[:10]
                    <= windows.bar_end
                ]
            if "sge:Au99.99" in weights:
                benchmarks["sge:Au99.99"] = [
                    row
                    for row in self.data_fetch.fetch_gold_spot_history()
                    if windows.bar_start
                    <= str(row.get("date"))[:10]
                    <= windows.bar_end
                ]
            factor_values.append(
                self.commodity_service.calculate_and_store(
                    asset_id=asset.asset_id,
                    asset_records=[
                        dict(record) for record in return_bars.records
                    ],
                    benchmarks=benchmarks,
                    weights=weights,
                    as_of_date=task.as_of_date,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(factor_values)} cycle factor values",
                    sources=(
                        "factor_engineering",
                        *tuple(sorted(weights)),
                    ),
                    metric_versions=tuple(
                        sorted(
                            {
                                item.calculation_version
                                for item in factor_values
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
                "factor_engineering",
                "commodity_factor_calculation_failed",
                exc,
                bar_response=bars,
                quality_summary=quality,
            )

        fundamental = None
        dividends = None
        try:
            financials = self.data_access.get_financial_bundle(
                asset_id=asset.asset_id,
                start_date=windows.financial_start,
                end_date=windows.financial_end,
                as_of_date=task.as_of_date,
                allow_remote=options.allow_remote,
                provider_timeout_seconds=(
                    options.provider_timeout_seconds
                ),
            )
            prices = [
                record
                for record in bars.records
                if str(record.get("trade_date")) <= task.as_of_date
            ]
            price = Decimal(str(prices[-1]["close"]))
            dividends, dividend_gaps = collect_dividends(
                self.dividends,
                asset=asset,
                as_of_date=task.as_of_date,
                price=price,
                config=options,
            )
            gaps.extend(dividend_gaps)
            fundamental = self.fundamental_service.analyze(
                financials,
                market_price=price,
                dividend_per_share_ttm=(
                    None if dividends is None else Decimal(dividends["ttm_per_share"])
                ),
            )
            phases.append(
                ResearchPhaseResult(
                    "fundamental",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(fundamental.metrics)} company metrics",
                    sources=financials.sources,
                    metric_versions=tuple(
                        sorted(
                            {
                                item.calculation_version
                                for item in fundamental.metrics
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
                "fundamental_analysis",
                "cycle_fundamental_analysis_failed",
                exc,
                bar_response=bars,
                quality_summary=quality,
                factor_values=tuple(factor_values),
                risk_result=risk,
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
                raise ValueError(
                    "Cyclical-resource benchmark must be an index"
                )
            self.benchmark_service.set_benchmark(
                asset_id=asset.asset_id,
                benchmark_asset_id=benchmark_asset.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source="cycle_research_execution",
                effective_date=windows.bar_start,
                reason="ResearchTaskSpec benchmark",
            )
            benchmark = self.benchmark_service.compare(
                asset_id=asset.asset_id,
                start_date=windows.bar_start,
                end_date=windows.bar_end,
                as_of_date=task.as_of_date,
                benchmark_asset_id=benchmark_asset.asset_id,
                allow_remote=options.allow_remote,
                provider_timeout_seconds=(
                    options.provider_timeout_seconds
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
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "benchmark",
                    ResearchPhaseStatus.GAP,
                    "Cycle benchmark comparison unavailable",
                    error=error,
                )
            )

        evidence = None
        try:
            evidence = self.evidence_service.get_bundle(
                asset_id=asset.asset_id,
                start_date=windows.evidence_start,
                end_date=windows.evidence_end,
                as_of_date=task.as_of_date,
                allow_remote=options.allow_remote,
                disclosure_sources=options.disclosure_sources,
                research_sources=options.research_sources,
                provider_timeout_seconds=(
                    options.provider_timeout_seconds
                ),
            )
            phase_status = (
                ResearchPhaseStatus.SUCCESS
                if evidence.complete
                else ResearchPhaseStatus.GAP
            )
            phases.append(
                ResearchPhaseResult(
                    "evidence",
                    phase_status,
                    f"{len(evidence.evidence)} evidence items",
                    sources=evidence.sources,
                )
            )
            if not evidence.complete:
                gaps.append(
                    ResearchExecutionGap(
                        "research_evidence",
                        "evidence_incomplete",
                        str(evidence.errors),
                        False,
                    )
                )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "research_evidence",
                    "evidence_fetch_failed",
                    error,
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "evidence",
                    ResearchPhaseStatus.GAP,
                    "Cycle evidence unavailable",
                    error=error,
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
                        options.risk_parameters.price_adjustment
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
                    factor_names=_rolling_risk_factor_names(risk.series),
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
                    f"{len(artifacts)} cycle chart artifacts",
                    sources=("research_visualization",),
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
                    "Cycle chart rendering incomplete",
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
                fundamental_results=list(fundamental.metrics),
                evidence_bundle=evidence,
                extra_evidence_types=("financial_statement",),
                factor_parameter_filters={
                    "max_drawdown": {"window": "input"},
                    "annualized_volatility": {"window": "input"},
                },
            )
            strategy_result = self.strategy_executor.execute(
                strategy,
                strategy_input,
            )
            phase_status = (
                ResearchPhaseStatus.GAP
                if strategy_result.status
                is StrategyRunStatus.INSUFFICIENT_DATA
                else ResearchPhaseStatus.SUCCESS
            )
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
            if phase_status is ResearchPhaseStatus.GAP:
                gaps.append(
                    ResearchExecutionGap(
                        "strategy",
                        "strategy_input_incomplete",
                        str(strategy_result.missing_metrics),
                        True,
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
                    "Cycle strategy execution failed",
                    error=error,
                )
            )

        context = collect_industry_context(
            self.valuation_position,
            self.benchmark_service,
            task=task,
            asset=asset,
            windows=windows,
            config=options,
        )
        gaps.extend(context.gaps)
        valuation_model, model_gaps = collect_valuation_model(
            self.valuation_models,
            task=task,
            asset=asset,
            fundamental=fundamental,
            config=options,
        )
        gaps.extend(model_gaps)
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
            fundamental_result=fundamental,
            benchmark_result=benchmark,
            evidence_bundle=evidence,
            chart_artifacts=artifacts,
            strategy_result=strategy_result,
            industry_context=context,
            valuation_model=valuation_model,
            dividends=dividends,
        )

    def _validate(self, task):
        if task.profile_id != PROFILE_CYCLICAL_RESOURCE:
            raise ValueError(
                "Cycle execution requires cyclical_resource"
            )
        if task.horizon is not ResearchHorizon.LONG:
            raise ValueError("Cycle execution supports long only")
        if task.benchmark_asset_id is None:
            raise ValueError("Cycle task requires benchmark_asset_id")
        asset = self.master_data.get_asset(task.asset_id)
        if asset is None or asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(
                "Cycle execution requires a stock asset"
            )
        compatible = [
            spec
            for spec in self.strategy_registry.list_specs()
            if task.horizon in spec.horizons
            and task.profile_id in spec.supported_profiles
        ]
        compatible.sort(
            key=lambda spec: (
                len(spec.supported_profiles),
                spec.strategy_id,
            )
        )
        if not compatible:
            raise ValueError("No cycle strategy supports task")
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
        phase_id = {
            "daily_bars_unavailable": "bars",
            "commodity_factor_calculation_failed": "factors",
            "cycle_fundamental_analysis_failed": "fundamental",
        }[code]
        phases.append(
            ResearchPhaseResult(
                phase_id,
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
