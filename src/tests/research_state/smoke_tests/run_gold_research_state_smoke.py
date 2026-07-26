"""Run a complete manual research-state flow with a real gold ETF."""

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
    from src.master_data import MasterDataService, SQLiteMasterDataRepository
    from src.research_state import (
        ResearchStateService,
        SQLiteResearchStateRepository,
    )
    from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "manual-research-smoke.sqlite3"
        store = SQLiteDataStore(db_path)
        master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch_service = DataFetchService(
            store=store,
            master_data_service=master_data,
        )
        access_repository = SQLiteDataAccessRepository(store)
        data_access = DataAccessService(
            master_data=master_data,
            repository=access_repository,
            fetch_service=fetch_service,
            trading_calendar=TradingCalendarService(
                repository=access_repository,
                fetch_service=fetch_service,
            ),
        )
        response = data_access.get(
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
            task_name="gold-manual-research",
            response=response,
            expected_start="2026-07-01",
            expected_end="2026-07-24",
        )
        if not quality_summary.can_publish:
            raise RuntimeError("Gold ETF data failed the quality gate")

        user_space = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        context = user_space.default_context()
        research = ResearchStateService(
            SQLiteResearchStateRepository(db_path),
            user_space,
            master_data,
        )
        item = research.add_watchlist_item(
            context,
            asset_id=response.asset.asset_id,
            reason="验证黄金资产的手动研究流程",
            tags=("黄金", "ETF"),
        )
        last_close = float(response.records[-1]["close"])
        position = research.upsert_holding(
            context,
            asset_id=response.asset.asset_id,
            quantity=100,
            average_cost=last_close,
            note="smoke 测试持仓",
        )
        report = research.save_report(
            context,
            asset_id=response.asset.asset_id,
            title="黄金ETF手动研究流程验证",
            summary="真实数据、质量检查和研究状态持久化均通过。",
            content_markdown=(
                "# 黄金ETF\n\n"
                f"- 数据条数：{len(response.records)}\n"
                f"- 最后交易日：{response.records[-1]['trade_date']}\n"
                f"- 最后收盘价：{last_close}\n"
                f"- 质量检查：{quality_summary.pass_count} pass\n"
            ),
            status="final",
        )

        reloaded_user_space = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        reloaded_master = MasterDataService(SQLiteMasterDataRepository(db_path))
        reloaded_research = ResearchStateService(
            SQLiteResearchStateRepository(db_path),
            reloaded_user_space,
            reloaded_master,
        )
        reloaded_context = reloaded_user_space.default_context()
        result = {
            "asset_id": response.asset.asset_id,
            "record_count": len(response.records),
            "quality_can_publish": quality_summary.can_publish,
            "watchlist_item_asset_id": item.asset_id,
            "holding_quantity": position.quantity,
            "report_id": report.report_id,
            "reloaded_watchlist_count": len(
                reloaded_research.list_watchlist_items(reloaded_context)
            ),
            "reloaded_holding_count": len(
                reloaded_research.list_holdings(reloaded_context)
            ),
            "reloaded_report_count": len(
                reloaded_research.list_reports(reloaded_context)
            ),
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
