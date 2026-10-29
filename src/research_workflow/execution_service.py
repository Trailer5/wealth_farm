"""Formal ordinary-company research execution over public services."""

from __future__ import annotations

from datetime import date, timedelta
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
from src.factor_engineering import FactorService
from src.fundamental_analysis import DividendHistoryService, FundamentalAnalysisService, ValuationModelService
from src.fundamental_analysis.service import COMPANY_PROFILES
from src.master_data import ASSET_TYPE_INDEX, MasterDataService
from src.research_contracts import (
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_ORDINARY_COMPANY,
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
    ResearchExecutionGap,
    ResearchExecutionOptions,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchWindows,
)
from .industry_context import collect_industry_context
from .valuation_context import collect_dividends, collect_growth_trend, collect_valuation_model
from .return_bars import fetch_total_return_bars, risk_parameters_for


_PHASES = (
    "task",
    "bars",
    "quality",
    "factors",
    "fundamental",
    "benchmark",
    "evidence",
    "charts",
    "strategy",
)


class ResearchExecutionService:
    """Execute the first production ordinary-company research slice."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        quality_service: DataQualityService,
        factor_service: FactorService,
        fundamental_service: FundamentalAnalysisService,
        benchmark_service: BenchmarkAnalysisService,
        evidence_service: ResearchEvidenceService,
        strategy_registry: StrategyRegistry,
        chart_renderer: ChartRenderer,
        strategy_builder: StrategyInputBuilder | None = None,
        strategy_executor: StrategyExecutor | None = None,
        valuation_position: ValuationPositionService | None = None,
        valuation_models: ValuationModelService | None = None,
        profile_id: str = PROFILE_ORDINARY_COMPANY,
        dividends: DividendHistoryService | None = None,
    ) -> None:
        if profile_id not in COMPANY_PROFILES:
            raise ValueError(f"ResearchExecutionService does not run {profile_id}")
        self.profile_id = profile_id
        self.dividends = dividends
        self.master_data = master_data
        self.data_access = data_access
        self.quality_service = quality_service
        self.factor_service = factor_service
        self.fundamental_service = fundamental_service
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
        options: ResearchExecutionOptions | None = None,
    ) -> ResearchExecutionResult:
        config = options or ResearchExecutionOptions()
        asset, strategy = self._validate(task)
        windows = _derive_windows(task, strategy.data_window_days)
        phases = [
            ResearchPhaseResult(
                "task",
                ResearchPhaseStatus.SUCCESS,
                "ResearchTaskSpec validated",
                sources=("research_workflow",),
            )
        ]
        gaps: list[ResearchExecutionGap] = []

        try:
            bars = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=task.asset_id),
                    dataset=DATASET_STOCK_DAILY_BARS,
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
                    f"{len(bars.records)} daily bars",
                    sources=bars.sources,
                )
            )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "data_access",
                    "daily_bars_unavailable",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.FAILED,
                    "Daily bars unavailable",
                    error=error,
                )
            )
            return _blocked(task, windows, phases, gaps)

        quality = self.quality_service.evaluate_data_access_response(
            task_name=f"research-execution:{task.task_id}",
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
                    "Daily bars did not pass the quality gate",
                    True,
                )
            )

        try:
            return_bars, basis_gap = fetch_total_return_bars(
                self.data_access,
                bars,
                start_date=windows.bar_start,
                end_date=windows.bar_end,
                as_of_date=task.as_of_date,
                options=config,
            )
            if basis_gap is not None:
                gaps.append(basis_gap)
            market_factors = self.factor_service.compute_from_response(
                return_bars
            )
            risk = self.factor_service.compute_risk_from_response(
                return_bars,
                parameters=risk_parameters_for(
                    return_bars,
                    config.risk_parameters,
                ),
            )
            factor_values = [*market_factors, *risk.values]
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(factor_values)} factor values",
                    sources=("factor_engineering",),
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
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "factor_engineering",
                    "factor_calculation_failed",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.FAILED,
                    "Factor calculation failed",
                    error=error,
                )
            )
            return _blocked(
                task,
                windows,
                phases,
                gaps,
                bar_response=bars,
                quality_summary=quality,
            )

        fundamental = None
        growth_trend = None
        dividends = None
        try:
            financials = self.data_access.get_financial_bundle(
                asset_id=task.asset_id,
                start_date=windows.financial_start,
                end_date=windows.financial_end,
                as_of_date=task.as_of_date,
                allow_remote=config.allow_remote,
                provider_timeout_seconds=(
                    config.provider_timeout_seconds
                ),
            )
            eligible_bars = [
                record
                for record in bars.records
                if str(record.get("trade_date")) <= task.as_of_date
            ]
            if not eligible_bars:
                raise ValueError(
                    "No market price at or before as_of_date"
                )
            price = Decimal(str(eligible_bars[-1]["close"]))
            dividends, dividend_gaps = collect_dividends(
                self.dividends,
                asset=asset,
                as_of_date=task.as_of_date,
                price=price,
                config=config,
            )
            gaps.extend(dividend_gaps)
            fundamental = self.fundamental_service.analyze(
                financials,
                market_price=price,
                profile_id=task.profile_id,
                dividend_per_share_ttm=(
                    None if dividends is None else Decimal(dividends["ttm_per_share"])
                ),
            )
            if task.profile_id == PROFILE_HIGH_GROWTH_UNPROFITABLE:
                growth_trend = collect_growth_trend(financials)
            phases.append(
                ResearchPhaseResult(
                    "fundamental",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(fundamental.metrics)} fundamental metrics",
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
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "fundamental_analysis",
                    "fundamental_analysis_failed",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "fundamental",
                    ResearchPhaseStatus.FAILED,
                    "Fundamental analysis failed",
                    error=error,
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
                raise ValueError(
                    "Ordinary-company benchmark must be an index"
                )
            self.benchmark_service.set_benchmark(
                asset_id=task.asset_id,
                benchmark_asset_id=benchmark_asset.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source="research_execution",
                effective_date=windows.bar_start,
                reason="ResearchTaskSpec benchmark",
            )
            benchmark = self.benchmark_service.compare(
                asset_id=task.asset_id,
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
                    metric_versions=tuple(
                        sorted(
                            {
                                item.calculation_version
                                for item in benchmark.values
                            }
                        )
                    ),
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
                    "Benchmark comparison unavailable",
                    error=error,
                )
            )

        evidence = None
        try:
            evidence = self.evidence_service.get_bundle(
                asset_id=task.asset_id,
                start_date=windows.evidence_start,
                end_date=windows.evidence_end,
                as_of_date=task.as_of_date,
                allow_remote=config.allow_remote,
                disclosure_sources=config.disclosure_sources,
                research_sources=config.research_sources,
                provider_timeout_seconds=(
                    config.provider_timeout_seconds
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
                    "Evidence unavailable",
                    error=error,
                )
            )

        artifacts = ()
        try:
            # Valuation history and financial trend charts need multi-period
            # history (roadmap stage 6); one snapshot would mislead.
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
                    f"{len(artifacts)} chart artifacts",
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
                    "Chart rendering incomplete",
                    error=error,
                )
            )

        strategy_result = None
        try:
            if fundamental is None:
                raise ValueError(
                    "Fundamental result required for strategy"
                )
            strategy_input = self.strategy_builder.build(
                asset_id=task.asset_id,
                profile_id=task.profile_id,
                horizon=task.horizon,
                as_of_date=task.as_of_date,
                factor_values=factor_values,
                fundamental_results=list(fundamental.metrics),
                evidence_bundle=evidence,
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
                    metric_versions=tuple(
                        sorted(
                            set(
                                strategy_result.metric_versions.values()
                            )
                        )
                    ),
                )
            )
            if phase_status is ResearchPhaseStatus.GAP:
                gaps.append(
                    ResearchExecutionGap(
                        "strategy",
                        "strategy_input_incomplete",
                        str(
                            {
                                "metrics": (
                                    strategy_result.missing_metrics
                                ),
                                "evidence": (
                                    strategy_result.missing_evidence_types
                                ),
                            }
                        ),
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
                    "Strategy execution failed",
                    error=error,
                )
            )

        context = collect_industry_context(
            self.valuation_position,
            self.benchmark_service,
            task=task,
            asset=asset,
            windows=windows,
            config=config,
        )
        gaps.extend(context.gaps)
        valuation_model, model_gaps = collect_valuation_model(
            self.valuation_models,
            task=task,
            asset=asset,
            fundamental=fundamental,
            config=config,
        )
        gaps.extend(model_gaps)
        phases = _complete_phases(phases)
        status = _status(phases, gaps)
        return ResearchExecutionResult(
            task=task,
            windows=windows,
            status=status,
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
            growth_trend=growth_trend,
            dividends=dividends,
        )

    def _validate(self, task: ResearchTaskSpec):
        if task.profile_id != self.profile_id:
            raise ValueError(
                f"This ResearchExecutionService runs {self.profile_id} only"
            )
        if task.horizon is not ResearchHorizon.MEDIUM_LONG:
            raise ValueError(
                "ResearchExecutionService v1 supports "
                "medium_long only"
            )
        if task.benchmark_asset_id is None:
            raise ValueError("Research task requires benchmark_asset_id")
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
                "No strategy supports task profile and horizon"
            )
        compatible.sort(
            key=lambda spec: (
                len(spec.supported_profiles),
                spec.strategy_id,
            )
        )
        return asset, compatible[0]


def _derive_windows(
    task: ResearchTaskSpec,
    strategy_window_days: int,
) -> ResearchWindows:
    end = date.fromisoformat(task.as_of_date)
    return ResearchWindows(
        bar_start=(
            end - timedelta(days=strategy_window_days)
        ).isoformat(),
        bar_end=task.as_of_date,
        financial_start=(end - timedelta(days=1095)).isoformat(),
        financial_end=task.as_of_date,
        evidence_start=(end - timedelta(days=548)).isoformat(),
        evidence_end=task.as_of_date,
        strategy_window_days=strategy_window_days,
    )


def _blocked(
    task,
    windows,
    phases,
    gaps,
    **kwargs,
) -> ResearchExecutionResult:
    completed = _complete_phases(phases)
    return ResearchExecutionResult(
        task=task,
        windows=windows,
        status=ResearchExecutionStatus.BLOCKED,
        phases=tuple(completed),
        gaps=tuple(gaps),
        **kwargs,
    )


def _complete_phases(
    phases: list[ResearchPhaseResult],
) -> list[ResearchPhaseResult]:
    existing = {phase.phase_id for phase in phases}
    return [
        *phases,
        *[
            ResearchPhaseResult(
                phase_id,
                ResearchPhaseStatus.SKIPPED,
                "Skipped due to prior phase outcome",
            )
            for phase_id in _PHASES
            if phase_id not in existing
        ],
    ]


def _status(phases, gaps) -> ResearchExecutionStatus:
    if any(gap.blocking for gap in gaps) or any(
        phase.status is ResearchPhaseStatus.FAILED
        for phase in phases
    ):
        return ResearchExecutionStatus.BLOCKED
    if gaps or any(
        phase.status
        in {ResearchPhaseStatus.GAP, ResearchPhaseStatus.SKIPPED}
        for phase in phases
    ):
        return ResearchExecutionStatus.PARTIAL
    return ResearchExecutionStatus.COMPLETE


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _rolling_risk_factor_names(series) -> tuple[str, str]:
    """Prefer the longest rolling window that produced observations."""

    available = {item.factor_name for item in series}
    for window in (60, 20, 10):
        names = (
            f"rolling_annualized_volatility_{window}d",
            f"rolling_sharpe_{window}d",
        )
        if names[0] in available:
            return names
    return (
        "rolling_annualized_volatility_5d",
        "rolling_sharpe_5d",
    )
