"""Build, persist, and safely query research universes."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone

from src.data_src import IndexConstituent
from src.data_src.service import DataFetchService
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    MasterDataService,
)

from .models import (
    HistoricalUniverseUsageError,
    MembershipStatus,
    ResearchUniverse,
    UniverseMembership,
    UniverseQualitySummary,
    UniverseSnapshot,
)
from .sqlite_repository import SQLiteResearchUniverseRepository


class ResearchUniverseService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_fetch: DataFetchService,
        repository: SQLiteResearchUniverseRepository,
    ) -> None:
        self.master_data = master_data
        self.data_fetch = data_fetch
        self.repository = repository
        self.repository.initialize()

    def refresh_index_universe(
        self,
        *,
        universe_id: str,
        name: str,
        index_asset_id: str,
        source: str = "akshare",
        provider_timeout_seconds: float = 30.0,
    ) -> UniverseSnapshot:
        index_asset = self.master_data.get_asset(index_asset_id)
        if index_asset is None:
            raise KeyError(f"Unknown index_asset_id: {index_asset_id}")
        if index_asset.asset_type != ASSET_TYPE_INDEX:
            raise ValueError(
                "index_asset_id must identify an index asset"
            )
        records = self.data_fetch.fetch_index_constituents(
            index_asset.standard_symbol,
            source=source,
            timeout_seconds=provider_timeout_seconds,
        )
        return self.import_index_constituents(
            universe_id=universe_id,
            name=name,
            index_asset_id=index_asset_id,
            records=records,
        )

    def import_index_constituents(
        self,
        *,
        universe_id: str,
        name: str,
        index_asset_id: str,
        records: Sequence[IndexConstituent],
        acquired_at: str | None = None,
    ) -> UniverseSnapshot:
        if not records:
            raise ValueError(
                "Index constituent source returned no records"
            )
        dates = {record.effective_date for record in records}
        if len(dates) != 1:
            raise ValueError(
                "One universe snapshot must have one effective_date"
            )
        sources = {record.source for record in records}
        if len(sources) != 1:
            raise ValueError(
                "One universe snapshot must have one source"
            )
        effective_date = next(iter(dates))
        source = next(iter(sources))
        version = _snapshot_version(effective_date, source)
        acquired = acquired_at or datetime.now(
            timezone.utc
        ).isoformat()

        memberships: list[UniverseMembership] = []
        seen: set[str] = set()
        duplicate_count = 0
        for record in records:
            symbol = record.constituent_symbol.strip()
            if symbol in seen:
                duplicate_count += 1
                continue
            seen.add(symbol)
            resolution = self.master_data.resolve_identifier(
                source=source,
                raw_symbol=symbol,
                name=record.constituent_name,
                asset_type_hint=ASSET_TYPE_STOCK,
                market_hint="cn_a",
                exchange_hint=record.exchange,
                raw=record.raw,
                create=bool(re.fullmatch(r"\d{6}", symbol)),
            )
            asset = resolution.asset
            if asset is None or resolution.needs_review:
                status = MembershipStatus.UNRESOLVED
                asset_id = None
                reason = resolution.reason or resolution.status
            elif asset.status != "active":
                status = MembershipStatus.INACTIVE
                asset_id = asset.asset_id
                reason = f"canonical_asset_status:{asset.status}"
            else:
                status = MembershipStatus.RESOLVED
                asset_id = asset.asset_id
                reason = resolution.reason
            memberships.append(
                UniverseMembership(
                    universe_id=universe_id,
                    universe_version=version,
                    source_symbol=symbol,
                    source_name=record.constituent_name,
                    exchange=record.exchange,
                    effective_date=effective_date,
                    status=status,
                    asset_id=asset_id,
                    reason=reason,
                )
            )

        quality = UniverseQualitySummary(
            total_source_records=len(records),
            unique_source_symbols=len(seen),
            resolved_count=sum(
                member.status is MembershipStatus.RESOLVED
                for member in memberships
            ),
            unresolved_count=sum(
                member.status is MembershipStatus.UNRESOLVED
                for member in memberships
            ),
            inactive_count=sum(
                member.status is MembershipStatus.INACTIVE
                for member in memberships
            ),
            duplicate_count=duplicate_count,
        )
        snapshot = UniverseSnapshot(
            universe=ResearchUniverse(
                universe_id=universe_id,
                name=name,
                index_asset_id=index_asset_id,
                source=source,
                version=version,
                acquired_at=acquired,
                effective_date=effective_date,
                historical_membership=False,
            ),
            memberships=tuple(memberships),
            quality=quality,
        )
        return self.repository.save(snapshot)

    def get(
        self,
        universe_id: str,
        *,
        version: str | None = None,
    ) -> UniverseSnapshot | None:
        if version is not None:
            return self.repository.get(universe_id, version)
        return self.repository.latest(universe_id)

    def members(
        self,
        universe_id: str,
        *,
        version: str | None = None,
        for_historical_backtest: bool = False,
        include_unresolved: bool = True,
    ) -> tuple[UniverseMembership, ...]:
        snapshot = self.get(universe_id, version=version)
        if snapshot is None:
            raise KeyError(f"Unknown universe: {universe_id}")
        if (
            for_historical_backtest
            and not snapshot.universe.historical_membership
        ):
            raise HistoricalUniverseUsageError(
                "Current index constituents cannot be used as "
                "historical membership. Load dated historical "
                "constituents before running a historical backtest."
            )
        if include_unresolved:
            return snapshot.memberships
        return tuple(
            member
            for member in snapshot.memberships
            if member.status is not MembershipStatus.UNRESOLVED
        )


def _snapshot_version(effective_date: str, source: str) -> str:
    source_slug = source.replace(":", "-").replace("/", "-")
    return f"{effective_date}:{source_slug}"
