"""Validate one direct stock and ETF daily-bar provider."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROVIDERS = ("tencent", "sina", "mootdx")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a direct stock and ETF daily-bar provider.",
    )
    parser.add_argument("provider", choices=PROVIDERS)
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        DATASET_STOCK_DAILY_BARS,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import (
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{args.provider}-kline-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch = DataFetchService(
            store=store,
            master_data_service=master,
        )
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
            "stock": _request(
                provider=args.provider,
                dataset=DATASET_STOCK_DAILY_BARS,
                raw_symbol="600519",
                name="贵州茅台",
                asset_type_hint="股票",
            ),
            "etf": _request(
                provider=args.provider,
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                raw_symbol="510300",
                name="沪深300ETF",
                asset_type_hint="ETF",
            ),
        }
        responses = {
            name: access.get(request)
            for name, request in requests.items()
        }
        result = {
            name: {
                "asset_type": response.asset.asset_type,
                "record_count": len(response.records),
                "first_trade_date": response.records[0]["trade_date"],
                "last_trade_date": response.records[-1]["trade_date"],
                "last_close": response.records[-1]["close"],
                "source": response.sources[0],
            }
            for name, response in responses.items()
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def _request(
    *,
    provider: str,
    dataset: str,
    raw_symbol: str,
    name: str,
    asset_type_hint: str,
):
    from src.data_access import AssetInput, DataAccessRequest

    timeout = 20 if provider == "mootdx" else 30
    return DataAccessRequest(
        asset=AssetInput(
            raw_symbol=raw_symbol,
            name=name,
            asset_type_hint=asset_type_hint,
        ),
        dataset=dataset,
        start_date="2026-07-01",
        end_date="2026-07-24",
        remote_sources=(provider,),
        provider_timeout_seconds=timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
