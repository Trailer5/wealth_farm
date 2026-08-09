"""Persistent service boundary for master data identity resolution."""

from __future__ import annotations

from src.data_src.models import SecurityInfo

from .models import (
    RESOLUTION_CREATED,
    RESOLUTION_MATCHED,
    Asset,
    AssetIdentifier,
    AssetIdentifierRebinding,
    AssetNameRecord,
    AssetResolution,
    AssetSourceObservation,
    StoredAssetObservation,
)
from .resolver import MasterDataResolver
from .sqlite_repository import SQLiteMasterDataRepository


class MasterDataService:
    """Resolve identities and persist every source observation."""

    def __init__(self, repository: SQLiteMasterDataRepository | None = None) -> None:
        self.repository = repository or SQLiteMasterDataRepository()
        self.repository.initialize()
        self._reload_resolver()

    def _reload_resolver(self) -> None:
        self.resolver = MasterDataResolver(
            assets=self.repository.list_assets(),
            identifiers=self.repository.list_identifiers(),
        )

    def resolve_identifier(
        self,
        source: str,
        raw_symbol: str,
        name: str | None = None,
        asset_type_hint: str | None = None,
        market_hint: str | None = None,
        exchange_hint: str | None = None,
        raw: dict[str, object] | None = None,
        create: bool = True,
    ) -> AssetResolution:
        """Resolve and persist one source identity observation."""

        resolution = self.resolver.resolve_identifier(
            source=source,
            raw_symbol=raw_symbol,
            name=name,
            asset_type_hint=asset_type_hint,
            market_hint=market_hint,
            exchange_hint=exchange_hint,
            raw=raw,
            create=create,
        )
        self.repository.save_resolution(resolution)
        return resolution

    def ingest_security_info(self, security: SecurityInfo, create: bool = True) -> AssetResolution:
        """Resolve and persist a normalized provider security record."""

        return self.resolve_identifier(
            source=security.source,
            raw_symbol=security.symbol,
            name=security.name,
            asset_type_hint=security.security_type,
            exchange_hint=security.exchange,
            raw=security.raw,
            create=create,
        )

    def get_asset(self, asset_id: str) -> Asset | None:
        """Return one persisted canonical asset."""

        return self.repository.get_asset(asset_id)

    def register_asset(
        self,
        asset: Asset,
        *,
        source: str,
        raw_symbol: str,
    ) -> Asset:
        """Register a canonical asset supplied by a trusted shared catalog."""

        canonical_matches = self.repository.find_assets(
            standard_symbol=asset.standard_symbol,
            asset_type=asset.asset_type,
        )
        conflicting = [
            match
            for match in canonical_matches
            if match.asset_id != asset.asset_id
            and match.market == asset.market
            and match.exchange == asset.exchange
        ]
        if conflicting:
            raise ValueError(
                f"Canonical identity already uses another asset_id: "
                f"{conflicting[0].asset_id}"
            )
        existing = self.repository.get_asset(asset.asset_id)
        identifier = AssetIdentifier(
            asset_id=asset.asset_id,
            source=source,
            raw_symbol=raw_symbol,
            standard_symbol=asset.standard_symbol,
            name=asset.display_name,
            asset_type=asset.asset_type,
            market=asset.market,
            exchange=asset.exchange,
            confidence=1.0,
        )
        resolution = AssetResolution(
            status=RESOLUTION_MATCHED if existing is not None else RESOLUTION_CREATED,
            observation=AssetSourceObservation(
                source=source,
                raw_symbol=raw_symbol,
                standard_symbol=asset.standard_symbol,
                name=asset.display_name,
                asset_type_hint=asset.asset_type,
                market_hint=asset.market,
                exchange_hint=asset.exchange,
                raw={"catalog": source},
            ),
            asset=asset,
            identifier=identifier,
            confidence=1.0,
            reason="registered_trusted_catalog_asset",
        )
        self.repository.save_resolution(resolution)
        self._reload_resolver()
        return asset

    def find_assets(
        self,
        *,
        standard_symbol: str | None = None,
        source: str | None = None,
        raw_symbol: str | None = None,
        name: str | None = None,
        asset_type: str | None = None,
    ) -> list[Asset]:
        """Find persisted assets by canonical or source identity."""

        return self.repository.find_assets(
            standard_symbol=standard_symbol,
            source=source,
            raw_symbol=raw_symbol,
            name=name,
            asset_type=asset_type,
        )

    def list_assets(self, *, asset_type: str | None = None) -> list[Asset]:
        """Return canonical assets through the public service boundary."""

        return self.repository.find_assets(asset_type=asset_type)

    def register_asset_name(
        self,
        record: AssetNameRecord,
    ) -> AssetNameRecord:
        """Register an official historical name or reviewed alias."""

        if self.get_asset(record.asset_id) is None:
            raise KeyError(f"Unknown asset_id: {record.asset_id}")
        return self.repository.upsert_asset_name(record)

    def list_asset_names(
        self,
        *,
        asset_id: str | None = None,
        as_of_date: str | None = None,
    ) -> list[AssetNameRecord]:
        return self.repository.list_asset_names(
            asset_id=asset_id,
            as_of_date=as_of_date,
        )

    def list_unresolved_observations(self) -> list[StoredAssetObservation]:
        """Return persisted observations that still require review."""

        return self.repository.list_unresolved_observations()

    def get_observation(self, observation_id: int) -> StoredAssetObservation | None:
        """Return one persisted source observation."""

        return self.repository.get_observation(observation_id)

    def confirm_observation(
        self,
        observation_id: int,
        asset_id: str,
        reason: str = "user_confirmed",
    ) -> AssetIdentifier:
        """Confirm an unresolved observation against an existing asset."""

        identifier = self.repository.confirm_observation(
            observation_id=observation_id,
            asset_id=asset_id,
            reason=reason,
        )
        self._reload_resolver()
        return identifier

    def dismiss_observation(
        self,
        observation_id: int,
        reason: str = "user_dismissed",
    ) -> StoredAssetObservation:
        """Dismiss an unresolved observation."""

        return self.repository.dismiss_observation(observation_id, reason)

    def rebind_identifier(
        self,
        source: str,
        raw_symbol: str,
        new_asset_id: str,
        reason: str,
    ) -> AssetIdentifier:
        """Explicitly correct a source identifier mapping."""

        identifier = self.repository.rebind_identifier(
            source=source,
            raw_symbol=raw_symbol,
            new_asset_id=new_asset_id,
            reason=reason,
        )
        self._reload_resolver()
        return identifier

    def list_rebindings(self) -> list[AssetIdentifierRebinding]:
        """Return explicit source identifier corrections."""

        return self.repository.list_rebindings()
