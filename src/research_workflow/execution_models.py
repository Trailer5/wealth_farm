"""Structured results for formal single-asset research execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.benchmark_analysis import BenchmarkCalculationResult
from src.data_access import DataAccessResponse
from src.data_quality import QualitySummary
from src.factor_engineering import (
    FactorValue,
    OpenFundStandardMetricResult,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.fund_research import FundResearchBundle
from src.fundamental_analysis import (
    FundamentalMetricResult,
    FundamentalMetricSet,
)
from src.research_contracts import ResearchTaskSpec
from src.research_evidence import ResearchEvidenceBundle
from src.research_visualization import ChartArtifact
from src.strategy import StrategyResult


class ResearchExecutionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class ResearchPhaseStatus(str, Enum):
    SUCCESS = "success"
    GAP = "gap"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ResearchExecutionOptions:
    chart_output_root: str = "temp/research"
    allow_remote: bool = True
    bar_remote_sources: tuple[str, ...] = (
        "mootdx",
        "tencent",
        "sina",
        "ths",
    )
    disclosure_sources: tuple[str, ...] = ("cninfo",)
    research_sources: tuple[str, ...] = ("eastmoney",)
    provider_timeout_seconds: float = 90.0
    risk_parameters: RiskMetricParameters = field(
        default_factory=lambda: RiskMetricParameters(
            rolling_windows=(5, 20, 60),
            price_adjustment="none",
        )
    )

    def __post_init__(self) -> None:
        if self.provider_timeout_seconds <= 0:
            raise ValueError(
                "provider_timeout_seconds must be positive"
            )


@dataclass(frozen=True)
class CycleResearchExecutionOptions(ResearchExecutionOptions):
    commodity_weights: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        weights = dict(self.commodity_weights)
        if (
            not weights
            or len(weights) != len(self.commodity_weights)
        ):
            raise ValueError(
                "commodity_weights must be non-empty and unique"
            )
        allowed = {"sina:CU0", "sge:Au99.99"}
        if not set(weights).issubset(allowed):
            raise ValueError(
                "Unsupported commodity benchmark"
            )
        if (
            any(value < 0 for value in weights.values())
            or abs(sum(weights.values()) - 1.0) > 1e-9
        ):
            raise ValueError(
                "commodity_weights must be non-negative and sum to 1"
            )


@dataclass(frozen=True)
class ResearchWindows:
    bar_start: str
    bar_end: str
    financial_start: str
    financial_end: str
    evidence_start: str
    evidence_end: str
    strategy_window_days: int


@dataclass(frozen=True)
class ResearchExecutionGap:
    module: str
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class ResearchPhaseResult:
    phase_id: str
    status: ResearchPhaseStatus
    summary: str
    sources: tuple[str, ...] = ()
    metric_versions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ResearchExecutionResult:
    task: ResearchTaskSpec
    windows: ResearchWindows
    status: ResearchExecutionStatus
    phases: tuple[ResearchPhaseResult, ...]
    gaps: tuple[ResearchExecutionGap, ...]
    bar_response: DataAccessResponse | None = None
    quality_summary: QualitySummary | None = None
    factor_values: tuple[FactorValue, ...] = ()
    risk_result: RiskCalculationResult | None = None
    open_fund_standard_metrics: (
        OpenFundStandardMetricResult | None
    ) = None
    fundamental_result: FundamentalMetricSet | None = None
    profile_metrics: tuple[FundamentalMetricResult, ...] = ()
    benchmark_result: BenchmarkCalculationResult | None = None
    evidence_bundle: ResearchEvidenceBundle | None = None
    fund_bundle: FundResearchBundle | None = None
    chart_artifacts: tuple[ChartArtifact, ...] = ()
    strategy_result: StrategyResult | None = None

    def __post_init__(self) -> None:
        phase_ids = [phase.phase_id for phase in self.phases]
        if len(set(phase_ids)) != len(phase_ids):
            raise ValueError("Research phase IDs must be unique")
