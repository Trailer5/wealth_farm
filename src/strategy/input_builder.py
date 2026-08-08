"""Adapt factor, fundamental, and evidence outputs to strategy inputs."""

from __future__ import annotations

import json
from decimal import Decimal

from src.factor_engineering import FactorValue
from src.fundamental_analysis import (
    FundamentalMetricResult,
    MetricStatus,
)
from src.research_evidence import ResearchEvidenceBundle
from src.research_workflow import ResearchHorizon

from .models import (
    StrategyInput,
    StrategyMetricInput,
)


class StrategyInputBuilder:
    def build(
        self,
        *,
        asset_id: str,
        profile_id: str,
        horizon: ResearchHorizon,
        as_of_date: str,
        factor_values: list[FactorValue] | None = None,
        fundamental_results: (
            list[FundamentalMetricResult] | None
        ) = None,
        evidence_bundle: ResearchEvidenceBundle | None = None,
        extra_metrics: dict[
            str,
            StrategyMetricInput,
        ] | None = None,
        factor_parameter_filters: dict[
            str,
            dict[str, object],
        ] | None = None,
        extra_evidence_types: tuple[str, ...] = (),
    ) -> StrategyInput:
        metrics: dict[str, StrategyMetricInput] = {}
        filters = factor_parameter_filters or {}
        factor_names = {
            value.factor_name
            for value in factor_values or []
            if value.as_of_date <= as_of_date
        }
        for metric_name in sorted(factor_names):
            selected = _select_factor(
                factor_values or [],
                metric_name,
                as_of_date,
                filters.get(metric_name),
            )
            if selected is not None:
                metrics[metric_name] = StrategyMetricInput(
                    metric_name=metric_name,
                    value=Decimal(str(selected.value)),
                    as_of_date=selected.as_of_date,
                    calculation_version=(
                        selected.calculation_version
                    ),
                )
        latest_fundamental = {}
        for result in sorted(
            fundamental_results or [],
            key=lambda item: (
                item.as_of_date,
                item.computed_at,
            ),
            reverse=True,
        ):
            if result.as_of_date <= as_of_date:
                latest_fundamental.setdefault(
                    result.metric_name,
                    result,
                )
        for metric_name, result in latest_fundamental.items():
            metrics[metric_name] = StrategyMetricInput(
                metric_name=metric_name,
                value=result.value,
                as_of_date=result.as_of_date,
                calculation_version=result.calculation_version,
                applicable=(
                    result.status is MetricStatus.AVAILABLE
                    and result.value is not None
                ),
                reason=result.reason,
            )
        for metric_name, metric in (extra_metrics or {}).items():
            if metric_name != metric.metric_name:
                raise ValueError(
                    "extra_metrics key must equal metric_name"
                )
            metrics[metric_name] = metric
        evidence_types = tuple(
            sorted(
                set(_evidence_types(evidence_bundle))
                | set(extra_evidence_types)
            )
        )
        return StrategyInput(
            asset_id=asset_id,
            profile_id=profile_id,
            horizon=horizon,
            as_of_date=as_of_date,
            metrics=metrics,
            evidence_types=evidence_types,
        )


def _select_factor(
    values: list[FactorValue],
    metric_name: str,
    as_of_date: str,
    parameter_filter: dict[str, object] | None,
) -> FactorValue | None:
    candidates = [
        value
        for value in values
        if value.factor_name == metric_name
        and value.as_of_date <= as_of_date
        and (
            parameter_filter is None
            or all(
                value.parameters.get(key) == expected
                for key, expected in parameter_filter.items()
            )
        )
    ]
    if not candidates:
        return None
    latest_date = max(value.as_of_date for value in candidates)
    latest = [
        value
        for value in candidates
        if value.as_of_date == latest_date
    ]
    if len(latest) > 1:
        parameter_sets = {
            json.dumps(
                value.parameters,
                sort_keys=True,
                default=str,
            )
            for value in latest
        }
        if len(parameter_sets) > 1:
            raise ValueError(
                f"Ambiguous factor parameters for {metric_name}; "
                "provide factor_parameter_filters"
            )
    return sorted(
        latest,
        key=lambda value: (
            value.calculation_version,
            json.dumps(
                value.parameters,
                sort_keys=True,
                default=str,
            ),
        ),
    )[-1]


def _evidence_types(
    bundle: ResearchEvidenceBundle | None,
) -> tuple[str, ...]:
    if bundle is None:
        return ()
    types = {
        item.evidence_type
        for item in bundle.evidence
    }
    for item in bundle.evidence:
        if item.evidence_type == "disclosure":
            category = str(
                item.metadata.get("category") or ""
            ).lower()
            title = item.title
            if (
                any(
                    keyword in category
                    for keyword in (
                        "annual",
                        "quarter",
                        "report",
                    )
                )
                or any(
                    keyword in title
                    for keyword in (
                        "年度报告",
                        "半年度报告",
                        "季度报告",
                    )
                )
            ):
                types.add("financial_statement")
        if item.evidence_type == "research_event":
            event_type = item.metadata.get("event_type")
            if event_type:
                types.add(f"event:{event_type}")
    return tuple(sorted(types))
