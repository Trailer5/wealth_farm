"""Industry assignment and comparable-company analysis."""

from __future__ import annotations

from decimal import Decimal

from src.fundamental_analysis import (
    MetricStatus,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import ASSET_TYPE_STOCK, MasterDataService
from src.sector_data import CLASSIFICATION, SectorDataService

from .models import (
    IndustryAssignment,
    PeerComparisonBundle,
    PeerComparisonStatus,
    PeerMetricComparison,
    PeerMetricValue,
    PeerSet,
    PeerSetMember,
)
from .sqlite_repository import SQLitePeerAnalysisRepository


class PeerAnalysisService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        metric_repository: SQLiteFundamentalMetricRepository,
        peer_repository: SQLitePeerAnalysisRepository,
        sector_data: SectorDataService | None = None,
    ) -> None:
        self.master_data = master_data
        self.metric_repository = metric_repository
        self.peer_repository = peer_repository
        self.sector_data = sector_data
        self.metric_repository.initialize()
        self.peer_repository.initialize()

    def assign_from_shenwan(
        self,
        *,
        asset_id: str,
        as_of_date: str,
        allow_remote: bool = True,
    ) -> tuple[IndustryAssignment, PeerSet | None, dict[str, object]] | None:
        """Save the Shenwan level-3 assignment and the default peer set."""

        if self.sector_data is None:
            return None
        asset = self._require_stock(asset_id)
        peers = self.sector_data.peers(asset.standard_symbol, allow_remote=allow_remote)
        if peers is None:
            return None
        l3 = peers["path"]["l3"]
        assignment = self.assign_industry(
            IndustryAssignment(
                asset_id=asset_id,
                industry_code=str(l3["code"]),
                industry_name=str(l3["name"]),
                classification_source=CLASSIFICATION,
                classification_version="current_members",
                effective_date=as_of_date,
            )
        )
        industry = peers["peer_industry"]
        members = []
        for item in peers["peers"]:
            resolution = self.master_data.resolve_identifier(
                source=CLASSIFICATION,
                raw_symbol=str(item["symbol"]),
                name=str(item["name"]),
                asset_type_hint=ASSET_TYPE_STOCK,
            )
            if resolution.asset is not None and resolution.asset.asset_id != asset_id:
                members.append(
                    PeerSetMember(
                        asset_id=resolution.asset.asset_id,
                        included=True,
                        reason=f"申万{industry['name']}当前成分",
                    )
                )
        peer_set = None
        if members:
            peer_set = self.save_peer_set(
                PeerSet(
                    peer_set_id=f"peer-set:{CLASSIFICATION}:{industry['code']}:{asset_id}",
                    target_asset_id=asset_id,
                    name=f"申万{industry['name']}同行",
                    source=CLASSIFICATION,
                    version="current_members",
                    effective_date=as_of_date,
                    members=tuple(members),
                )
            )
        return assignment, peer_set, peers

    def assign_industry(
        self,
        assignment: IndustryAssignment,
    ) -> IndustryAssignment:
        self._require_stock(assignment.asset_id)
        return self.peer_repository.save_industry(assignment)

    def get_industry(
        self,
        *,
        asset_id: str,
        as_of_date: str,
    ) -> IndustryAssignment | None:
        """Return the effective industry through the public service."""
        self._require_stock(asset_id)
        return self.peer_repository.get_industry(
            asset_id,
            as_of_date=as_of_date,
        )

    def save_peer_set(self, peer_set: PeerSet) -> PeerSet:
        self._require_stock(peer_set.target_asset_id)
        for member in peer_set.members:
            self._require_stock(member.asset_id)
        return self.peer_repository.save_peer_set(peer_set)

    def compare(
        self,
        *,
        peer_set_id: str,
        as_of_date: str,
        metric_names: tuple[str, ...],
    ) -> PeerComparisonBundle:
        if not metric_names:
            raise ValueError("metric_names must not be empty")
        if len(set(metric_names)) != len(metric_names):
            raise ValueError(
                "metric_names must not contain duplicates"
            )
        peer_set = self.peer_repository.get_peer_set(
            peer_set_id,
            as_of_date=as_of_date,
        )
        if peer_set is None:
            raise KeyError(
                f"Unknown or not-yet-effective peer_set: "
                f"{peer_set_id}"
            )
        industry = self.peer_repository.get_industry(
            peer_set.target_asset_id,
            as_of_date=as_of_date,
        )
        target_metrics = self._latest_metrics(
            peer_set.target_asset_id,
            as_of_date,
        )
        included_members = [
            member
            for member in peer_set.members
            if member.included
        ]
        peer_metrics = {
            member.asset_id: self._latest_metrics(
                member.asset_id,
                as_of_date,
            )
            for member in included_members
        }
        comparisons = tuple(
            self._compare_metric(
                peer_set,
                metric_name,
                target_metrics.get(metric_name),
                peer_metrics,
            )
            for metric_name in metric_names
        )
        chart_data = {
            comparison.metric_name: {
                "labels": [
                    self._asset_name(
                        peer_set.target_asset_id
                    ),
                    *[
                        self._asset_name(value.asset_id)
                        for value in comparison.peers
                    ],
                ],
                "values": [
                    (
                        format(comparison.target.value, "f")
                        if comparison.target is not None
                        else None
                    ),
                    *[
                        format(value.value, "f")
                        for value in comparison.peers
                    ],
                ],
                "unit": (
                    comparison.target.unit
                    if comparison.target is not None
                    else None
                ),
                "excluded_assets": (
                    comparison.excluded_assets
                ),
            }
            for comparison in comparisons
        }
        return PeerComparisonBundle(
            peer_set=peer_set,
            industry=industry,
            as_of_date=as_of_date,
            comparisons=comparisons,
            chart_data=chart_data,
        )

    def _compare_metric(
        self,
        peer_set: PeerSet,
        metric_name: str,
        target_result,
        peer_metrics,
    ) -> PeerMetricComparison:
        excluded = {
            member.asset_id: f"excluded_by_peer_set: {member.reason}"
            for member in peer_set.members
            if not member.included
        }
        target = _metric_value(target_result)
        if target is None:
            reason = (
                "target metric is missing"
                if target_result is None
                else f"target metric status: "
                f"{target_result.status.value}"
            )
            excluded[peer_set.target_asset_id] = reason
            return PeerMetricComparison(
                metric_name=metric_name,
                status=PeerComparisonStatus.INCOMPATIBLE,
                target=None,
                peers=(),
                sample_count=0,
                median=None,
                mean=None,
                standard_deviation=None,
                minimum=None,
                maximum=None,
                target_percentile=None,
                target_vs_median=None,
                excluded_assets=excluded,
            )
        compatible: list[PeerMetricValue] = []
        for asset_id, metrics in peer_metrics.items():
            result = metrics.get(metric_name)
            value = _metric_value(result)
            if value is None:
                excluded[asset_id] = (
                    "metric missing"
                    if result is None
                    else f"metric status: {result.status.value}"
                )
                continue
            incompatibility = _incompatibility(target_result, result)
            if incompatibility is not None:
                excluded[asset_id] = incompatibility
                continue
            compatible.append(value)
        compatible.sort(key=lambda value: value.asset_id)
        if len(compatible) < 2:
            return PeerMetricComparison(
                metric_name=metric_name,
                status=PeerComparisonStatus.INSUFFICIENT_SAMPLE,
                target=target,
                peers=tuple(compatible),
                sample_count=len(compatible),
                median=None,
                mean=None,
                standard_deviation=None,
                minimum=None,
                maximum=None,
                target_percentile=None,
                target_vs_median=None,
                excluded_assets=excluded,
            )
        values = [value.value for value in compatible]
        median = _median(values)
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        variance = sum(
            (value - mean) ** 2
            for value in values
        ) / Decimal(len(values))
        percentile = Decimal(
            sum(value <= target.value for value in values)
        ) / Decimal(len(values))
        return PeerMetricComparison(
            metric_name=metric_name,
            status=PeerComparisonStatus.AVAILABLE,
            target=target,
            peers=tuple(compatible),
            sample_count=len(values),
            median=median,
            mean=mean,
            standard_deviation=variance.sqrt(),
            minimum=min(values),
            maximum=max(values),
            target_percentile=percentile,
            target_vs_median=(
                target.value / median - Decimal("1")
                if median != 0
                else None
            ),
            excluded_assets=excluded,
        )

    def _latest_metrics(
        self,
        asset_id: str,
        as_of_date: str,
    ):
        results = self.metric_repository.list_results(
            asset_id,
            as_of_or_before=as_of_date,
        )
        latest = {}
        for result in results:
            latest.setdefault(result.metric_name, result)
        return latest

    def _require_stock(self, asset_id: str):
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        if asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(
                f"Peer analysis requires stock assets: {asset_id}"
            )
        return asset

    def _asset_name(self, asset_id: str) -> str:
        asset = self.master_data.get_asset(asset_id)
        return (
            asset.display_name or asset.standard_symbol
            if asset is not None
            else asset_id
        )


def _metric_value(result) -> PeerMetricValue | None:
    if (
        result is None
        or result.status is not MetricStatus.AVAILABLE
        or result.value is None
    ):
        return None
    return PeerMetricValue(
        asset_id=result.asset_id,
        value=result.value,
        unit=result.unit,
        report_period=result.report_period,
        as_of_date=result.as_of_date,
    )


def _incompatibility(target, peer) -> str | None:
    if target.unit != peer.unit:
        return f"unit mismatch: {peer.unit} != {target.unit}"
    if target.report_period != peer.report_period:
        return (
            f"report_period mismatch: {peer.report_period} "
            f"!= {target.report_period}"
        )
    if target.calculation_version != peer.calculation_version:
        return "calculation_version mismatch"
    target_currency = target.parameters.get("currency")
    peer_currency = peer.parameters.get("currency")
    if target_currency != peer_currency:
        return (
            f"currency mismatch: {peer_currency} "
            f"!= {target_currency}"
        )
    return None


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (
        ordered[midpoint - 1] + ordered[midpoint]
    ) / Decimal("2")
