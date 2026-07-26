"""Calculate and persist baseline factors for a real gold ETF."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_quality import DataQualityService, SQLiteDataQualityRepository
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.factor_engineering import FactorService, SQLiteFactorRepository
    from src.master_data import MasterDataService, SQLiteMasterDataRepository

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-factor-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch_service = DataFetchService(
            store=store,
            master_data_service=master_data,
        )
        access_repository = SQLiteDataAccessRepository(store)
        response = DataAccessService(
            master_data=master_data,
            repository=access_repository,
            fetch_service=fetch_service,
            trading_calendar=TradingCalendarService(
                repository=access_repository,
                fetch_service=fetch_service,
            ),
        ).get(
            DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="518880",
                    name="黄金ETF",
                    asset_type_hint="ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                remote_sources=("akshare", "baostock"),
            )
        )
        quality = DataQualityService(SQLiteDataQualityRepository(db_path))
        quality_summary = quality.evaluate_data_access_response(
            task_name="gold-factor-smoke",
            response=response,
            expected_start="2026-07-01",
            expected_end="2026-07-24",
        )
        if not quality_summary.can_publish:
            raise RuntimeError("Gold ETF input failed the quality gate")

        factors = FactorService(
            SQLiteFactorRepository(db_path),
            master_data,
        )
        values = factors.compute_from_response(response)
        stored = factors.list_values(
            response.asset.asset_id,
            as_of_date=response.records[-1]["trade_date"],
        )
        if len(stored) != 5:
            raise RuntimeError(f"Expected 5 factors, got {len(stored)}")
        result = {
            "asset_id": response.asset.asset_id,
            "record_count": len(response.records),
            "as_of_date": response.records[-1]["trade_date"],
            "quality_can_publish": quality_summary.can_publish,
            "factors": {
                value.factor_name: value.value
                for value in values
            },
            "stored_count": len(stored),
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
