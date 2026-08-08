"""Exact, evidence-backed fundamental metric models."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


FUNDAMENTAL_CALCULATION_VERSION = "fundamental_v1"


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    MISSING_INPUT = "missing_input"
    NOT_APPLICABLE = "not_applicable"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class FundamentalInputEvidence:
    field_name: str
    value: Decimal
    report_period: str
    source: str
    unit: str
    currency: str | None = None


@dataclass(frozen=True)
class FundamentalSnapshot:
    asset_id: str
    as_of_date: str
    report_period: str
    currency: str
    market_price: Decimal
    shares_outstanding: Decimal | None
    fields: dict[str, Decimal]
    prior_fields: dict[str, Decimal]
    evidence: dict[
        str,
        tuple[FundamentalInputEvidence, ...],
    ] = field(default_factory=dict)
    prior_evidence: dict[
        str,
        tuple[FundamentalInputEvidence, ...],
    ] = field(default_factory=dict)
    flow_basis: str = "ttm"


@dataclass(frozen=True)
class FundamentalMetricResult:
    asset_id: str
    metric_name: str
    as_of_date: str
    report_period: str
    status: MetricStatus
    value: Decimal | None
    unit: str
    formula: str
    numerator: Decimal | None
    denominator: Decimal | None
    reason: str | None
    calculation_version: str
    parameters: dict[str, Any]
    inputs: tuple[FundamentalInputEvidence, ...]
    computed_at: str


@dataclass(frozen=True)
class FundamentalMetricSet:
    snapshot: FundamentalSnapshot
    metrics: tuple[FundamentalMetricResult, ...]

    def by_name(self) -> dict[str, FundamentalMetricResult]:
        return {
            metric.metric_name: metric
            for metric in self.metrics
        }
