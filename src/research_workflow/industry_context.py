"""Valuation percentiles and Shenwan industry benchmark for stock research runs."""

from __future__ import annotations

from src.benchmark_analysis import BenchmarkAnalysisService
from src.master_data import Asset
from src.research_contracts import ResearchTaskSpec
from src.valuation_position import ValuationPositionService

from .execution_models import (
    IndustryContext,
    ResearchExecutionGap,
    ResearchExecutionOptions,
    ResearchWindows,
)


def collect_industry_context(
    service: ValuationPositionService | None,
    benchmark_service: BenchmarkAnalysisService,
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    windows: ResearchWindows,
    config: ResearchExecutionOptions,
) -> IndustryContext:
    """Both parts are optional context; failures become non-blocking gaps."""

    if service is None:
        return IndustryContext()
    gaps = []
    position = None
    try:
        position = service.position(
            asset.standard_symbol,
            as_of_date=task.as_of_date,
            allow_remote=config.allow_remote,
        )
    except Exception as exc:  # noqa: BLE001 - reported as a gap
        gaps.append(
            ResearchExecutionGap(
                "valuation_position",
                "valuation_position_unavailable",
                f"{type(exc).__name__}: {exc}",
                False,
            )
        )
    industry_benchmark = None
    name = None
    try:
        found = service.industry_index(asset.standard_symbol, allow_remote=config.allow_remote)
        if found is None:
            raise ValueError("not a current Shenwan industry member")
        index_asset, name = found
        industry_benchmark = benchmark_service.compare(
            asset_id=task.asset_id,
            start_date=windows.bar_start,
            end_date=windows.bar_end,
            as_of_date=task.as_of_date,
            benchmark_asset_id=index_asset.asset_id,
            allow_remote=config.allow_remote,
            provider_timeout_seconds=config.provider_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - reported as a gap
        name = None
        gaps.append(
            ResearchExecutionGap(
                "benchmark_analysis",
                "industry_benchmark_unavailable",
                f"{type(exc).__name__}: {exc}",
                False,
            )
        )
    return IndustryContext(position, industry_benchmark, name, tuple(gaps))
