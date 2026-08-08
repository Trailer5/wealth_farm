"""Benchmark identity and relative-performance analysis."""

from .calculator import BenchmarkMetricCalculator
from .models import (
    BENCHMARK_CALCULATION_VERSION,
    AlignedReturnSeries,
    BenchmarkCalculationResult,
    BenchmarkKind,
    BenchmarkMetricParameters,
    BenchmarkRelation,
)
from .service import BenchmarkAnalysisService
from .resolver import DefaultBenchmarkResolver
from .sqlite_repository import SQLiteBenchmarkRepository

__all__ = [
    "BENCHMARK_CALCULATION_VERSION",
    "AlignedReturnSeries",
    "BenchmarkAnalysisService",
    "BenchmarkCalculationResult",
    "BenchmarkKind",
    "BenchmarkMetricCalculator",
    "BenchmarkMetricParameters",
    "DefaultBenchmarkResolver",
    "BenchmarkRelation",
    "SQLiteBenchmarkRepository",
]
