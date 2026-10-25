"""Benchmark relationships and relative-performance results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.factor_engineering import FactorValue


BENCHMARK_CALCULATION_VERSION = "benchmark_daily_v1"


class BenchmarkKind(str, Enum):
    INDEX = "index"
    ETF_PROXY = "etf_proxy"
    COMPOSITE = "composite"


class BenchmarkComponentKind(str, Enum):
    INDEX = "index"
    TOTAL_RETURN_INDEX = "total_return_index"
    CASH_RATE = "cash_rate"


@dataclass(frozen=True)
class BenchmarkComponent:
    label: str
    kind: BenchmarkComponentKind
    weight: Decimal
    source: str
    index_asset_id: str | None = None
    annual_rate: Decimal | None = None
    rate_effective_date: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip() or not self.source.strip():
            raise ValueError("benchmark component label and source are required")
        if self.weight <= 0 or self.weight > Decimal("1"):
            raise ValueError("benchmark component weight must be in (0, 1]")
        if self.kind is BenchmarkComponentKind.CASH_RATE:
            if self.index_asset_id is not None:
                raise ValueError("cash-rate component must not reference an index")
            if self.annual_rate is None:
                raise ValueError("cash-rate component requires annual_rate")
            if self.annual_rate <= Decimal("-1"):
                raise ValueError("cash annual_rate must be greater than -1")
            if self.rate_effective_date is None:
                raise ValueError(
                    "cash-rate component requires rate_effective_date"
                )
            date.fromisoformat(self.rate_effective_date)
        elif self.annual_rate is not None or self.rate_effective_date is not None:
            raise ValueError("index component must not contain cash-rate fields")

    @property
    def resolved(self) -> bool:
        if self.kind is BenchmarkComponentKind.CASH_RATE:
            return self.annual_rate is not None
        return bool(self.index_asset_id)


@dataclass(frozen=True)
class CompositeBenchmarkDefinition:
    asset_id: str
    effective_date: str
    source: str
    source_document_id: str
    benchmark_text: str
    components: tuple[BenchmarkComponent, ...]
    is_default: bool = True
    resolution_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.asset_id,
                self.source,
                self.source_document_id,
                self.benchmark_text,
            )
        ):
            raise ValueError("benchmark definition identity must not be blank")
        date.fromisoformat(self.effective_date)
        if not self.components:
            raise ValueError("benchmark definition requires components")
        total_weight = sum(
            (component.weight for component in self.components),
            Decimal("0"),
        )
        if abs(total_weight - Decimal("1")) > Decimal("0.000001"):
            raise ValueError("benchmark component weights must sum to 1")

    @property
    def resolved(self) -> bool:
        return (
            not self.resolution_errors
            and all(component.resolved for component in self.components)
        )

    @property
    def synthetic_asset_id(self) -> str:
        return f"composite:{self.asset_id}:{self.effective_date}"


@dataclass(frozen=True)
class BenchmarkRelation:
    asset_id: str
    benchmark_asset_id: str
    benchmark_kind: BenchmarkKind
    source: str
    effective_date: str
    reason: str
    is_default: bool = True

    def __post_init__(self) -> None:
        if self.asset_id == self.benchmark_asset_id:
            raise ValueError(
                "Benchmark must differ from research asset"
            )
        date.fromisoformat(self.effective_date)
        if not self.source.strip() or not self.reason.strip():
            raise ValueError("source and reason must not be blank")


@dataclass(frozen=True)
class BenchmarkMetricParameters:
    window: int | None = None
    annualization_days: int = 252
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.window is not None and self.window < 2:
            raise ValueError("window must be at least 2")
        if self.annualization_days <= 0:
            raise ValueError("annualization_days must be positive")
        if self.risk_free_rate <= -1:
            raise ValueError("risk_free_rate must be greater than -1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window or "input",
            "annualization_days": self.annualization_days,
            "risk_free_rate": self.risk_free_rate,
            "alignment": "matching_observation_intervals",
            "variance_method": "population",
        }


@dataclass(frozen=True)
class AlignedReturnSeries:
    dates: tuple[str, ...]
    asset_returns: tuple[float, ...]
    benchmark_returns: tuple[float, ...]
    asset_cumulative: tuple[float, ...]
    benchmark_cumulative: tuple[float, ...]


@dataclass(frozen=True)
class BenchmarkCalculationResult:
    asset_id: str
    benchmark_asset_id: str
    benchmark_kind: BenchmarkKind
    parameters: BenchmarkMetricParameters
    aligned: AlignedReturnSeries
    series: tuple[FactorValue, ...]
    metrics: tuple[FactorValue, ...]
    benchmark_definition: CompositeBenchmarkDefinition | None = None

    @property
    def values(self) -> tuple[FactorValue, ...]:
        return self.series + self.metrics
