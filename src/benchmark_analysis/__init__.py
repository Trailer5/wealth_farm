"""Benchmark identity and relative-performance analysis."""

from .calculator import BenchmarkMetricCalculator
from .models import (
    BENCHMARK_CALCULATION_VERSION,
    AlignedReturnSeries,
    BenchmarkCalculationResult,
    BenchmarkComponent,
    BenchmarkComponentKind,
    BenchmarkKind,
    BenchmarkMetricParameters,
    BenchmarkRelation,
    CompositeBenchmarkDefinition,
)
from .service import (
    BenchmarkAnalysisService,
    BenchmarkComponentUnavailableError,
    BenchmarkCoverageError,
    BenchmarkDefinitionIncompleteError,
)
from .resolver import (
    BROAD_MARKET_FALLBACK_ROLES,
    BROAD_MARKET_SYMBOL,
    DefaultBenchmarkResolver,
)
from .sqlite_repository import SQLiteBenchmarkRepository

__all__ = [
    "BENCHMARK_CALCULATION_VERSION",
    "BROAD_MARKET_FALLBACK_ROLES",
    "BROAD_MARKET_SYMBOL",
    "AlignedReturnSeries",
    "BenchmarkAnalysisService",
    "BenchmarkCalculationResult",
    "BenchmarkComponent",
    "BenchmarkComponentKind",
    "BenchmarkComponentUnavailableError",
    "BenchmarkCoverageError",
    "BenchmarkDefinitionIncompleteError",
    "BenchmarkKind",
    "BenchmarkMetricCalculator",
    "BenchmarkMetricParameters",
    "DefaultBenchmarkResolver",
    "BenchmarkRelation",
    "CompositeBenchmarkDefinition",
    "SQLiteBenchmarkRepository",
]
