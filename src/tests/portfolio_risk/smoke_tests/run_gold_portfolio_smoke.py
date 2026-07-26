"""Value a real gold ETF holding through the manual research stack."""

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
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import MasterDataService, SQLiteMasterDataRepository
    from src.portfolio_risk import (
        PortfolioRiskService,
        SQLitePortfolioRepository,
    )
    from src.research_state import (
        ResearchStateService,
        SQLiteResearchStateRepository,
    )
    from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-portfolio-smoke.sqlite3"
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
        last_price = float(response.records[-1]["close"])

        users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        context = users.default_context()
        research = ResearchStateService(
            SQLiteResearchStateRepository(db_path),
            users,
            master,
        )
        research.upsert_holding(
            context,
            asset_id=response.asset.asset_id,
            quantity=100,
            average_cost=8.1,
            note="黄金ETF组合估值验证",
        )
        portfolio = PortfolioRiskService(
            SQLitePortfolioRepository(db_path),
            research,
        )
        valuation = portfolio.value_current_holdings(
            context,
            as_of_date=str(response.records[-1]["trade_date"]),
            prices={response.asset.asset_id: last_price},
        )
        reloaded = portfolio.get_snapshot(
            context,
            valuation.snapshot.snapshot_id,
        )
        if reloaded is None:
            raise RuntimeError("Portfolio snapshot was not persisted")

        result = {
            "asset_id": response.asset.asset_id,
            "as_of_date": valuation.snapshot.as_of_date,
            "market_price": last_price,
            "quantity": valuation.positions[0].quantity,
            "total_market_value": valuation.snapshot.total_market_value,
            "total_cost_value": valuation.snapshot.total_cost_value,
            "unrealized_pnl": valuation.snapshot.unrealized_pnl,
            "max_weight": valuation.snapshot.max_weight,
            "concentration_hhi": valuation.snapshot.concentration_hhi,
            "reloaded_position_count": len(reloaded.positions),
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
