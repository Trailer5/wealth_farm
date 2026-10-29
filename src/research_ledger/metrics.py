"""Metric series for settlement: macro series and company financial metrics, read point-in-time.

Design notes: docs/modules/5-研究产物/观点账本模块.md, “指标结算”.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.data_access import DataAccessService
from src.fundamental_analysis import metric_history, metric_label
from src.macro_data import MacroDataService
from src.master_data import MasterDataService

from .settlement import MetricPoint, PriceDataUnavailable

MACRO_METRIC_PREFIX = "macro:"


def macro_metric_id(series_id: str) -> str:
    return f"{MACRO_METRIC_PREFIX}{series_id}"


class MacroMetricSeriesSource:
    """Settlement ``observed_id`` is ``macro:<series_id>`` from the macro catalog."""

    def __init__(
        self,
        *,
        macro_data: MacroDataService,
        allow_remote: bool = True,
    ) -> None:
        self.macro_data = macro_data
        self.allow_remote = allow_remote

    def known(self, metric_id: str) -> bool:
        series_id = _series_id(metric_id)
        return series_id is not None and any(
            item.series_id == series_id for item in self.macro_data.catalog()
        )

    def series(
        self,
        metric_id: str,
        start: str,
        end: str,
    ) -> tuple[MetricPoint, ...]:
        series_id = _series_id(metric_id)
        if series_id is None:
            raise PriceDataUnavailable(
                f"未知指标 {metric_id}；指标 ID 形如 {macro_metric_id('cpi_yoy')}"
            )
        try:
            result = self.macro_data.get(
                series_id,
                start_date=start,
                end_date=end,
                as_of_date=end,
                allow_remote=self.allow_remote,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise PriceDataUnavailable(str(exc)) from exc
        if not result.points and result.provider_errors:
            raise PriceDataUnavailable(
                "；".join(
                    f"{source}: {error}"
                    for source, error in result.provider_errors.items()
                )
            )
        return tuple(
            MetricPoint(
                period_end=item.period_end,
                available_at=item.available_at,
                value=Decimal(str(item.value)),
            )
            for item in result.points
        )


def _series_id(metric_id: str) -> str | None:
    if not metric_id.startswith(MACRO_METRIC_PREFIX):
        return None
    return metric_id[len(MACRO_METRIC_PREFIX):] or None


FIN_METRIC_PREFIX = "fin:"
# Enough history for TTM and year-on-year values of the first period in the window.
_FIN_HISTORY_DAYS = 1100


def fin_metric_id(asset_id: str, metric: str) -> str:
    return f"{FIN_METRIC_PREFIX}{asset_id}:{metric}"


def parse_fin_metric_id(metric_id: str) -> tuple[str, str] | None:
    """``fin:<asset_id>:<metric>``; asset IDs contain colons, metrics do not."""

    if not metric_id.startswith(FIN_METRIC_PREFIX):
        return None
    asset_id, _, metric = metric_id[len(FIN_METRIC_PREFIX):].rpartition(":")
    if not asset_id or metric_label(metric) is None:
        return None
    return asset_id, metric


class FinancialMetricSeriesSource:
    """Settlement ``observed_id`` ``fin:<asset_id>:<metric>``: one value per report period as first published."""

    def __init__(
        self,
        *,
        data_access: DataAccessService,
        master_data: MasterDataService,
        allow_remote: bool = True,
    ) -> None:
        self.data_access = data_access
        self.master_data = master_data
        self.allow_remote = allow_remote

    def known(self, metric_id: str) -> bool:
        parsed = parse_fin_metric_id(metric_id)
        return parsed is not None and self.master_data.get_asset(parsed[0]) is not None

    def series(self, metric_id: str, start: str, end: str) -> tuple[MetricPoint, ...]:
        parsed = parse_fin_metric_id(metric_id)
        if parsed is None:
            raise PriceDataUnavailable(
                f"未知指标 {metric_id}；财务指标 ID 形如 fin:<asset_id>:recurring_profit_growth"
            )
        asset_id, metric = parsed
        try:
            bundle = self.data_access.get_financial_bundle(
                asset_id=asset_id,
                start_date=(date.fromisoformat(start) - timedelta(days=_FIN_HISTORY_DAYS)).isoformat(),
                end_date=end,
                as_of_date=end,
                allow_remote=self.allow_remote,
                allow_stale_on_error=True,
            )
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            raise PriceDataUnavailable(str(exc)) from exc
        return tuple(
            MetricPoint(period_end=item.period_end, available_at=item.available_at, value=item.value)
            for item in metric_history(bundle, metric)
            if item.period_end >= start
        )


class CompositeMetricSource:
    """Route a metric ID to the source that knows it (macro series or company financials)."""

    def __init__(self, *sources: Any) -> None:
        self.sources = sources

    def known(self, metric_id: str) -> bool:
        return any(source.known(metric_id) for source in self.sources)

    def series(self, metric_id: str, start: str, end: str) -> tuple[MetricPoint, ...]:
        for source in self.sources:
            if source.known(metric_id):
                return source.series(metric_id, start, end)
        raise PriceDataUnavailable(
            f"未知指标 {metric_id}；宏观指标写 macro:<series_id>，公司财务指标写 fin:<asset_id>:<指标>"
        )
