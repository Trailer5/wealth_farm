"""Load discovery inputs from persisted fundamental and factor results."""

from __future__ import annotations

from decimal import Decimal

from src.factor_engineering import SQLiteFactorRepository
from src.fundamental_analysis import (
    MetricStatus,
    SQLiteFundamentalMetricRepository,
)
from src.strategy import StrategyMetricInput

from .models import DiscoveryMemberInput, DiscoverySpec


class StoredDiscoveryMetricLoader:
    """Read existing metrics without triggering remote provider calls."""

    def __init__(
        self,
        *,
        fundamental_repository: SQLiteFundamentalMetricRepository,
        factor_repository: SQLiteFactorRepository,
        factor_parameter_filters: dict[
            str,
            dict[str, object],
        ]
        | None = None,
    ) -> None:
        self.fundamental_repository = fundamental_repository
        self.factor_repository = factor_repository
        self.factor_parameter_filters = (
            factor_parameter_filters or {}
        )
        self.fundamental_repository.initialize()
        self.factor_repository.initialize()

    def load(
        self,
        *,
        asset_id: str,
        spec: DiscoverySpec,
    ) -> DiscoveryMemberInput:
        metrics: dict[str, StrategyMetricInput] = {}
        for metric_name in spec.required_metrics:
            fundamental = (
                self.fundamental_repository.list_results(
                    asset_id,
                    metric_name=metric_name,
                    as_of_or_before=spec.as_of_date,
                )
            )
            if fundamental:
                result = fundamental[0]
                available = (
                    result.status is MetricStatus.AVAILABLE
                    and result.value is not None
                )
                metrics[metric_name] = StrategyMetricInput(
                    metric_name=metric_name,
                    value=result.value if available else None,
                    as_of_date=result.as_of_date,
                    calculation_version=(
                        result.calculation_version
                    ),
                    applicable=available,
                    reason=(
                        None
                        if available
                        else (
                            f"{result.status.value}:"
                            f"{result.reason or 'no value'}"
                        )
                    ),
                )
                continue
            factors = [
                value
                for value in self.factor_repository.list_values(
                    asset_id,
                    factor_name=metric_name,
                )
                if value.as_of_date <= spec.as_of_date
                and _parameters_match(
                    value.parameters,
                    self.factor_parameter_filters.get(metric_name),
                )
            ]
            if not factors:
                continue
            latest_date = max(
                value.as_of_date for value in factors
            )
            latest = [
                value
                for value in factors
                if value.as_of_date == latest_date
            ]
            if len(latest) > 1:
                metrics[metric_name] = StrategyMetricInput(
                    metric_name=metric_name,
                    value=None,
                    as_of_date=latest_date,
                    calculation_version="ambiguous",
                    applicable=False,
                    reason="ambiguous_factor_parameters",
                )
                continue
            factor = latest[0]
            metrics[metric_name] = StrategyMetricInput(
                metric_name=metric_name,
                value=Decimal(str(factor.value)),
                as_of_date=factor.as_of_date,
                calculation_version=factor.calculation_version,
            )
        return DiscoveryMemberInput(
            asset_id=asset_id,
            profile_id=spec.profile_id,
            metrics=metrics,
        )


def _parameters_match(
    parameters: dict[str, object],
    expected: dict[str, object] | None,
) -> bool:
    return expected is None or all(
        parameters.get(key) == value
        for key, value in expected.items()
    )
