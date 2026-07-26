"""Validate unified access with a real gold ETF and temporary SQLite."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one gold ETF through the unified data access layer."
    )
    parser.add_argument("--symbol", default="518880", help="Gold ETF symbol.")
    parser.add_argument("--name", default="黄金ETF", help="Display name hint.")
    parser.add_argument("--start-date", default="2026-07-01")
    parser.add_argument("--end-date", default="2026-07-24")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["baostock", "akshare"],
        default=["akshare", "baostock"],
        help="Ordered ETF daily providers used for automatic fallback.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        DailyDataReader,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.service import DataFetchService
    from src.data_quality import DataQualityService, SQLiteDataQualityRepository
    from src.data_store import SQLiteDataStore
    from src.master_data import MasterDataService, SQLiteMasterDataRepository

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-etf-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch_service = DataFetchService(
            store=store,
            master_data_service=master_data,
        )
        data_access = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch_service,
            daily_data_reader=DailyDataReader(PROJECT_ROOT / "data" / "daily_data"),
            trading_calendar=TradingCalendarService(
                repository=SQLiteDataAccessRepository(store),
                fetch_service=fetch_service,
            ),
        )
        response = data_access.get(
            DataAccessRequest(
                asset=AssetInput(
                    raw_symbol=args.symbol,
                    name=args.name,
                    asset_type_hint="ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date=args.start_date,
                end_date=args.end_date,
                remote_sources=tuple(args.sources),
            )
        )

        if not response.records:
            raise RuntimeError("Unified data access returned no gold ETF records")
        quality = DataQualityService(SQLiteDataQualityRepository(db_path))
        quality_summary = quality.evaluate_data_access_response(
            task_name="gold-etf-smoke",
            response=response,
            expected_start=args.start_date,
            expected_end=str(
                response.metadata.get("effective_end_date") or args.end_date
            ),
        )
        result = {
            "asset_id": response.asset.asset_id,
            "asset_type": response.asset.asset_type,
            "symbol": response.asset.standard_symbol,
            "name": response.asset.display_name,
            "exchange": response.asset.exchange,
            "record_count": len(response.records),
            "first_trade_date": response.records[0]["trade_date"],
            "last_trade_date": response.records[-1]["trade_date"],
            "last_close": response.records[-1]["close"],
            "sources": response.sources,
            "requested_sources": args.sources,
            "provider_errors": response.metadata.get("provider_errors"),
            "backends": response.metadata.get("backends"),
            "effective_end_date": response.metadata.get("effective_end_date"),
            "trading_calendar": response.metadata.get("trading_calendar"),
            "fetched_remote": response.fetched_remote,
            "complete": response.complete,
            "quality": {
                "pass_count": quality_summary.pass_count,
                "warn_count": quality_summary.warn_count,
                "fail_count": quality_summary.fail_count,
                "can_publish": quality_summary.can_publish,
            },
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
