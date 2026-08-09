"""Master data identity resolution for project assets."""

from .models import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    ASSET_TYPE_UNKNOWN,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFIRMED,
    RESOLUTION_CREATED,
    RESOLUTION_DISMISSED,
    RESOLUTION_MATCHED,
    RESOLUTION_UNRESOLVED,
    Asset,
    AssetIdentifier,
    AssetIdentifierRebinding,
    AssetMatchCandidate,
    AssetNameRecord,
    AssetResolution,
    AssetSourceObservation,
    StoredAssetObservation,
)
from .resolver import MasterDataResolver
from .service import MasterDataService
from .sqlite_repository import IdentityMappingConflictError, SQLiteMasterDataRepository

__all__ = [
    "ASSET_TYPE_EXCHANGE_FUND",
    "ASSET_TYPE_INDEX",
    "ASSET_TYPE_OPEN_FUND",
    "ASSET_TYPE_STOCK",
    "ASSET_TYPE_UNKNOWN",
    "RESOLUTION_AMBIGUOUS",
    "RESOLUTION_CONFIRMED",
    "RESOLUTION_CREATED",
    "RESOLUTION_DISMISSED",
    "RESOLUTION_MATCHED",
    "RESOLUTION_UNRESOLVED",
    "Asset",
    "AssetIdentifier",
    "AssetIdentifierRebinding",
    "AssetMatchCandidate",
    "AssetNameRecord",
    "AssetResolution",
    "AssetSourceObservation",
    "StoredAssetObservation",
    "MasterDataResolver",
    "MasterDataService",
    "IdentityMappingConflictError",
    "SQLiteMasterDataRepository",
]
