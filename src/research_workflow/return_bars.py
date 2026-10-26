"""Total-return bars for return and risk metrics in research execution."""

from __future__ import annotations

from dataclasses import replace

from src.data_access import (
    TOTAL_RETURN_BAR_DATASETS,
    TOTAL_RETURN_DATASET_BY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessResponse,
)
from src.factor_engineering import RiskMetricParameters

from .execution_models import ResearchExecutionGap, ResearchExecutionOptions


def fetch_total_return_bars(
    data_access,
    bars: DataAccessResponse,
    *,
    start_date: str,
    end_date: str,
    as_of_date: str,
    options: ResearchExecutionOptions,
) -> tuple[DataAccessResponse, ResearchExecutionGap | None]:
    """Total-return bars, or the unadjusted bars with a non-blocking gap."""

    dataset = TOTAL_RETURN_DATASET_BY_BARS.get(bars.dataset)
    if dataset is None:
        return bars, None
    try:
        response = data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=bars.asset.asset_id),
                dataset=dataset,
                start_date=start_date,
                end_date=end_date,
                as_of_date=as_of_date,
                allow_remote=options.allow_remote,
                provider_timeout_seconds=options.provider_timeout_seconds,
            )
        )
    except Exception as exc:  # noqa: BLE001 - unadjusted bars remain usable
        error = f"{type(exc).__name__}: {exc}"
    else:
        if response.dataset == dataset and response.complete and response.records:
            return response, None
        error = response.error or "复权总回报日线不完整"
    return bars, ResearchExecutionGap(
        "data_access",
        "total_return_bars_unavailable",
        (
            "复权总回报日线不可用，收益、波动和回撤改用未复权收盘价，"
            f"区间内分红、送转或拆分会使结果失真：{error}"
        ),
        False,
    )


def risk_parameters_for(
    response: DataAccessResponse,
    parameters: RiskMetricParameters,
) -> RiskMetricParameters:
    if response.dataset in TOTAL_RETURN_BAR_DATASETS:
        return replace(parameters, price_adjustment="total_return")
    return parameters
