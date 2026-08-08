"""Benchmark relationships and relative-performance results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from src.factor_engineering import FactorValue


BENCHMARK_CALCULATION_VERSION = "benchmark_daily_v1"


class BenchmarkKind(str, Enum):
    INDEX = "index"
    ETF_PROXY = "etf_proxy"


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

    @property
    def values(self) -> tuple[FactorValue, ...]:
        return self.series + self.metrics
