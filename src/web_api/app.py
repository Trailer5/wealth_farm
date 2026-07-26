"""FastAPI application factory for local manual research."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.ai_harness import (
    ModelAdapter,
    ModelAdapterError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataAccessFetchError,
    DataAccessRequest,
)
from src.master_data import ASSET_TYPE_EXCHANGE_FUND, ASSET_TYPE_STOCK
from src.user_space import UserContext
from src.web_ui import WEB_UI_DIRECTORY, load_index_html

from .container import AppServices, create_services
from .schemas import (
    AnalysisReportRequest,
    AIAnalysisTaskRequest,
    CreateUserRequest,
    DailyBarsRequest,
    EventAlertRuleRequest,
    HoldingRequest,
    MaxWeightAlertRuleRequest,
    PortfolioValuationRequest,
    PriceAlertRuleRequest,
    PriceObservationRequest,
    WatchlistItemRequest,
)


def create_app(
    db_path: str | Path = "data/database/wealth_farm.sqlite3",
    model_adapter_factory: Callable[[], ModelAdapter] | None = None,
) -> FastAPI:
    """Create one local API backed by a shared SQLite file."""

    services = create_services(db_path)
    app = FastAPI(
        title="Wealth Farm Local Research API",
        version="0.1.0",
    )
    app.state.services = services
    app.state.model_adapter_factory = model_adapter_factory
    app.mount(
        "/static",
        StaticFiles(directory=WEB_UI_DIRECTORY),
        name="static",
    )

    @app.exception_handler(KeyError)
    async def handle_key_error(
        request: Request,
        exc: KeyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc).strip("'")},
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

    def current_context(
        x_user_id: str | None = Header(
            default=None,
            alias="X-User-Id",
        ),
    ) -> UserContext:
        if x_user_id is None:
            return services.user_space.default_context()
        return services.user_space.context_for_user(x_user_id)

    @app.get("/", response_class=HTMLResponse)
    def research_dashboard() -> str:
        return load_index_html()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "default_user_id": services.user_space.default_context().user_id,
        }

    @app.get("/users")
    def list_users() -> list[dict[str, object]]:
        return [asdict(user) for user in services.user_space.list_users()]

    @app.post("/users", status_code=201)
    def create_user(payload: CreateUserRequest) -> dict[str, object]:
        return asdict(
            services.user_space.create_user(payload.display_name)
        )

    @app.get("/watchlist")
    def get_watchlist(
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(item)
            for item in services.research_state.list_watchlist_items(context)
        ]

    @app.put("/watchlist/{asset_id}")
    def put_watchlist_item(
        asset_id: str,
        payload: WatchlistItemRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.research_state.add_watchlist_item(
                context,
                asset_id=asset_id,
                reason=payload.reason,
                tags=payload.tags,
                status=payload.status,
            )
        )

    @app.delete("/watchlist/{asset_id}")
    def delete_watchlist_item(
        asset_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, bool]:
        return {
            "removed": services.research_state.remove_watchlist_item(
                context,
                asset_id=asset_id,
            )
        }

    @app.get("/holdings")
    def get_holdings(
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(position)
            for position in services.research_state.list_holdings(context)
        ]

    @app.put("/holdings/{asset_id}")
    def put_holding(
        asset_id: str,
        payload: HoldingRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.research_state.upsert_holding(
                context,
                asset_id=asset_id,
                quantity=payload.quantity,
                average_cost=payload.average_cost,
                currency=payload.currency,
                note=payload.note,
            )
        )

    @app.delete("/holdings/{asset_id}")
    def delete_holding(
        asset_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, bool]:
        return {
            "removed": services.research_state.remove_holding(
                context,
                asset_id=asset_id,
            )
        }

    @app.get("/reports")
    def get_reports(
        asset_id: str | None = Query(default=None),
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(report)
            for report in services.research_state.list_reports(
                context,
                asset_id=asset_id,
            )
        ]

    @app.post("/reports", status_code=201)
    def create_report(
        payload: AnalysisReportRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.research_state.save_report(
                context,
                asset_id=payload.asset_id,
                title=payload.title,
                summary=payload.summary,
                content_markdown=payload.content_markdown,
                status=payload.status,
            )
        )

    @app.get("/reports/{report_id}")
    def get_report(
        report_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        report = services.research_state.get_report(context, report_id)
        if report is None:
            raise KeyError(f"Unknown report_id for user: {report_id}")
        return asdict(report)

    @app.get("/assets")
    def search_assets(
        query: str = Query(default="", max_length=100),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, object]]:
        needle = query.strip().casefold()
        assets = services.master_data.find_assets()
        if needle:
            assets = [
                asset
                for asset in assets
                if needle in asset.standard_symbol.casefold()
                or needle in asset.display_name.casefold()
            ]
            assets.sort(
                key=lambda asset: (
                    not asset.standard_symbol.casefold().startswith(needle),
                    not asset.display_name.casefold().startswith(needle),
                    asset.standard_symbol,
                )
            )
        else:
            assets.sort(key=lambda asset: asset.standard_symbol)
        return [asdict(asset) for asset in assets[:limit]]

    def query_daily_bars(
        asset_id: str,
        payload: DailyBarsRequest,
        *,
        task_name: str,
    ):
        asset = services.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        expected_dataset = {
            ASSET_TYPE_STOCK: DATASET_STOCK_DAILY_BARS,
            ASSET_TYPE_EXCHANGE_FUND: (
                DATASET_EXCHANGE_FUND_DAILY_BARS
            ),
        }.get(asset.asset_type)
        if expected_dataset is None:
            raise ValueError(
                f"Daily bars are not supported for {asset.asset_type}"
            )
        if payload.dataset != expected_dataset:
            raise ValueError(
                f"Dataset {payload.dataset} does not match "
                f"asset type {asset.asset_type}"
            )
        request = DataAccessRequest(
            asset=AssetInput(asset_id=asset_id),
            dataset=payload.dataset,
            start_date=payload.start_date,
            end_date=payload.end_date,
            max_age_seconds=payload.max_age_seconds,
            allow_remote=payload.allow_remote,
            allow_stale_on_error=payload.allow_stale_on_error,
            remote_sources=payload.remote_sources,
            provider_timeout_seconds=payload.provider_timeout_seconds,
        )
        try:
            response = services.data_access.get(request)
        except DataAccessFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        quality = services.data_quality.evaluate_data_access_response(
            task_name=task_name,
            response=response,
            expected_start=payload.start_date,
            expected_end=payload.end_date,
        )
        return response, quality

    @app.post("/assets/{asset_id}/daily-bars/query")
    def get_asset_daily_bars(
        asset_id: str,
        payload: DailyBarsRequest,
    ) -> dict[str, object]:
        response, quality = query_daily_bars(
            asset_id,
            payload,
            task_name="web_daily_bars_query",
        )
        return {
            "data": asdict(response),
            "quality": asdict(quality),
        }

    @app.post("/assets/{asset_id}/factors/compute")
    def compute_asset_factors(
        asset_id: str,
        payload: DailyBarsRequest,
    ) -> dict[str, object]:
        response, quality = query_daily_bars(
            asset_id,
            payload,
            task_name="web_factor_compute",
        )
        if not quality.can_publish:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Quality gate blocked factor computation: "
                    f"{quality.fail_count} failed checks"
                ),
            )
        values = services.factors.compute_and_store(
            asset_id=response.asset.asset_id,
            records=[dict(record) for record in response.records],
            source_dataset=response.dataset,
        )
        return {
            "data": asdict(response),
            "quality": asdict(quality),
            "values": [asdict(value) for value in values],
        }

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict[str, object]:
        asset = services.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        return asdict(asset)

    @app.get("/assets/{asset_id}/factors")
    def get_asset_factors(
        asset_id: str,
        factor_name: str | None = Query(default=None),
        as_of_date: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return [
            asdict(value)
            for value in services.factors.list_values(
                asset_id,
                factor_name=factor_name,
                as_of_date=as_of_date,
            )
        ]

    @app.get("/assets/{asset_id}/events")
    def get_asset_events(
        asset_id: str,
        start_date: str | None = Query(default=None),
        end_date: str | None = Query(default=None),
        event_type: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return [
            asdict(event)
            for event in services.events.list_events(
                asset_id,
                start_date=start_date,
                end_date=end_date,
                event_type=event_type,
            )
        ]

    @app.get("/portfolio/snapshots")
    def get_portfolio_snapshots(
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(snapshot)
            for snapshot in services.portfolio.list_snapshots(context)
        ]

    @app.post("/portfolio/valuations", status_code=201)
    def create_portfolio_valuation(
        payload: PortfolioValuationRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        valuation = services.portfolio.value_current_holdings(
            context,
            as_of_date=payload.as_of_date,
            prices=payload.prices,
        )
        generated_alerts = services.alerts.evaluate_portfolio(
            context,
            valuation.snapshot,
        )
        return {
            "valuation": asdict(valuation),
            "generated_alerts": [
                asdict(alert) for alert in generated_alerts
            ],
        }

    @app.get("/portfolio/snapshots/{snapshot_id}")
    def get_portfolio_snapshot(
        snapshot_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        snapshot = services.portfolio.get_snapshot(
            context,
            snapshot_id,
        )
        if snapshot is None:
            raise KeyError(
                f"Unknown snapshot_id for user: {snapshot_id}"
            )
        return asdict(snapshot)

    @app.get("/alerts")
    def get_alerts(
        status: str | None = Query(default=None),
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(alert)
            for alert in services.alerts.list_alerts(
                context,
                status=status,
            )
        ]

    @app.get("/alert-rules")
    def get_alert_rules(
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(rule)
            for rule in services.alerts.list_rules(context)
        ]

    @app.post("/alert-rules/price", status_code=201)
    def create_price_alert_rule(
        payload: PriceAlertRuleRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.alerts.create_price_rule(
                context,
                asset_id=payload.asset_id,
                rule_type=payload.rule_type,
                threshold=payload.threshold,
            )
        )

    @app.post("/alert-rules/event", status_code=201)
    def create_event_alert_rule(
        payload: EventAlertRuleRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.alerts.create_event_rule(
                context,
                event_type=payload.event_type,
                asset_id=payload.asset_id,
            )
        )

    @app.post("/alert-rules/max-weight", status_code=201)
    def create_max_weight_alert_rule(
        payload: MaxWeightAlertRuleRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.alerts.create_max_weight_rule(
                context,
                threshold=payload.threshold,
            )
        )

    @app.post("/alerts/evaluate-price")
    def evaluate_price_alerts(
        payload: PriceObservationRequest,
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(alert)
            for alert in services.alerts.evaluate_price(
                context,
                asset_id=payload.asset_id,
                price=payload.price,
                observed_at=payload.observed_at,
            )
        ]

    @app.post("/alerts/{alert_id}/acknowledge")
    def acknowledge_alert(
        alert_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.alerts.acknowledge(context, alert_id)
        )

    @app.post("/ai/tasks", status_code=201)
    def create_ai_task(
        payload: AIAnalysisTaskRequest,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.ai_harness.create_asset_analysis(
                context,
                asset_id=payload.asset_id,
                question=payload.question,
            )
        )

    @app.get("/ai/tasks")
    def list_ai_tasks(
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(task)
            for task in services.ai_harness.list_tasks(context)
        ]

    @app.get("/ai/tasks/{task_id}")
    def get_ai_task(
        task_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        return asdict(
            services.ai_harness.get_task(context, task_id)
        )

    @app.get("/ai/tasks/{task_id}/evidence")
    def get_ai_evidence(
        task_id: str,
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(item)
            for item in services.ai_harness.list_evidence(
                context,
                task_id,
            )
        ]

    @app.get("/ai/tasks/{task_id}/tools")
    def get_ai_tool_calls(
        task_id: str,
        context: UserContext = Depends(current_context),
    ) -> list[dict[str, object]]:
        return [
            asdict(item)
            for item in services.ai_harness.list_tool_executions(
                context,
                task_id,
            )
        ]

    @app.post("/ai/tasks/{task_id}/execute")
    def execute_ai_task(
        task_id: str,
        context: UserContext = Depends(current_context),
    ) -> dict[str, object]:
        try:
            adapter = (
                model_adapter_factory()
                if model_adapter_factory is not None
                else OpenAICompatibleAdapter(
                    OpenAICompatibleConfig.from_env()
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"AI model is not configured: {exc}",
            ) from exc
        try:
            return asdict(
                services.ai_harness.execute(
                    context,
                    task_id,
                    adapter,
                )
            )
        except ModelAdapterError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

    @app.get("/data-quality/runs")
    def list_data_quality_runs(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        return [
            asdict(run)
            for run in services.data_quality.list_tasks(limit=limit)
        ]

    @app.get("/data-quality/runs/{run_id}")
    def get_data_quality_run(run_id: str) -> dict[str, object]:
        return asdict(services.data_quality.summary(run_id))

    @app.get("/master-data/unresolved")
    def list_unresolved_master_data() -> list[dict[str, object]]:
        return [
            asdict(observation)
            for observation in (
                services.master_data.list_unresolved_observations()
            )
        ]

    return app
