"""Formal open-fund research execution over public service boundaries."""

from __future__ import annotations

from src.benchmark_analysis import (
    BENCHMARK_CALCULATION_VERSION,
    BenchmarkAnalysisService,
    BenchmarkComponentKind,
    BenchmarkComponentUnavailableError,
    BenchmarkCoverageError,
    BenchmarkDefinitionIncompleteError,
)
from src.data_access import (
    DATASET_FUND_ALLOCATION,
    DATASET_FUND_SCALE,
    DATASET_FUND_STATUS,
    DATASET_OPEN_FUND_NAV,
    DataAccessResponse,
)
from src.data_src.common import DataSourceError
from src.data_quality import DataQualityService
from src.factor_engineering import FactorService
from src.fund_research import (
    FundResearchBundle,
    FundResearchService,
    OpenFundBenchmarkResolver,
    OpenFundHistoricalSummaryCalculator,
)
from src.master_data import ASSET_TYPE_OPEN_FUND, MasterDataService
from src.research_visualization import (
    ChartRenderer,
    ChartType,
    build_factor_series_spec,
)

from .execution_models import (
    ResearchExecutionGap,
    ResearchExecutionOptions,
    ResearchExecutionResult,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)
from .execution_service import (
    _blocked,
    _complete_phases,
    _derive_windows,
    _error,
    _status,
)
from .models import ResearchHorizon, ResearchQuestionType, ResearchTaskSpec
from .profiles import PROFILE_OPEN_FUND


_WINDOW_DAYS = {
    ResearchHorizon.SHORT: 180,
    ResearchHorizon.MEDIUM_SHORT: 365,
    ResearchHorizon.MEDIUM_LONG: 1095,
    ResearchHorizon.LONG: 3650,
}
_FULL_NAV_START = "1900-01-01"


