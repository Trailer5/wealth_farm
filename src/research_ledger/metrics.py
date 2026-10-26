"""Metric series for settlement, read point-in-time from the macro data module."""

from __future__ import annotations

from decimal import Decimal

from src.macro_data import MacroDataService

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
