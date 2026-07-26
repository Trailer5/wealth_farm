"""Load and validate the shared tracked-asset manifest."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
)
from src.master_data import ASSET_TYPE_EXCHANGE_FUND, ASSET_TYPE_STOCK

from .models import TrackedAsset


class TrackedAssetManifestError(ValueError):
    """Raised when a shared automation manifest is invalid."""


def load_tracked_assets(path: str | Path) -> tuple[TrackedAsset, ...]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise TrackedAssetManifestError(
            f"Tracked asset manifest does not exist: {manifest_path}"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise TrackedAssetManifestError(
            "tracked_assets schema_version must be 1"
        )
    items = payload.get("assets")
    if not isinstance(items, list):
        raise TrackedAssetManifestError("tracked_assets assets must be a list")

    tracked: list[TrackedAsset] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TrackedAssetManifestError(
                f"tracked_assets item {index} must be an object"
            )
        tracked_asset = _tracked_asset_from_dict(item, index)
        if tracked_asset.asset_id in seen_ids:
            raise TrackedAssetManifestError(
                f"Duplicate tracked asset_id: {tracked_asset.asset_id}"
            )
        seen_ids.add(tracked_asset.asset_id)
        tracked.append(tracked_asset)
    return tuple(tracked)


def _tracked_asset_from_dict(
    item: dict[str, object],
    index: int,
) -> TrackedAsset:
    def text(field: str) -> str:
        value = str(item.get(field) or "").strip()
        if not value:
            raise TrackedAssetManifestError(
                f"tracked_assets item {index} requires {field}"
            )
        return value

    asset_id = text("asset_id")
    standard_symbol = text("standard_symbol")
    display_name = text("display_name")
    asset_type = text("asset_type")
    market = text("market")
    exchange = text("exchange")
    dataset = text("dataset")
    initial_start_date = text("initial_start_date")
    for field, value in (
        ("standard_symbol", standard_symbol),
        ("market", market),
        ("exchange", exchange),
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise TrackedAssetManifestError(
                f"Unsafe {field} in tracked asset {asset_id}: {value}"
            )
    try:
        date.fromisoformat(initial_start_date)
    except ValueError as exc:
        raise TrackedAssetManifestError(
            f"Invalid initial_start_date for {asset_id}"
        ) from exc

    expected_dataset = {
        ASSET_TYPE_STOCK: DATASET_STOCK_DAILY_BARS,
        ASSET_TYPE_EXCHANGE_FUND: (
            DATASET_EXCHANGE_FUND_DAILY_BARS
        ),
    }.get(asset_type)
    if expected_dataset is None:
        raise TrackedAssetManifestError(
            f"Unsupported asset_type for {asset_id}: {asset_type}"
        )
    if dataset != expected_dataset:
        raise TrackedAssetManifestError(
            f"Dataset {dataset} does not match {asset_type} for {asset_id}"
        )

    overlap_days = int(item.get("overlap_days", 7))
    if overlap_days < 0 or overlap_days > 365:
        raise TrackedAssetManifestError(
            f"overlap_days must be between 0 and 365 for {asset_id}"
        )
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TrackedAssetManifestError(
            f"enabled must be boolean for {asset_id}"
        )
    remote_sources_value = item.get("remote_sources", [])
    if not isinstance(remote_sources_value, list):
        raise TrackedAssetManifestError(
            f"remote_sources must be a list for {asset_id}"
        )
    remote_sources = tuple(
        str(source).strip()
        for source in remote_sources_value
        if str(source).strip()
    )
    provider_timeout_seconds = float(
        item.get("provider_timeout_seconds", 30.0)
    )
    if provider_timeout_seconds <= 0 or provider_timeout_seconds > 300:
        raise TrackedAssetManifestError(
            f"provider_timeout_seconds is invalid for {asset_id}"
        )
    return TrackedAsset(
        asset_id=asset_id,
        standard_symbol=standard_symbol,
        display_name=display_name,
        asset_type=asset_type,
        market=market,
        exchange=exchange,
        dataset=dataset,
        initial_start_date=initial_start_date,
        overlap_days=overlap_days,
        enabled=enabled,
        remote_sources=remote_sources,
        provider_timeout_seconds=provider_timeout_seconds,
    )
