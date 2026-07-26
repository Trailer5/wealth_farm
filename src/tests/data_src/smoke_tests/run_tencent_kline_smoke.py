"""Validate Tencent direct historical K-line for stock and ETF."""

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
        DATASET_STOCK_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import MasterDataService, SQLiteMasterDataRepository

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "tencent-kline-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch = DataFetchService(store=store, master_data_service=master)
        repository = SQLiteDataAccessRepository(store)
        access = DataAccessService(
            master_data=master,
            repository=repository,
            fetch_service=fetch,
            trading_calendar=TradingCalendarService(
                repository=repository,
                fetch_service=fetch,
            ),
        )
        requests = {
            "stock": DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="600519",
                    name="贵州茅台",
                    asset_type_hint="股票",
                ),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                remote_sources=("tencent",),
            ),
            "etf": DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="510300",
                    name="沪深300ETF",
                    asset_type_hint="ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                remote_sources=("tencent",),
            ),
        }
        responses = {
            name: access.get(request)
            for name, request in requests.items()
        }
        result = {
            name: {
                "record_count": len(response.records),
                "last_trade_date": response.records[-1]["trade_date"],
                "last_close": response.records[-1]["close"],
                "source": response.sources[0],
            }
            for name, response in responses.items()
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
