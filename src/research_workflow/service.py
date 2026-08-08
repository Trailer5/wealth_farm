"""Factory boundary for validated research task specifications."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from src.master_data import Asset, MasterDataService

from .models import (
    AssetResearchProfile,
    ResearchHorizon,
    ResearchOutputDepth,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from .profiles import (
    ResearchProfileRegistry,
    default_profile_registry,
)


class DefaultBenchmarkResolverProtocol(Protocol):
    def resolve(
        self,
        asset: Asset,
        profile: AssetResearchProfile,
        as_of_date: str,
    ) -> str | None: ...


class ResearchTaskFactory:
    def __init__(
        self,
        master_data: MasterDataService,
        profile_registry: ResearchProfileRegistry | None = None,
        benchmark_resolver: (
            DefaultBenchmarkResolverProtocol | None
        ) = None,
    ) -> None:
        self.master_data = master_data
        self.profile_registry = (
            profile_registry or default_profile_registry()
        )
        self.benchmark_resolver = benchmark_resolver

    def create(
        self,
        *,
        asset_id: str,
        question_type: ResearchQuestionType,
        horizon: ResearchHorizon,
        as_of_date: str,
        profile_id: str | None = None,
        benchmark_asset_id: str | None = None,
        peer_asset_ids: tuple[str, ...] = (),
        output_depth: ResearchOutputDepth = (
            ResearchOutputDepth.STANDARD
        ),
        requested_metrics: tuple[str, ...] = (),
        requested_outputs: tuple[str, ...] | None = None,
    ) -> ResearchTaskSpec:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        profile = (
            self.profile_registry.default_for_asset(asset)
            if profile_id is None
            else self.profile_registry.validate_for_asset(
                profile_id,
                asset,
            )
        )
        if (
            benchmark_asset_id is None
            and self.benchmark_resolver is not None
        ):
            benchmark_asset_id = self.benchmark_resolver.resolve(
                asset,
                profile,
                as_of_date,
            )
        self._require_related_assets(
            benchmark_asset_id,
            peer_asset_ids,
        )
        self._validate_requested_metrics(
            requested_metrics,
            profile.forbidden_metrics,
        )
        arguments = {
            "task_id": f"research:{uuid4()}",
            "asset_id": asset.asset_id,
            "profile_id": profile.profile_id,
            "question_type": question_type,
            "horizon": horizon,
            "as_of_date": as_of_date,
            "benchmark_asset_id": benchmark_asset_id,
            "peer_asset_ids": peer_asset_ids,
            "output_depth": output_depth,
            "requested_metrics": requested_metrics,
        }
        if requested_outputs is not None:
            arguments["requested_outputs"] = requested_outputs
        return ResearchTaskSpec(**arguments)

    def restore(
        self,
        payload: dict[str, object],
    ) -> ResearchTaskSpec:
        task = ResearchTaskSpec.from_dict(payload)
        asset = self.master_data.get_asset(task.asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {task.asset_id}")
        profile = self.profile_registry.validate_for_asset(
            task.profile_id,
            asset,
        )
        self._validate_requested_metrics(
            task.requested_metrics,
            profile.forbidden_metrics,
        )
        self._require_related_assets(
            task.benchmark_asset_id,
            task.peer_asset_ids,
        )
        return task

    @staticmethod
    def _validate_requested_metrics(
        requested_metrics: tuple[str, ...],
        forbidden_metrics: tuple[str, ...],
    ) -> None:
        forbidden = set(requested_metrics) & set(forbidden_metrics)
        if forbidden:
            raise ValueError(
                "Research profile forbids requested metrics: "
                f"{sorted(forbidden)}"
            )

    def _require_related_assets(
        self,
        benchmark_asset_id: str | None,
        peer_asset_ids: tuple[str, ...],
    ) -> None:
        related_ids = [
            asset_id
            for asset_id in (
                benchmark_asset_id,
                *peer_asset_ids,
            )
            if asset_id is not None
        ]
        missing = [
            asset_id
            for asset_id in related_ids
            if self.master_data.get_asset(asset_id) is None
        ]
        if missing:
            raise KeyError(
                f"Unknown related asset_id: {missing[0]}"
            )
