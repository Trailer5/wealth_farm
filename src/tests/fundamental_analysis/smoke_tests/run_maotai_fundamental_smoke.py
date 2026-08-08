"""Calculate real Maotai valuation and financial-quality metrics."""

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
        db_path = Path(temp_dir) / "fundamental-smoke.sqlite3"
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
            source="fundamental-smoke",
            raw_symbol="600519",
        )
        store = SQLiteDataStore(db_path)
        data_access = DataAccessService(
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
        bars = data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
                allow_remote=True,
                remote_sources=("mootdx", "tencent", "sina"),
                provider_timeout_seconds=30,
            )
        )
        financials = data_access.get_financial_bundle(
            asset_id=asset.asset_id,
            start_date="2023-01-01",
            end_date="2026-06-30",
            as_of_date="2026-08-03",
            allow_remote=True,
            provider_timeout_seconds=90,
        )
        if not financials.complete:
            raise RuntimeError(
                f"Financial bundle failed: {financials.errors}"
            )
        last_close = Decimal(str(bars.records[-1]["close"]))
        result = FundamentalAnalysisService(
            master_data=master_data,
            repository=SQLiteFundamentalMetricRepository(
                db_path
            ),
        ).analyze(
            financials,
            market_price=last_close,
        )
        metrics = result.by_name()
        required = {"market_cap", "pe_ttm", "pb", "ps_ttm", "roe"}
        unavailable = {
            name: metrics[name].reason
            for name in required
            if metrics[name].value is None
        }
        if unavailable:
            balance_fields = sorted(
                {
                    str(record["field_label"])
                    for record in financials.records_by_dataset[
                        "stock_balance_sheets"
                    ]
                    if "SHARE" in str(record["field_label"]).upper()
                    or "股" in str(record["field_label"])
                }
            )
            raise RuntimeError(
                f"Required real metrics unavailable: {unavailable}; "
                f"share fields: {balance_fields}"
            )
        print(
            json.dumps(
                {
                    "asset_id": asset.asset_id,
                    "market_price": format(last_close, "f"),
                    "report_period": result.snapshot.report_period,
                    "available_metrics": {
                        name: format(metric.value, "f")
                        for name, metric in metrics.items()
                        if metric.value is not None
                    },
                    "unavailable_metrics": {
                        name: metric.reason
                        for name, metric in metrics.items()
                        if metric.value is None
                    },
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
