"""Build point-in-time stock-name snapshots from master data."""

from __future__ import annotations

from decimal import Decimal

from src.master_data import ASSET_TYPE_STOCK, MasterDataService
from src.research_workflow import (
    ResearchProfileRegistry,
    default_profile_registry,
)

from .models import AssetNameAlias, AssetNameSnapshot


class AssetNameSnapshotLoader:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        profile_registry: ResearchProfileRegistry | None = None,
    ) -> None:
        self.master_data = master_data
        self.profile_registry = (
            profile_registry or default_profile_registry()
        )

    def load(
        self,
        *,
        as_of_date: str,
        allow_current_name_fallback: bool = False,
    ) -> tuple[AssetNameSnapshot, ...]:
        records = self.master_data.list_asset_names(
            as_of_date=as_of_date,
        )
        records_by_asset = {}
        for record in records:
            records_by_asset.setdefault(record.asset_id, []).append(record)
        snapshots = []
        for asset in self.master_data.list_assets(
            asset_type=ASSET_TYPE_STOCK
        ):
            name_records = records_by_asset.get(asset.asset_id, [])
            official = sorted(
                (
                    record
                    for record in name_records
                    if record.name_type == "official"
                ),
                key=lambda item: (
                    item.confidence,
                    item.effective_from,
                ),
                reverse=True,
            )
            if official:
                selected = official[0]
                name = selected.name
                effective_from = selected.effective_from
                effective_to = selected.effective_to
                source = selected.source
            elif (
                allow_current_name_fallback
                and asset.status == "active"
                and asset.display_name
            ):
                name = asset.display_name
                effective_from = as_of_date
                effective_to = None
                source = "master_data:current_display_name"
            else:
                continue
            aliases = tuple(
                AssetNameAlias(
                    name=record.name,
                    alias_type=record.name_type,
                    confidence=Decimal(str(record.confidence)),
                    source=record.source,
                )
                for record in name_records
                if record.name != name
            )
            profile = self.profile_registry.default_for_asset(asset)
            snapshots.append(
                AssetNameSnapshot(
                    asset_id=asset.asset_id,
                    standard_symbol=asset.standard_symbol,
                    name=name,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    aliases=aliases,
                    profile_id=profile.profile_id,
                    source=source,
                )
            )
        return tuple(
            sorted(
                snapshots,
                key=lambda item: item.standard_symbol,
            )
        )
