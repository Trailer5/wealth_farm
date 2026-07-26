"""Models for deterministic daily_data publication tasks."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.master_data import Asset


@dataclass(frozen=True)
class TrackedAsset:
    asset_id: str
    standard_symbol: str
    display_name: str
    asset_type: str
    market: str
    exchange: str
    dataset: str
    initial_start_date: str
    overlap_days: int = 7
    enabled: bool = True
    remote_sources: tuple[str, ...] = ()
    provider_timeout_seconds: float = 30.0

    def to_asset(self) -> Asset:
        return Asset(
            asset_id=self.asset_id,
            asset_type=self.asset_type,
            standard_symbol=self.standard_symbol,
            display_name=self.display_name,
            market=self.market,
            exchange=self.exchange,
        )


@dataclass(frozen=True)
class DailyDataWriteResult:
    asset_id: str
    dataset: str
    input_record_count: int
    changed_paths: tuple[str, ...] = ()
    coverage_start: str = ""
    coverage_end: str = ""


@dataclass(frozen=True)
class AssetUpdateResult:
    asset_id: str
    dataset: str
    start_date: str
    end_date: str
    record_count: int
    sources: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchUpdateResult:
    end_date: str
    assets: tuple[AssetUpdateResult, ...] = ()
    skipped_asset_ids: tuple[str, ...] = ()
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return not self.failures
