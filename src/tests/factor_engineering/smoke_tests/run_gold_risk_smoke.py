"""Calculate real gold ETF return and risk metrics."""

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
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.factor_engineering import (
        FactorService,
        RiskMetricParameters,
        SQLiteFactorRepository,
    )
    from src.master_data import (
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-risk.sqlite3"
        store = SQLiteDataStore(db_path)
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        response = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master_data,
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
                allow_remote=True,
                remote_sources=(
                    "mootdx",
                    "tencent",
                    "sina",
                    "baostock",
                    "akshare",
                ),
                provider_timeout_seconds=30,
            )
        )
        result = FactorService(
            SQLiteFactorRepository(db_path),
            master_data,
        ).compute_risk_from_response(
            response,
            parameters=RiskMetricParameters(
                annualization_days=252,
                risk_free_rate=0.015,
                rolling_windows=(5, 10),
                price_adjustment="none",
            ),
        )
        metrics = {
            value.factor_name: value.value
            for value in result.metrics
        }
        required = {
            "annualized_return",
            "annualized_volatility",
            "downside_volatility",
            "max_drawdown",
        }
        if not required.issubset(metrics):
            raise RuntimeError(
                f"Missing risk metrics: {sorted(required - metrics.keys())}"
            )
        episode = result.drawdown_episode
        print(
            json.dumps(
                {
                    "asset_id": response.asset.asset_id,
                    "record_count": len(response.records),
                    "sources": response.sources,
                    "metrics": metrics,
                    "drawdown_episode": {
                        "peak_date": episode.peak_date,
                        "trough_date": episode.trough_date,
                        "recovery_date": episode.recovery_date,
                        "duration_days": episode.duration_days,
                    },
                    "series_count": len(result.series),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
