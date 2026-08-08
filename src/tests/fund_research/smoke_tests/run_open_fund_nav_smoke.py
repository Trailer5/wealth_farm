"""Fetch one real open-fund NAV series through unified access."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_OPEN_FUND_NAV,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import (
        ASSET_TYPE_OPEN_FUND,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "fund-smoke.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = master_data.register_asset(
            Asset(
                asset_id="asset:smoke-open-fund",
                asset_type=ASSET_TYPE_OPEN_FUND,
                standard_symbol="000001",
                display_name="场外基金样本",
                market="cn_fund",
            ),
            source="fund-smoke",
            raw_symbol="000001",
        )
        store = SQLiteDataStore(db_path)
        response = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master_data,
            ),
        ).get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_OPEN_FUND_NAV,
                start_date="2025-01-01",
                end_date="2026-08-03",
                as_of_date="2026-08-03",
                allow_remote=True,
                remote_sources=("akshare",),
                provider_timeout_seconds=60,
            )
        )
        if not response.records:
            raise RuntimeError("Real open-fund NAV returned no records")
        if any(record["estimated"] for record in response.records):
            raise RuntimeError(
                "Official NAV dataset contained estimated values"
            )
        print(
            json.dumps(
                {
                    "asset_id": asset.asset_id,
                    "record_count": len(response.records),
                    "coverage_start": response.coverage_start,
                    "coverage_end": response.coverage_end,
                    "sources": response.sources,
                    "complete": response.complete,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
