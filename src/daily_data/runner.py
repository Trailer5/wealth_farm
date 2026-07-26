"""Batch entry point for quality-gated daily_data updates."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.data_access import (
    AssetInput,
    DataAccessRequest,
    DataAccessService,
    DailyDataReader,
    SQLiteDataAccessRepository,
    TradingCalendarService,
)
from src.data_quality import (
    DataQualityService,
    SQLiteDataQualityRepository,
)
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore
from src.master_data import MasterDataService, SQLiteMasterDataRepository

from .manifest import load_tracked_assets
from .models import (
    AssetUpdateResult,
    BatchUpdateResult,
    TrackedAsset,
)
from .writer import DailyDataWriter


class DailyDataUpdateRunner:
    """Update every enabled shared asset without publishing user state."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        data_quality: DataQualityService,
        writer: DailyDataWriter,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.data_quality = data_quality
        self.writer = writer

    def run(
        self,
        tracked_assets: tuple[TrackedAsset, ...],
        *,
        end_date: str,
        checked_at: str | None = None,
    ) -> BatchUpdateResult:
        target_end = date.fromisoformat(end_date)
        checked = checked_at or datetime.now(timezone.utc).isoformat()
        updates: list[AssetUpdateResult] = []
        skipped: list[str] = []
        failures: dict[str, str] = {}

        for tracked in tracked_assets:
            if not tracked.enabled:
                skipped.append(tracked.asset_id)
                continue
            try:
                asset = self.master_data.register_asset(
                    tracked.to_asset(),
                    source="daily_data:tracked_assets",
                    raw_symbol=tracked.standard_symbol,
                )
                start = self._start_date(tracked, asset)
                if start > target_end:
                    skipped.append(tracked.asset_id)
                    continue
                request = DataAccessRequest(
                    asset=AssetInput(asset_id=asset.asset_id),
                    dataset=tracked.dataset,
                    start_date=start.isoformat(),
                    end_date=target_end.isoformat(),
                    max_age_seconds=0,
                    allow_remote=True,
                    allow_stale_on_error=False,
                    remote_sources=tracked.remote_sources,
                    provider_timeout_seconds=(
                        tracked.provider_timeout_seconds
                    ),
                )
                response = self.data_access.get(request)
                quality = (
                    self.data_quality.evaluate_data_access_response(
                        task_name="daily_data_scheduled_update",
                        response=response,
                        expected_start=request.start_date,
                        expected_end=request.end_date,
                    )
                )
                written = self.writer.publish_daily_bars(
                    response,
                    quality,
                    checked_at=checked,
                )
                updates.append(
                    AssetUpdateResult(
                        asset_id=asset.asset_id,
                        dataset=tracked.dataset,
                        start_date=request.start_date,
                        end_date=(
                            response.coverage_end
                            or request.end_date
                        ),
                        record_count=len(response.records),
                        sources=response.sources,
                        changed_paths=written.changed_paths,
                    )
                )
            except Exception as exc:
                failures[tracked.asset_id] = (
                    f"{type(exc).__name__}: {exc}"
                )
        return BatchUpdateResult(
            end_date=end_date,
            assets=tuple(updates),
            skipped_asset_ids=tuple(skipped),
            failures=failures,
        )

    def _start_date(self, tracked: TrackedAsset, asset) -> date:
        initial = date.fromisoformat(tracked.initial_start_date)
        coverage_end = self.writer.latest_coverage_end(
            asset,
            tracked.dataset,
        )
        if coverage_end is None:
            return initial
        overlap_start = date.fromisoformat(coverage_end) - timedelta(
            days=tracked.overlap_days
        )
        return max(initial, overlap_start)


def build_runner(
    *,
    database_path: str | Path,
    daily_data_root: str | Path,
) -> DailyDataUpdateRunner:
    db_path = Path(database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteDataStore(db_path)
    master_data = MasterDataService(
        SQLiteMasterDataRepository(db_path)
    )
    fetch_service = DataFetchService(
        store=store,
        master_data_service=master_data,
    )
    repository = SQLiteDataAccessRepository(store)
    data_access = DataAccessService(
        master_data=master_data,
        repository=repository,
        fetch_service=fetch_service,
        daily_data_reader=DailyDataReader(daily_data_root),
        trading_calendar=TradingCalendarService(
            repository=repository,
            fetch_service=fetch_service,
        ),
    )
    return DailyDataUpdateRunner(
        master_data=master_data,
        data_access=data_access,
        data_quality=DataQualityService(
            SQLiteDataQualityRepository(db_path)
        ),
        writer=DailyDataWriter(daily_data_root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update quality-gated shared daily market data."
    )
    parser.add_argument(
        "--daily-data-root",
        default="data/daily_data",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Defaults to <daily-data-root>/manifests/tracked_assets.json",
    )
    parser.add_argument(
        "--database",
        default="temp/daily_data_update.sqlite3",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
    )
    args = parser.parse_args(argv)

    root = Path(args.daily_data_root)
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else root / "manifests" / "tracked_assets.json"
    )
    tracked_assets = load_tracked_assets(manifest_path)
    if not tracked_assets:
        result = BatchUpdateResult(end_date=args.end_date)
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0

    runner = build_runner(
        database_path=args.database,
        daily_data_root=root,
    )
    result = runner.run(
        tracked_assets,
        end_date=args.end_date,
    )
    print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
    return 0 if result.successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
