"""Run a real-asset manual research flow through FastAPI TestClient."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from fastapi.testclient import TestClient

    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.service import DataFetchService
    from src.data_src.models import DisclosureDocument
    from src.data_store import SQLiteDataStore
    from src.web_api import create_app

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "local-api-smoke.sqlite3"
        app = create_app(db_path)
        services = app.state.services
        store = SQLiteDataStore(db_path)
        fetch = DataFetchService(
            store=store,
            master_data_service=services.master_data,
        )
        repository = SQLiteDataAccessRepository(store)
        response = DataAccessService(
            master_data=services.master_data,
            repository=repository,
            fetch_service=fetch,
            trading_calendar=TradingCalendarService(
                repository=repository,
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
            )
        )
        asset_id = response.asset.asset_id
        last_close = float(response.records[-1]["close"])

        with TestClient(app) as client:
            health = client.get("/health")
            watchlist = client.put(
                f"/watchlist/{asset_id}",
                json={
                    "reason": "本地 API smoke",
                    "tags": ["黄金", "ETF"],
                },
            )
            holding = client.put(
                f"/holdings/{asset_id}",
                json={
                    "quantity": 100,
                    "average_cost": last_close,
                    "note": "API smoke 持仓",
                },
            )
            report = client.post(
                "/reports",
                json={
                    "asset_id": asset_id,
                    "title": "黄金ETF本地API验证",
                    "summary": "FastAPI 手动研究流程通过。",
                    "content_markdown": (
                        "# 黄金ETF\n\n"
                        f"最后收盘价：{last_close}"
                    ),
                    "status": "final",
                },
            )
            services.factors.compute_from_response(response)
            services.events.ingest_disclosure(
                asset_id,
                DisclosureDocument(
                    document_id="gold-api-smoke-event",
                    symbol="518880",
                    title="黄金ETF2025年年度报告",
                    announcement_date="2026-04-17",
                    category="annual",
                    source="cninfo",
                    file_url="https://example.com/gold-report.pdf",
                ),
            )
            context = services.user_space.default_context()
            valuation = services.portfolio.value_current_holdings(
                context,
                as_of_date=str(response.records[-1]["trade_date"]),
                prices={asset_id: last_close},
            )
            factors = client.get(f"/assets/{asset_id}/factors")
            events = client.get(f"/assets/{asset_id}/events")
            portfolio = client.get(
                f"/portfolio/snapshots/{valuation.snapshot.snapshot_id}"
            )
            dashboard = client.get("/")
            dashboard_script = client.get("/static/app.js")
            dashboard_styles = client.get("/static/styles.css")
            result = {
                "health": health.json(),
                "asset_id": asset_id,
                "last_close": last_close,
                "watchlist_status": watchlist.status_code,
                "holding_status": holding.status_code,
                "report_status": report.status_code,
                "watchlist_count": len(client.get("/watchlist").json()),
                "holding_count": len(client.get("/holdings").json()),
                "report_count": len(client.get("/reports").json()),
                "factor_count": len(factors.json()),
                "event_count": len(events.json()),
                "portfolio_position_count": len(
                    portfolio.json()["positions"]
                ),
                "dashboard_status": dashboard.status_code,
                "dashboard_contains_asset_research": (
                    'id="asset-summary"' in dashboard.text
                ),
                "dashboard_contains_editors": all(
                    form_id in dashboard.text
                    for form_id in (
                        'id="watchlist-form"',
                        'id="holding-form"',
                        'id="report-form"',
                    )
                ),
                "dashboard_contains_workbench_views": all(
                    view in dashboard.text
                    for view in (
                        'data-view="dashboard"',
                        'data-view="asset"',
                        'data-view="portfolio"',
                        'data-view="alerts"',
                        'data-view="reports"',
                        'data-view="ai"',
                        'data-view="system"',
                    )
                ),
                "dashboard_script_status": dashboard_script.status_code,
                "dashboard_styles_status": dashboard_styles.status_code,
            }
            print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
