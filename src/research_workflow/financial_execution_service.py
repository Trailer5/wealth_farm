"""Formal financial-institution research execution."""

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
from src.factor_engineering import FactorService
from src.financial_data import (
    DATASET_STOCK_FINANCIAL_INDICATORS,
)
from src.fundamental_analysis import (
    FinancialInstitutionAnalysisService,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    MasterDataService,
)
from src.research_contracts import (
    PROFILE_FINANCIAL_INSTITUTION,
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
    _rolling_risk_factor_names,
    _status,
)
from .return_bars import fetch_total_return_bars, risk_parameters_for


class FinancialInstitutionResearchExecutionService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        quality_service: DataQualityService,
        factor_service: FactorService,
        financial_service: FinancialInstitutionAnalysisService,
        benchmark_service: BenchmarkAnalysisService,
        evidence_service: ResearchEvidenceService,
        strategy_registry: StrategyRegistry,
        chart_renderer: ChartRenderer,
        strategy_builder: StrategyInputBuilder | None = None,
        strategy_executor: StrategyExecutor | None = None,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.quality_service = quality_service
        self.factor_service = factor_service
        self.financial_service = financial_service
        self.benchmark_service = benchmark_service
        self.evidence_service = evidence_service
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
                "Financial-institution task validated",
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
                    f"{len(bars.records)} bank daily bars",
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
            task_name=f"bank-research-execution:{task.task_id}",
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
                    "Bank bars did not pass quality gate",
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
                options=config,
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
                    config.risk_parameters,
                ),
            )
            factor_values.extend(risk.values)
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(factor_values)} market/risk factors",
                    sources=("factor_engineering",),
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
                "factor_calculation_failed",
                exc,
                bar_response=bars,
                quality_summary=quality,
            )

        profile_metrics = ()
        try:
            indicators = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset.asset_id),
                    dataset=DATASET_STOCK_FINANCIAL_INDICATORS,
                    start_date=windows.financial_start,
                    end_date=windows.financial_end,
                    as_of_date=task.as_of_date,
                    allow_remote=config.allow_remote,
                    remote_sources=("akshare",),
                    provider_timeout_seconds=(
                        config.provider_timeout_seconds
                    ),
                )
            )
            prices = [
                record
                for record in bars.records
                if str(record.get("trade_date")) <= task.as_of_date
            ]
            profile_metrics = self.financial_service.analyze(
                asset_id=asset.asset_id,
                as_of_date=task.as_of_date,
                market_price=Decimal(str(prices[-1]["close"])),
                indicator_records=[
                    dict(record) for record in indicators.records
                ],
            )
            phases.append(
                ResearchPhaseResult(
                    "fundamental",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(profile_metrics)} bank metrics",
                    sources=indicators.sources,
                    metric_versions=tuple(
                        sorted(
                            {
                                item.calculation_version
                                for item in profile_metrics
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
                "bank_metric_calculation_failed",
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
                raise ValueError("Bank benchmark must be an index")
            self.benchmark_service.set_benchmark(
                asset_id=asset.asset_id,
                benchmark_asset_id=benchmark_asset.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source="bank_research_execution",
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
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "benchmark",
                    ResearchPhaseStatus.GAP,
                    "Bank benchmark comparison unavailable",
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
                    "Bank evidence unavailable",
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
                    f"{len(artifacts)} bank chart artifacts",
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
                    "Bank chart rendering incomplete",
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
                fundamental_results=list(profile_metrics),
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
                    "Bank strategy execution failed",
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
            profile_metrics=tuple(profile_metrics),
            benchmark_result=benchmark,
            evidence_bundle=evidence,
            chart_artifacts=artifacts,
            strategy_result=strategy_result,
        )

    def _validate(self, task):
        if task.profile_id != PROFILE_FINANCIAL_INSTITUTION:
            raise ValueError(
                "Financial execution requires financial_institution"
            )
        if task.horizon is not ResearchHorizon.MEDIUM_LONG:
            raise ValueError(
                "Financial execution supports medium_long only"
            )
        if task.benchmark_asset_id is None:
            raise ValueError("Bank task requires benchmark_asset_id")
        asset = self.master_data.get_asset(task.asset_id)
        if asset is None or asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(
                "Financial execution requires a stock asset"
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
            raise ValueError("No bank strategy supports task")
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
            "factor_calculation_failed": "factors",
            "bank_metric_calculation_failed": "fundamental",
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
