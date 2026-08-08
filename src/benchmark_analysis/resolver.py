"""Resolve reliable profile benchmark roles to canonical assets."""

from __future__ import annotations

from typing import Any

from src.fund_research import (
    FundRelationType,
    SQLiteFundResearchRepository,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    Asset,
    MasterDataService,
)

from .models import BenchmarkKind, BenchmarkRelation
from .sqlite_repository import SQLiteBenchmarkRepository


class DefaultBenchmarkResolver:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        benchmark_repository: SQLiteBenchmarkRepository,
        fund_repository: SQLiteFundResearchRepository | None = None,
    ) -> None:
        self.master_data = master_data
        self.benchmark_repository = benchmark_repository
        self.fund_repository = fund_repository
        self.benchmark_repository.initialize()
        if self.fund_repository is not None:
            self.fund_repository.initialize()

    def resolve(
        self,
        asset: Asset,
        profile: Any,
        as_of_date: str,
    ) -> str | None:
        existing = self.benchmark_repository.get_default(
            asset.asset_id,
            as_of_date=as_of_date,
        )
        if existing is not None:
            return existing.benchmark_asset_id
        role = profile.default_benchmark_role
        if role == "broad_market":
            benchmark = self._broad_market()
        elif role == "underlying_index":
            benchmark = self._underlying_index(
                asset.asset_id,
                as_of_date,
            )
        else:
            return None
        if benchmark is None or benchmark.asset_id == asset.asset_id:
            return None
        self.benchmark_repository.save_relation(
            BenchmarkRelation(
                asset_id=asset.asset_id,
                benchmark_asset_id=benchmark.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source=f"profile_default:{role}",
                effective_date=as_of_date,
                reason=f"Resolved default benchmark role: {role}",
            )
        )
        return benchmark.asset_id

    def _broad_market(self) -> Asset | None:
        matches = self.master_data.find_assets(
            standard_symbol="000300",
            asset_type=ASSET_TYPE_INDEX,
        )
        return matches[0] if len(matches) == 1 else None

    def _underlying_index(
        self,
        asset_id: str,
        as_of_date: str,
    ) -> Asset | None:
        if self.fund_repository is None:
            return None
        relations = self.fund_repository.list_relations(
            asset_id,
            as_of_date=as_of_date,
            relation_type=FundRelationType.UNDERLYING_INDEX,
        )
        if not relations:
            return None
        latest_date = max(item.effective_date for item in relations)
        asset_ids = {
            item.related_asset_id
            for item in relations
            if item.effective_date == latest_date
            and item.related_asset_id is not None
        }
        if len(asset_ids) != 1:
            return None
        benchmark = self.master_data.get_asset(asset_ids.pop())
        if (
            benchmark is None
            or benchmark.asset_type != ASSET_TYPE_INDEX
        ):
            return None
        return benchmark