class OpenFundResearchExecutionService:
    """Collect NAV, fund attributes, risk metrics, and charts."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        fund_research: FundResearchService,
        quality_service: DataQualityService,
        factor_service: FactorService,
        benchmark_service: BenchmarkAnalysisService,
        benchmark_resolver: OpenFundBenchmarkResolver,
        chart_renderer: ChartRenderer,
    ) -> None:
        self.master_data = master_data
        self.fund_research = fund_research
        self.quality_service = quality_service
        self.factor_service = factor_service
        self.benchmark_service = benchmark_service
        self.benchmark_resolver = benchmark_resolver
        self.chart_renderer = chart_renderer
        self.summary_calculator = OpenFundHistoricalSummaryCalculator()

    def execute(
        self,
        task: ResearchTaskSpec,
        *,
        options: ResearchExecutionOptions | None = None,
    ) -> ResearchExecutionResult:
        config = options or ResearchExecutionOptions()
        asset = self._validate(task)
        windows = _derive_windows(task, _WINDOW_DAYS[task.horizon])
        phases = [
            ResearchPhaseResult(
                "task",
                ResearchPhaseStatus.SUCCESS,
                "Open-fund ResearchTaskSpec validated",
                sources=("research_workflow",),
            )
        ]
        gaps: list[ResearchExecutionGap] = []

        bundle = None
        try:
            bundle = self.fund_research.get_open_fund_bundle(
                asset_id=asset.asset_id,
                start_date=_FULL_NAV_START,
                end_date=windows.bar_end,
                as_of_date=task.as_of_date,
                fund_type=_fund_type(asset.display_name),
                allocation_year=task.as_of_date[:4],
                allow_remote=config.allow_remote,
                provider_timeout_seconds=config.provider_timeout_seconds,
            )
            nav_response = _nav_response(bundle)
            if not nav_response.records:
                nav_error = bundle.errors.get(DATASET_OPEN_FUND_NAV)
                code = (
                    "open_fund_nav_source_unavailable"
                    if nav_error
                    else "open_fund_nav_business_empty"
                )
                message = (
                    f"正式净值来源暂时不可用：{nav_error}"
                    if nav_error
                    else "数据源正常返回，但该基金没有正式净值记录。"
                )
                gaps.append(
                    ResearchExecutionGap(
                        "fund_research",
                        code,
                        message,
                        True,
                    )
                )
                phases.append(
                    ResearchPhaseResult(
                        "bars",
                        ResearchPhaseStatus.FAILED,
                        "Official open-fund NAV unavailable",
                        error=message,
                    )
                )
                return _blocked(
                    task,
                    windows,
                    phases,
                    gaps,
                    fund_bundle=bundle,
                )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(nav_response.records)} official NAV observations",
                    sources=nav_response.sources,
                )
            )
        except (DataSourceError, OSError, TimeoutError) as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "fund_research",
                    "open_fund_nav_source_unavailable",
                    f"正式净值来源暂时不可用：{error}",
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.FAILED,
                    "Official open-fund NAV unavailable",
                    error=error,
                )
            )
            return _blocked(
                task,
                windows,
                phases,
                gaps,
                fund_bundle=bundle,
            )
        except Exception as exc:
            error = _error(exc)
            gaps.append(
                ResearchExecutionGap(
                    "fund_research",
                    "open_fund_nav_collection_failed",
                    (
                        "正式净值采集链路发生未分类失败："
                        f"{error}"
                    ),
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "bars",
                    ResearchPhaseStatus.FAILED,
                    "Official open-fund NAV collection failed",
                    error=error,
                )
            )
            return _blocked(
                task,
                windows,
                phases,
                gaps,
                fund_bundle=bundle,
            )

        windowed_nav_response = _windowed_nav_response(
            nav_response,
            start_date=windows.bar_start,
            end_date=windows.bar_end,
        )
        quality = self.quality_service.evaluate_data_access_response(
            task_name=f"open-fund-research-execution:{task.task_id}",
            response=windowed_nav_response,
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
                    "open_fund_nav_quality_gate",
                    "Open-fund NAV did not pass the quality gate",
                    True,
                )
            )

        factor_values = []
        risk = None
        standard_metrics = None
        try:
            factor_values.extend(
                self.factor_service.compute_from_response(nav_response)
            )
            standard_metrics = (
                self.factor_service.compute_open_fund_standard_from_response(
                    nav_response,
                    as_of_date=task.as_of_date,
                )
            )
            factor_values.extend(standard_metrics.values)
            gaps.extend(
                ResearchExecutionGap(
                    "factor_engineering",
                    (
                        "open_fund_standard_window_"
                        f"{item.key}_{item.reason}"
                    ),
                    (
                        f"{item.label}收益率不可用："
                        f"{_standard_window_reason(item.reason)}"
                    ),
                    False,
                )
                for item in standard_metrics.unavailable_windows
            )
            summary_values = self.summary_calculator.calculate(
                asset_id=asset.asset_id,
                as_of_date=task.as_of_date,
                share_class_code=asset.standard_symbol,
                profile_snapshot=bundle.profile_snapshot,
                periodic_snapshot=bundle.periodic_snapshot,
                scale_records=bundle.scale_records,
            )
            self.factor_service.store_values(list(summary_values))
            factor_values.extend(summary_values)
            risk = self.factor_service.compute_risk_from_response(
                windowed_nav_response,
                parameters=config.risk_parameters,
            )
            factor_values.extend(risk.values)
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(factor_values)} NAV-derived factor values",
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
                    "open_fund_risk_calculation_failed",
                    error,
                    True,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "factors",
                    ResearchPhaseStatus.FAILED,
                    "Open-fund return and risk calculation failed",
                    error=error,
                )
            )
            return _blocked(
                task,
                windows,
                phases,
                gaps,
                bar_response=nav_response,
                quality_summary=quality,
                fund_bundle=bundle,
            )

        phases.append(
            ResearchPhaseResult(
                "fundamental",
                ResearchPhaseStatus.NOT_APPLICABLE,
                "Company fundamental analysis is not applicable to a fund",
            )
        )
        benchmark = None
        profile_snapshot = bundle.profile_snapshot
        if (
            profile_snapshot is None
            or not profile_snapshot.benchmark_text.strip()
        ):
            gaps.append(
                ResearchExecutionGap(
                    "benchmark_analysis",
                    "open_fund_benchmark_definition_missing",
                    "官方产品资料中没有可用的业绩比较基准定义。",
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "benchmark",
                    ResearchPhaseStatus.GAP,
                    "Official benchmark definition is unavailable",
                )
            )
        else:
            try:
                definition = self.benchmark_resolver.resolve(
                    profile_snapshot
                )
                benchmark = self.benchmark_service.compare_composite(
                    asset_id=asset.asset_id,
                    asset_records=[
                        dict(record)
                        for record in nav_response.records
                    ],
                    definition=definition,
                    start_date=windows.bar_start,
                    end_date=windows.bar_end,
                    as_of_date=task.as_of_date,
                    allow_remote=config.allow_remote,
                    provider_timeout_seconds=(
                        config.provider_timeout_seconds
                    ),
                )
                factor_values.extend(benchmark.values)
                if any(
                    component.kind is BenchmarkComponentKind.CASH_RATE
                    for component in definition.components
                ):
                    gaps.append(
                        ResearchExecutionGap(
                            "benchmark_analysis",
                            (
                                "open_fund_benchmark_cash_rate_"
                                "history_approximated"
                            ),
                            (
                                "复合基准中的现金利率组件按披露利率"
                                "固定延展，尚未重建逐次利率调整历史；"
                                "跨调整阶段的累计基准收益为近似值。"
                            ),
                            False,
                        )
                    )
                phases.append(
                    ResearchPhaseResult(
                        "benchmark",
                        ResearchPhaseStatus.SUCCESS,
                        (
                            f"{len(benchmark.aligned.dates)} common "
                            "benchmark observations"
                        ),
                        sources=tuple(
                            sorted(
                                {
                                    profile_snapshot.source,
                                    *(
                                        component.source
                                        for component
                                        in definition.components
                                    ),
                                }
                            )
                        ),
                        metric_versions=(
                            BENCHMARK_CALCULATION_VERSION,
                        ),
                    )
                )
            except BenchmarkDefinitionIncompleteError as exc:
                _append_benchmark_gap(
                    phases,
                    gaps,
                    code="open_fund_benchmark_definition_incomplete",
                    message=(
                        "官方基准定义存在未解析组件，未生成相对表现："
                        f"{exc}"
                    ),
                )
            except BenchmarkComponentUnavailableError as exc:
                _append_benchmark_gap(
                    phases,
                    gaps,
                    code="open_fund_benchmark_source_unavailable",
                    message=(
                        "官方基准至少一个必需组件的数据源不可用，"
                        f"未降级为部分基准：{exc}"
                    ),
                )
            except BenchmarkCoverageError as exc:
                _append_benchmark_gap(
                    phases,
                    gaps,
                    code="open_fund_benchmark_coverage_insufficient",
                    message=(
                        "基金与全部基准组件的共同历史覆盖不足："
                        f"{exc}"
                    ),
                )
            except (ValueError, KeyError) as exc:
                _append_benchmark_gap(
                    phases,
                    gaps,
                    code="open_fund_benchmark_definition_incomplete",
                    message=f"官方基准定义无法建立关系：{exc}",
                )
            except Exception as exc:
                _append_benchmark_gap(
                    phases,
                    gaps,
                    code="open_fund_benchmark_calculation_failed",
                    message=(
                        "开放式基金相对表现计算失败："
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

        evidence_gaps = _bundle_gaps(bundle)
        gaps.extend(evidence_gaps)
        phases.append(
            ResearchPhaseResult(
                "evidence",
                (
                    ResearchPhaseStatus.GAP
                    if evidence_gaps
                    else ResearchPhaseStatus.SUCCESS
                ),
                (
                    f"status={len(bundle.status_records)}, "
                    f"scale={len(bundle.scale_records)}, "
                    f"allocation={len(bundle.allocation_records)}, "
                    f"estimates={len(bundle.estimate_records)}"
                ),
                sources=bundle.sources or ("fund_research",),
            )
        )

        artifacts = ()
        try:
            cumulative_values = list(risk.series)
            cumulative_names = ("cumulative_return",)
            cumulative_source = DATASET_OPEN_FUND_NAV
            cumulative_benchmark_id = None
            if benchmark is not None and getattr(
                benchmark,
                "series",
                (),
            ):
                cumulative_values = list(benchmark.series)
                cumulative_names = (
                    "asset_cumulative_return",
                    "benchmark_cumulative_return",
                )
                cumulative_source = benchmark.series[0].source_dataset
                cumulative_benchmark_id = benchmark.benchmark_asset_id
            specs = (
                build_factor_series_spec(
                    task=task,
                    asset=asset,
                    chart_type=ChartType.CUMULATIVE_RETURN,
                    values=cumulative_values,
                    factor_names=cumulative_names,
                    source_dataset=cumulative_source,
                    benchmark_asset_id=cumulative_benchmark_id,
                ),
                build_factor_series_spec(
                    task=task,
                    asset=asset,
                    chart_type=ChartType.DRAWDOWN,
                    values=list(risk.series),
                    factor_names=("drawdown_series",),
                    source_dataset=DATASET_OPEN_FUND_NAV,
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
                    source_dataset=DATASET_OPEN_FUND_NAV,
                ),
            )
            artifacts = tuple(
                self.chart_renderer.render(spec) for spec in specs
            )
            phases.append(
                ResearchPhaseResult(
                    "charts",
                    ResearchPhaseStatus.SUCCESS,
                    f"{len(artifacts)} open-fund chart artifacts",
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
                    "open_fund_chart_render_failed",
                    error,
                    False,
                )
            )
            phases.append(
                ResearchPhaseResult(
                    "charts",
                    ResearchPhaseStatus.GAP,
                    "Open-fund chart rendering incomplete",
                    error=error,
                )
            )

        phases.append(
            ResearchPhaseResult(
                "strategy",
                ResearchPhaseStatus.NOT_APPLICABLE,
                "Fact-understanding research does not run a trading strategy",
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
            bar_response=nav_response,
            quality_summary=quality,
            factor_values=tuple(factor_values),
            risk_result=risk,
            open_fund_standard_metrics=standard_metrics,
            benchmark_result=benchmark,
            fund_bundle=bundle,
            chart_artifacts=artifacts,
        )

    def _validate(self, task: ResearchTaskSpec):
        if task.profile_id != PROFILE_OPEN_FUND:
            raise ValueError(
                "OpenFundResearchExecutionService requires open_fund"
            )
        if task.question_type is not ResearchQuestionType.UNDERSTANDING:
            raise ValueError(
                "Open-fund execution currently supports understanding only"
            )
        asset = self.master_data.get_asset(task.asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {task.asset_id}")
        if asset.asset_type != ASSET_TYPE_OPEN_FUND:
            raise ValueError("Open-fund execution requires open_fund asset")
        return asset


def _nav_response(bundle: FundResearchBundle) -> DataAccessResponse:
    by_date: dict[str, dict[str, object]] = {}
    for record in sorted(
        bundle.nav_records,
        key=lambda item: (
            str(item.get("nav_date") or ""),
            str(item.get("source") or ""),
        ),
    ):
        nav_date = str(record.get("nav_date") or "")[:10]
        if not nav_date or nav_date in by_date:
            continue
        by_date[nav_date] = {
            **record,
            "symbol": record.get("symbol")
            or bundle.asset.standard_symbol,
            "trade_date": nav_date,
            "close": record.get("unit_nav"),
        }
    records = tuple(by_date[value_date] for value_date in sorted(by_date))
    dates = tuple(by_date)
    sources = tuple(
        sorted(
            {
                str(record.get("source"))
                for record in records
                if record.get("source")
            }
        )
    )
    return DataAccessResponse(
        asset=bundle.asset,
        dataset=DATASET_OPEN_FUND_NAV,
        records=records,
        sources=sources,
        coverage_start=min(dates) if dates else None,
        coverage_end=max(dates) if dates else None,
        complete=bool(records),
        metadata={
            "record_semantics": "official_unit_nav_as_close",
            "as_of_date": bundle.as_of_date,
        },
    )


def _windowed_nav_response(
    response: DataAccessResponse,
    *,
    start_date: str,
    end_date: str,
) -> DataAccessResponse:
    records = tuple(
        record
        for record in response.records
        if start_date
        <= str(record.get("trade_date") or record.get("nav_date") or "")[:10]
        <= end_date
    )
    if not records:
        raise ValueError("No official NAV observations in execution window")
    dates = tuple(
        str(record.get("trade_date") or record.get("nav_date"))[:10]
        for record in records
    )
    return DataAccessResponse(
        asset=response.asset,
        dataset=response.dataset,
        records=records,
        sources=response.sources,
        coverage_start=min(dates),
        coverage_end=max(dates),
        complete=response.complete,
        metadata={
            **response.metadata,
            "execution_window_start": start_date,
            "execution_window_end": end_date,
        },
    )


def _bundle_gaps(
    bundle: FundResearchBundle,
) -> list[ResearchExecutionGap]:
    records_by_dataset = {
        DATASET_FUND_STATUS: bundle.status_records,
        DATASET_FUND_SCALE: bundle.scale_records,
        DATASET_FUND_ALLOCATION: bundle.allocation_records,
    }
    dataset_labels = {
        DATASET_FUND_STATUS: "最新申购赎回状态",
        DATASET_FUND_SCALE: "基金规模",
        DATASET_FUND_ALLOCATION: "基金配置",
    }
    required_datasets = set(records_by_dataset)
    gaps = [
        ResearchExecutionGap(
            "fund_research",
            f"{dataset}_unavailable",
            f"{dataset_labels[dataset]}来源暂时不可用：{error}",
            False,
        )
        for dataset, error in sorted(bundle.errors.items())
        if dataset in required_datasets
    ]
    failed = set(bundle.errors)
    gaps.extend(
        ResearchExecutionGap(
            "fund_research",
            f"{dataset}_empty",
            f"未取得{dataset_labels[dataset]}记录。",
            False,
        )
        for dataset, records in records_by_dataset.items()
        if not records and dataset not in failed
    )
    return gaps


def _standard_window_reason(reason: str) -> str:
    return {
        "history_shorter_than_window": "基金历史短于该标准区间。",
        "no_observation_near_window_start": (
            "目标起点附近没有满足容忍范围的正式净值。"
        ),
        "fewer_than_two_observations": (
            "该区间少于两个正式净值观察点。"
        ),
    }.get(reason, f"无法满足标准区间计算条件（{reason}）。")


def _append_benchmark_gap(
    phases: list[ResearchPhaseResult],
    gaps: list[ResearchExecutionGap],
    *,
    code: str,
    message: str,
) -> None:
    gaps.append(
        ResearchExecutionGap(
            "benchmark_analysis",
            code,
            message,
            False,
        )
    )
    phases.append(
        ResearchPhaseResult(
            "benchmark",
            ResearchPhaseStatus.GAP,
            "Open-fund benchmark comparison is unavailable",
            error=message,
        )
    )


def _fund_type(display_name: str | None) -> str:
    name = display_name or ""
    for keyword, fund_type in (
        ("混合", "混合型基金"),
        ("债券", "债券型基金"),
        ("货币", "货币型基金"),
        ("股票", "股票型基金"),
    ):
        if keyword in name:
            return fund_type
    return "股票型基金"
