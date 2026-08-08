"""Resumable real-metric preparation for discovery universes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum

from src.benchmark_analysis import (
    BenchmarkAnalysisService,
    BenchmarkKind,
)
from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.factor_engineering import FactorService
from src.fundamental_analysis import FundamentalAnalysisService
from src.master_data import ASSET_TYPE_INDEX, MasterDataService
from src.research_universe import (
    MembershipStatus,
    ResearchUniverseService,
)
from src.research_workflow import (
    PROFILE_ORDINARY_COMPANY,
    ResearchExecutionOptions,
)

from .metrics_loader import StoredDiscoveryMetricLoader
from .models import DiscoverySpec


class MetricPreparationStatus(str, Enum):
    CACHED = "cached"
    PREPARED = "prepared"
    MISSING = "missing"
    UNCLASSIFIED = "unclassified"
    UNSUPPORTED_PROFILE = "unsupported_profile"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class MemberMetricPreparation:
    source_symbol: str
    asset_id: str | None
    status: MetricPreparationStatus
    missing_metrics: tuple[str, ...] = ()
    error: str | None = None
    remote_attempted: bool = False


@dataclass(frozen=True)
class UniverseMetricPreparationResult:
    universe_id: str
    universe_version: str
    as_of_date: str
    remote_member_limit: int
    remote_attempted: int
    members: tuple[MemberMetricPreparation, ...]

    def count(self, status: MetricPreparationStatus) -> int:
        return sum(item.status is status for item in self.members)


class DiscoveryMetricPreparationService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        universe_service: ResearchUniverseService,
        metric_loader: StoredDiscoveryMetricLoader,
        data_access: DataAccessService,
        factor_service: FactorService,
        fundamental_service: FundamentalAnalysisService,
        benchmark_service: BenchmarkAnalysisService,
    ) -> None:
        self.master_data = master_data
        self.universe_service = universe_service
        self.metric_loader = metric_loader
        self.data_access = data_access
        self.factor_service = factor_service
        self.fundamental_service = fundamental_service
        self.benchmark_service = benchmark_service

    def prepare_universe(
        self,
        spec: DiscoverySpec,
        *,
        profile_by_asset_id: dict[str, str],
        remote_member_limit: int,
        options: ResearchExecutionOptions | None = None,
        skip_remote_asset_ids: set[str] | None = None,
    ) -> UniverseMetricPreparationResult:
        if remote_member_limit < 0:
            raise ValueError(
                "remote_member_limit must not be negative"
            )
        config = options or ResearchExecutionOptions()
        skipped_remote = skip_remote_asset_ids or set()
        snapshot = self.universe_service.get(
            spec.universe_id,
            version=spec.universe_version,
        )
        if snapshot is None:
            raise KeyError(f"Unknown universe: {spec.universe_id}")
        if spec.as_of_date < snapshot.universe.effective_date:
            raise ValueError(
                "Preparation as_of_date precedes universe"
            )
        benchmark_asset_id = (
            spec.benchmark_asset_id
            or snapshot.universe.index_asset_id
        )
        results = []
        remote_attempted = 0
        for member in snapshot.memberships:
            if member.status is MembershipStatus.UNRESOLVED:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        None,
                        MetricPreparationStatus.UNRESOLVED,
                    )
                )
                continue
            if member.status is MembershipStatus.INACTIVE:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        member.asset_id,
                        MetricPreparationStatus.INACTIVE,
                    )
                )
                continue
            asset_id = member.asset_id
            if asset_id is None:
                raise ValueError(
                    "Resolved membership requires asset_id"
                )
            profile = profile_by_asset_id.get(asset_id)
            if profile is None:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.UNCLASSIFIED,
                        missing_metrics=spec.required_metrics,
                    )
                )
                continue
            if profile != PROFILE_ORDINARY_COMPANY:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.UNSUPPORTED_PROFILE,
                        missing_metrics=spec.required_metrics,
                    )
                )
                continue
            missing = _missing(
                self.metric_loader.load(
                    asset_id=asset_id,
                    spec=spec,
                ),
                spec,
            )
            if not missing:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.CACHED,
                    )
                )
                continue
            if remote_attempted >= remote_member_limit:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.MISSING,
                        missing_metrics=missing,
                    )
                )
                continue
            if asset_id in skipped_remote:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.MISSING,
                        missing_metrics=missing,
                    )
                )
                continue
            remote_attempted += 1
            try:
                self._prepare_member(
                    asset_id=asset_id,
                    benchmark_asset_id=benchmark_asset_id,
                    spec=spec,
                    options=config,
                )
                remaining = _missing(
                    self.metric_loader.load(
                        asset_id=asset_id,
                        spec=spec,
                    ),
                    spec,
                )
                status = (
                    MetricPreparationStatus.PREPARED
                    if not remaining
                    else MetricPreparationStatus.MISSING
                )
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        status,
                        missing_metrics=remaining,
                        remote_attempted=True,
                    )
                )
            except Exception as exc:
                results.append(
                    MemberMetricPreparation(
                        member.source_symbol,
                        asset_id,
                        MetricPreparationStatus.FAILED,
                        missing_metrics=missing,
                        error=f"{type(exc).__name__}: {exc}",
                        remote_attempted=True,
                    )
                )
        return UniverseMetricPreparationResult(
            universe_id=spec.universe_id,
            universe_version=snapshot.universe.version,
            as_of_date=spec.as_of_date,
            remote_member_limit=remote_member_limit,
            remote_attempted=remote_attempted,
            members=tuple(results),
        )

    def _prepare_member(
        self,
        *,
        asset_id: str,
        benchmark_asset_id: str,
        spec: DiscoverySpec,
        options: ResearchExecutionOptions,
    ) -> None:
        end = date.fromisoformat(spec.as_of_date)
        start = (
            end - timedelta(days=spec.data_window_days)
        ).isoformat()
        bars = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date=start,
                end_date=spec.as_of_date,
                as_of_date=spec.as_of_date,
                allow_remote=options.allow_remote,
                remote_sources=options.bar_remote_sources,
                provider_timeout_seconds=(
                    options.provider_timeout_seconds
                ),
            )
        )
        self.factor_service.compute_from_response(bars)
        self.factor_service.compute_risk_from_response(
            bars,
            parameters=options.risk_parameters,
        )
        benchmark = self.master_data.get_asset(benchmark_asset_id)
        if (
            benchmark is None
            or benchmark.asset_type != ASSET_TYPE_INDEX
        ):
            raise ValueError("Discovery benchmark must be an index")
        self.benchmark_service.set_benchmark(
            asset_id=asset_id,
            benchmark_asset_id=benchmark.asset_id,
            benchmark_kind=BenchmarkKind.INDEX,
            source="discovery_metric_preparation",
            effective_date=start,
            reason="DiscoverySpec benchmark",
        )
        self.benchmark_service.compare(
            asset_id=asset_id,
            start_date=start,
            end_date=spec.as_of_date,
            as_of_date=spec.as_of_date,
            benchmark_asset_id=benchmark.asset_id,
            allow_remote=options.allow_remote,
            provider_timeout_seconds=(
                options.provider_timeout_seconds
            ),
        )
        financials = self.data_access.get_financial_bundle(
            asset_id=asset_id,
            start_date=(end - timedelta(days=1095)).isoformat(),
            end_date=spec.as_of_date,
            as_of_date=spec.as_of_date,
            allow_remote=options.allow_remote,
            provider_timeout_seconds=(
                options.provider_timeout_seconds
            ),
        )
        prices = [
            record
            for record in bars.records
            if str(record.get("trade_date")) <= spec.as_of_date
        ]
        self.fundamental_service.analyze(
            financials,
            market_price=Decimal(str(prices[-1]["close"])),
        )


def _missing(member_input, spec) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in spec.required_metrics
            if name not in member_input.metrics
            or not member_input.metrics[name].applicable
            or member_input.metrics[name].value is None
        )
    )
