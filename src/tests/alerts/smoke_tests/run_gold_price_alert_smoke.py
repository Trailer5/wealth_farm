"""Trigger and acknowledge a local alert from a real gold ETF price."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.alerts import (
        RULE_PRICE_ABOVE,
        AlertService,
        SQLiteAlertRepository,
    )
    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import MasterDataService, SQLiteMasterDataRepository
    from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-alert-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch = DataFetchService(store=store, master_data_service=master)
        access_repository = SQLiteDataAccessRepository(store)
        response = DataAccessService(
            master_data=master,
            repository=access_repository,
            fetch_service=fetch,
            trading_calendar=TradingCalendarService(
                repository=access_repository,
                fetch_service=fetch,
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
        latest = response.records[-1]
        price = float(latest["close"])
        observed_at = str(latest["trade_date"])

        users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        context = users.default_context()
        alerts = AlertService(
            SQLiteAlertRepository(db_path),
            users,
            master,
        )
        rule = alerts.create_price_rule(
            context,
            asset_id=response.asset.asset_id,
            rule_type=RULE_PRICE_ABOVE,
            threshold=8.0,
        )
        first = alerts.evaluate_price(
            context,
            asset_id=response.asset.asset_id,
            price=price,
            observed_at=observed_at,
        )
        second = alerts.evaluate_price(
            context,
            asset_id=response.asset.asset_id,
            price=price,
            observed_at=observed_at,
        )
        if len(first) != 1 or second:
            raise RuntimeError("Price alert deduplication failed")
        acknowledged = alerts.acknowledge(context, first[0].alert_id)

        result = {
            "asset_id": response.asset.asset_id,
            "trade_date": observed_at,
            "price": price,
            "rule_id": rule.rule_id,
            "alert_id": first[0].alert_id,
            "duplicate_alert_count": len(second),
            "status": acknowledged.status,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
