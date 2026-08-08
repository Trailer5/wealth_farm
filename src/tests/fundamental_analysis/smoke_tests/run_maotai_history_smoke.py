"""Compute real point-in-time Maotai fundamental history."""

from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DATASET_STOCK_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.financial_data import SQLiteFinancialDataRepository
    from src.fundamental_analysis import (
        FundamentalAnalysisService,
        SQLiteFundamentalMetricRepository,
    )
    from src.master_data import (
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "maotai-history.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = master.register_asset(
            Asset(
                asset_id="stock:maotai-history",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="history-smoke",
            raw_symbol="600519",
        )
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master,
            ),
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        cutoffs = ("2025-04-30", "2026-04-30")
        bars = data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2024-01-01",
                end_date=cutoffs[-1],
                as_of_date=cutoffs[-1],
                allow_remote=True,
                remote_sources=("mootdx", "tencent", "sina"),
                provider_timeout_seconds=60,
            )
        )
        market_prices = {
            cutoff: Decimal(
                str(
                    [
                        row
                        for row in bars.records
                        if row["trade_date"] <= cutoff
                    ][-1]["close"]
                )
            )
            for cutoff in cutoffs
        }
        bundle = data_access.get_financial_bundle(
            asset_id=asset.asset_id,
            start_date="2022-01-01",
            end_date="2026-06-30",
            as_of_date="2026-08-03",
            allow_remote=True,
            provider_timeout_seconds=120,
        )
        service = FundamentalAnalysisService(
            master_data=master,
            repository=SQLiteFundamentalMetricRepository(db_path),
        )
        history = service.analyze_history(
            bundle,
            market_prices_by_cutoff=market_prices,
        )
        if len(history) != 2:
            raise AssertionError("Expected two historical snapshots")
        if history[0].snapshot.report_period == (
            history[1].snapshot.report_period
        ):
            raise AssertionError(
                "Historical cutoffs used the same report period"
            )
        output = []
        for item in history:
            metrics = item.by_name()
            output.append(
                {
                    "as_of_date": item.snapshot.as_of_date,
                    "report_period": item.snapshot.report_period,
                    "flow_basis": item.snapshot.flow_basis,
                    "market_price": str(
                        item.snapshot.market_price
                    ),
                    "pe_ttm": (
                        str(metrics["pe_ttm"].value)
                        if metrics["pe_ttm"].value is not None
                        else None
                    ),
                    "roe": (
                        str(metrics["roe"].value)
                        if metrics["roe"].value is not None
                        else None
                    ),
                }
            )
        print(
            json.dumps(
                {
                    "period_summary": {
                        "latest_annual": (
                            bundle.period_summary()
                            .latest_annual_period
                        ),
                        "latest_interim": (
                            bundle.period_summary()
                            .latest_interim_period
                        ),
                    },
                    "history": output,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
