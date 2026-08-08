"""Fetch one real Maotai financial statement through unified access."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_STOCK_INCOME_STATEMENTS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.financial_data import SQLiteFinancialDataRepository
    from src.master_data import (
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "financial-smoke.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = master_data.register_asset(
            Asset(
                asset_id="asset:smoke-maotai",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="financial-smoke",
            raw_symbol="600519",
        )
        store = SQLiteDataStore(db_path)
        service = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master_data,
            ),
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        response = service.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_INCOME_STATEMENTS,
                start_date="2024-01-01",
                end_date="2026-12-31",
                as_of_date="2026-08-03",
                allow_remote=True,
                allow_stale_on_error=False,
                remote_sources=("akshare",),
                provider_timeout_seconds=90,
            )
        )
        if not response.records:
            raise RuntimeError(
                "Real financial smoke returned no normalized records"
            )
        payload = {
            "asset_id": response.asset.asset_id,
            "dataset": response.dataset,
            "record_count": len(response.records),
            "periods": sorted(
                {
                    str(record["report_period"])
                    for record in response.records
                }
            ),
            "sources": response.sources,
            "complete": response.complete,
            "fetched_remote": response.fetched_remote,
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
