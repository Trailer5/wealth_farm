"""Command-line entry point for unified single-asset research."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, TextIO

from src.event_name_discovery import (
    AlternativeCatalystAssessment,
    AttentionAttributionAssessment,
    AssetNameSnapshot,
    BusinessRelationAssessment,
    ConsensusFormationAssessment,
    EventNameDiscoverySpec,
    EventShockAssessment,
    ExternalEventAnchor,
    MarketReactionSnapshot,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    DecisionIntent,
    DecisionLane,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchRequest,
    View,
)
from src.analog_history import ANALOG_METRICS
from src.research_ledger import LedgerEntryType
from src.research_routing import (
    QuestionRouter,
    RecipeRunStatus,
    RoutingStatus,
    VisionAcceptanceRunner,
    render_coverage_matrix,
    vision_question_scenarios,
)
from src.research_workflow import (
    CycleResearchExecutionOptions,
    ResearchExecutionOptions,
)

from .container import (
    ResearchOrchestrationContainer,
    create_default_container,
)
from .models import ResearchRunRequest, ResearchRunStatus
from .event_name_discovery import EventNameDiscoveryRunRequest
from .ledger_projection import LedgerRecordingStatus
from .request_planner import (
    ResearchPlanningRequest,
    ResearchPlanningStatus,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wealth-farm-research",
        description="Run the unified research orchestration entry point.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_single = commands.add_parser(
        "run-single",
        help="Collect and publish one single-asset baseline package.",
    )
    identity = run_single.add_mutually_exclusive_group(required=True)
    identity.add_argument("--asset-id")
    identity.add_argument("--symbol")
    run_single.add_argument("--asset-type")
    run_single.add_argument("--exchange")
    run_single.add_argument(
        "--name",
        help=(
            "Optional expected name checked against the provider identity "
            "when a missing open-fund symbol is created."
        ),
    )
    run_single.add_argument("--profile", required=True)
    run_single.add_argument(
        "--question",
        required=True,
        type=ResearchQuestionType,
        choices=tuple(item.value for item in ResearchQuestionType),
    )
    run_single.add_argument(
        "--horizon",
        required=True,
        type=ResearchHorizon,
        choices=tuple(item.value for item in ResearchHorizon),
    )
    run_single.add_argument("--as-of-date", required=True)
    run_single.add_argument("--benchmark-asset-id")
    run_single.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    run_single.add_argument("--report-root", default="report")
    run_single.add_argument(
        "--provider-timeout",
        type=float,
        default=90.0,
    )
    run_single.add_argument(
        "--commodity-weight",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        type=_parse_commodity_weight,
    )
    plan_single = commands.add_parser(
        "plan-single",
        help=(
            "Resolve one code and produce a profile-aware baseline "
            "task draft."
        ),
    )
    planning_identity = plan_single.add_mutually_exclusive_group(
        required=True
    )
    planning_identity.add_argument("--asset-id")
    planning_identity.add_argument("--symbol")
    plan_single.add_argument("--asset-type")
    plan_single.add_argument("--market")
    plan_single.add_argument("--exchange")
    plan_single.add_argument(
        "--decision-intent",
        type=DecisionIntent,
        choices=tuple(item.value for item in DecisionIntent),
        default=DecisionIntent.UNDERSTANDING,
    )
    plan_single.add_argument(
        "--decision-lane",
        action="append",
        type=DecisionLane,
        choices=tuple(item.value for item in DecisionLane),
        default=[],
    )
    plan_single.add_argument(
        "--horizon",
        type=ResearchHorizon,
        choices=tuple(item.value for item in ResearchHorizon),
    )
    plan_single.add_argument(
        "--as-of-date",
        default=date.today().isoformat(),
    )
    plan_single.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    plan_single.add_argument("--report-root", default="report")
    event_name = commands.add_parser(
        "run-event-name-discovery",
        help="Run one prepared event-to-name discovery request.",
    )
    event_name.add_argument("--input-json", required=True)
    event_name.add_argument(
        "--allow-current-name-fallback",
        action="store_true",
    )
    event_name.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    event_name.add_argument("--report-root", default="report")
    replay_ledger = commands.add_parser(
        "replay-ledger",
        help="Record published final reports missing from the ledger.",
    )
    replay_ledger.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    replay_ledger.add_argument("--report-root", default="report")
    settle_ledger = commands.add_parser(
        "settle-ledger",
        help="Settle due ledger checkpoints with forward prices.",
    )
    settle_ledger.add_argument(
        "--as-of-date",
        default=date.today().isoformat(),
    )
    settle_ledger.add_argument(
        "--recompute",
        action="store_true",
        help="Re-settle finished checkpoints to capture data revisions.",
    )
    settle_ledger.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    settle_ledger.add_argument("--report-root", default="report")
    ledger = commands.add_parser(
        "ledger",
        help="Show ledger judgements with their latest settlements.",
    )
    ledger.add_argument(
        "--user-id",
        help="Include this user's personal entries; objective entries are always shown.",
    )
    ledger.add_argument("--subject-id")
    ledger.add_argument(
        "--entry-type",
        choices=[item.value for item in LedgerEntryType],
    )
    ledger.add_argument(
        "--active-on",
        help="Keep unexpired, unrevised entries on this date.",
    )
    ledger.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    ledger.add_argument("--report-root", default="report")
    record_view = commands.add_parser(
        "record-view",
        help="Record a falsifiable view (View JSON); metric views settle on macro series.",
    )
    record_view.add_argument(
        "--view-json",
        required=True,
        help="Path to a View JSON file; decimals are strings.",
    )
    record_view.add_argument(
        "--supersedes-entry-id",
        help="Ledger entry this view rewrites, e.g. the raw user view from run-recipe.",
    )
    record_view.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_view.add_argument("--report-root", default="report")
    asset = commands.add_parser(
        "asset",
        help="Look up asset IDs by code; registers listings not yet known locally.",
    )
    asset.add_argument("--symbol", required=True)
    asset.add_argument("--asset-type", help="stock, index, exchange_fund or open_fund.")
    asset.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    asset.add_argument("--report-root", default="report")
    industry = commands.add_parser(
        "industry",
        help="Shenwan 2021 industries: list, classify a stock, members, index and valuation.",
    )
    industry.add_argument("--symbol", help="Stock code: its industry path and peers.")
    industry.add_argument("--code", help="Shenwan industry code, e.g. 801120.")
    industry.add_argument("--level", type=int, choices=(1, 2, 3), default=1)
    industry.add_argument("--parent", help="List children of this industry code.")
    industry.add_argument("--members", action="store_true")
    industry.add_argument("--valuation", action="store_true")
    industry.add_argument("--start-date")
    industry.add_argument("--end-date", default=date.today().isoformat())
    industry.add_argument("--no-remote", action="store_true")
    industry.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    industry.add_argument("--report-root", default="report")
    cross_section = commands.add_parser(
        "cross-section",
        help="On-demand A-share cross-sections: snapshot, financials, valuations, delisted, index members.",
    )
    mode = cross_section.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", action="store_true", help="Latest all-stock price and valuation.")
    mode.add_argument("--financials", action="store_true", help="Report-period financials as of a date.")
    mode.add_argument("--valuation", action="store_true", help="Daily valuation history of --symbols.")
    mode.add_argument("--delisted", action="store_true")
    mode.add_argument("--index-members", help="000300, 000905 or 000016 on --as-of-date.")
    cross_section.add_argument("--symbols", help="Comma-separated stock codes.")
    cross_section.add_argument("--as-of-date", default=date.today().isoformat())
    cross_section.add_argument("--period", help="Report period end, e.g. 2026-06-30.")
    cross_section.add_argument("--start-date", default="2018-01-01")
    cross_section.add_argument("--limit", type=int, default=20, help="Rows printed (0 prints all).")
    cross_section.add_argument("--output", help="Also write the full JSON to this path.")
    cross_section.add_argument("--no-remote", action="store_true")
    cross_section.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    cross_section.add_argument("--report-root", default="report")
    macro = commands.add_parser(
        "macro",
        help="List macro series or read one series as of a date.",
    )
    macro.add_argument(
        "--series",
        help="Series id from the catalog; omit to list the catalog.",
    )
    macro.add_argument("--start-date", default="2016-01-01")
    macro.add_argument("--end-date", default=date.today().isoformat())
    macro.add_argument(
        "--as-of-date",
        help="Only values published on or before this date.",
    )
    macro.add_argument(
        "--limit",
        type=int,
        default=24,
        help="Print only the most recent points (0 prints all).",
    )
    macro.add_argument("--no-remote", action="store_true")
    macro.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    macro.add_argument("--report-root", default="report")
    market_archive = commands.add_parser(
        "market-archive",
        help="Build the A-share market state as of a date and record it.",
    )
    market_archive.add_argument("--as-of-date", default=date.today().isoformat())
    market_archive.add_argument(
        "--no-record",
        action="store_true",
        help="Do not record the state assessment in the ledger.",
    )
    market_archive.add_argument("--no-remote", action="store_true")
    market_archive.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    market_archive.add_argument("--report-root", default="report")
    macro_archive = commands.add_parser(
        "macro-archive",
        help="Build the China macro state as of a date and record it.",
    )
    macro_archive.add_argument("--as-of-date", default=date.today().isoformat())
    macro_archive.add_argument(
        "--no-record",
        action="store_true",
        help="Do not record the state assessment in the ledger.",
    )
    macro_archive.add_argument("--no-remote", action="store_true")
    macro_archive.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    macro_archive.add_argument("--report-root", default="report")
    analog = commands.add_parser(
        "analog",
        help="Forward CSI 300 returns after dates in a metric's percentile band.",
    )
    analog.add_argument(
        "--metric",
        help="Metric id; omit to list the available metrics.",
    )
    analog.add_argument("--as-of-date", default=date.today().isoformat())
    analog.add_argument(
        "--band",
        help="Percentile band low,high in [0,1]; default is current percentile ±0.1.",
    )
    analog.add_argument("--horizons", default="20,60,120,250")
    analog.add_argument("--no-remote", action="store_true")
    analog.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    analog.add_argument("--report-root", default="report")
    route = commands.add_parser(
        "route",
        help="Route one structured research request to recipes and gaps.",
    )
    route.add_argument(
        "--request-json",
        required=True,
        help="Path to a ResearchRequest JSON file.",
    )
    route.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    route.add_argument("--report-root", default="report")
    run_recipe = commands.add_parser(
        "run-recipe",
        help="Route one structured request and run its available steps.",
    )
    run_recipe.add_argument("--request-json", required=True)
    run_recipe.add_argument(
        "--discovery-input-json",
        help="Event-name discovery input for sector discovery recipes.",
    )
    run_recipe.add_argument(
        "--allow-current-name-fallback",
        action="store_true",
    )
    run_recipe.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    run_recipe.add_argument("--report-root", default="report")
    vision = commands.add_parser(
        "vision-acceptance",
        help="Replay vision_q_v1 scenarios and print the coverage matrix.",
    )
    vision.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    vision.add_argument("--report-root", default="report")
    user_context = commands.add_parser(
        "user-context",
        help="Show users, holdings, valuations, mandates and constraints.",
    )
    user_context.add_argument("--user-id")
    user_context.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    user_context.add_argument("--report-root", default="report")
    record_holding = commands.add_parser(
        "record-holding",
        help="Record one holding the user explicitly provided.",
    )
    record_holding.add_argument("--user-id", required=True)
    record_holding.add_argument("--asset-id", required=True)
    record_holding.add_argument("--quantity", required=True, type=float)
    record_holding.add_argument("--average-cost", type=float)
    record_holding.add_argument("--currency", default="CNY")
    record_holding.add_argument("--note", default="")
    record_holding.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_holding.add_argument("--report-root", default="report")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    container_factory: Callable[
        ..., ResearchOrchestrationContainer
    ] = create_default_container,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if stdout is None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if stderr is None and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr

    try:
        container = container_factory(
            db_path=args.db_path,
            report_root=args.report_root,
        )
        if args.command == "plan-single":
            planning_result = container.request_planner.plan(
                ResearchPlanningRequest(
                    asset_id=args.asset_id,
                    symbol=args.symbol,
                    asset_type=args.asset_type,
                    market=args.market,
                    exchange=args.exchange,
                    as_of_date=args.as_of_date,
                    decision_intent=args.decision_intent,
                    decision_lanes=tuple(args.decision_lane),
                    horizon=(
                        args.horizon
                        or _default_planning_horizon(
                            tuple(args.decision_lane)
                        )
                    ),
                )
            )
            print(
                json.dumps(
                    planning_result.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return (
                0
                if planning_result.status
                is ResearchPlanningStatus.READY
                else 2
            )
        if args.command == "run-event-name-discovery":
            payload = json.loads(
                Path(args.input_json).read_text(encoding="utf-8")
            )
            discovery_result = container.event_name_discovery.run(
                _event_name_request(
                    payload,
                    allow_current_name_fallback=(
                        args.allow_current_name_fallback
                    ),
                )
            )
            print(
                discovery_result.discovery_run.run_id,
                file=output,
            )
            return 0
        if args.command == "replay-ledger":
            outcomes = container.ledger_recorder.replay()
            print(
                json.dumps(
                    [
                        {
                            "report_id": item.report_id,
                            "status": item.status.value,
                            "entry_ids": list(item.entry_ids),
                            "error": item.error,
                        }
                        for item in outcomes
                    ],
                    ensure_ascii=False,
                ),
                file=output,
            )
            return (
                0
                if all(
                    item.status is LedgerRecordingStatus.RECORDED
                    for item in outcomes
                )
                else 2
            )
        if args.command == "settle-ledger":
            summary = container.ledger_settlement.settle_due(
                args.as_of_date,
                recompute=args.recompute,
            )
            print(
                json.dumps(summary.to_dict(), ensure_ascii=False),
                file=output,
            )
            return 0
        if args.command == "ledger":
            record = container.ledger.track_record(
                user_id=args.user_id,
                entry_type=(
                    LedgerEntryType(args.entry_type)
                    if args.entry_type
                    else None
                ),
                subject_id=args.subject_id,
                active_on=args.active_on,
            )
            for item in record["entries"]:
                asset = container.master_data.get_asset(item["subject_id"])
                if asset is not None:
                    item["subject_name"] = asset.display_name
                    item["subject_symbol"] = asset.standard_symbol
            print(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                file=output,
            )
            return 0
        if args.command == "record-view":
            payload = container.view_recorder.record(
                View.from_dict(
                    json.loads(Path(args.view_json).read_text(encoding="utf-8"))
                ),
                supersedes_entry_id=args.supersedes_entry_id,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command == "asset":
            assets = container.master_data.find_assets(
                standard_symbol=args.symbol,
                asset_type=args.asset_type,
            )
            if not assets:
                container.data_fetch.fetch_and_store_identities(
                    args.symbol,
                    asset_type=args.asset_type,
                )
                assets = container.master_data.find_assets(
                    standard_symbol=args.symbol,
                    asset_type=args.asset_type,
                )
            print(
                json.dumps(
                    [
                        {
                            "asset_id": item.asset_id,
                            "asset_type": item.asset_type,
                            "symbol": item.standard_symbol,
                            "name": item.display_name,
                            "market": item.market,
                            "exchange": item.exchange,
                        }
                        for item in assets
                    ],
                    ensure_ascii=False,
                ),
                file=output,
            )
            return 0 if assets else 2
        if args.command == "industry":
            print(
                json.dumps(
                    _industry_payload(container, args),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                file=output,
            )
            return 0
        if args.command == "cross-section":
            print(
                json.dumps(
                    _cross_section_payload(container, args),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                file=output,
            )
            return 0
        if args.command == "macro":
            if not args.series:
                payload: object = [
                    item.to_dict() for item in container.macro_data.catalog()
                ]
            else:
                result = container.macro_data.get(
                    args.series,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    as_of_date=args.as_of_date,
                    allow_remote=not args.no_remote,
                )
                payload = result.to_dict(limit=args.limit or None)
            print(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                file=output,
            )
            return 0
        if args.command in {"market-archive", "macro-archive"}:
            recorder = (
                container.market_state
                if args.command == "market-archive"
                else container.macro_state
            )
            payload = recorder.run(
                args.as_of_date,
                record=not args.no_record,
                allow_remote=not args.no_remote,
            )
            print(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                file=output,
            )
            return 0
        if args.command == "analog":
            if not args.metric:
                payload = [
                    {
                        "metric_id": item.metric_id,
                        "name": item.name,
                        "frequency": item.frequency,
                        "description": item.description,
                    }
                    for item in ANALOG_METRICS
                ]
            else:
                band = (
                    tuple(float(item) for item in args.band.split(","))
                    if args.band
                    else None
                )
                payload = container.analogs.query(
                    args.metric,
                    as_of_date=args.as_of_date,
                    band=band,
                    horizons=tuple(int(item) for item in args.horizons.split(",")),
                    allow_remote=not args.no_remote,
                )
            print(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                file=output,
            )
            return 0
        if args.command == "route":
            routing = container.router.route(
                _read_research_request(args.request_json)
            )
            print(
                json.dumps(
                    routing.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return (
                2
                if routing.status
                in (
                    RoutingStatus.UNRESOLVED,
                    RoutingStatus.UNRESOLVED_SUBJECT,
                )
                else 0
            )
        if args.command == "run-recipe":
            discovery_request = (
                _event_name_request(
                    json.loads(
                        Path(args.discovery_input_json).read_text(
                            encoding="utf-8"
                        )
                    ),
                    allow_current_name_fallback=(
                        args.allow_current_name_fallback
                    ),
                )
                if args.discovery_input_json
                else None
            )
            run = container.recipe_executor.run(
                _read_research_request(args.request_json),
                discovery_request=discovery_request,
            )
            print(
                json.dumps(
                    run.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return 0 if run.status is RecipeRunStatus.EXECUTED else 2
        if args.command == "vision-acceptance":
            return _vision_acceptance(container, output)
        if args.command == "user-context":
            user_ids = (
                [args.user_id]
                if args.user_id
                else [
                    item.user_id
                    for item in container.user_space.list_users()
                ]
            )
            print(
                json.dumps(
                    {
                        "users": [
                            container.user_context.describe(user_id)
                            for user_id in user_ids
                        ]
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return 0
        if args.command == "record-holding":
            container.portfolio_risk.research_state.upsert_holding(
                container.user_space.context_for_user(args.user_id),
                asset_id=args.asset_id,
                quantity=args.quantity,
                average_cost=args.average_cost,
                currency=args.currency,
                note=args.note,
            )
            print(
                json.dumps(
                    container.user_context.describe(args.user_id),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return 0
        asset_id = _resolve_asset_id(container, args)
        task = container.task_factory.create(
            asset_id=asset_id,
            profile_id=args.profile,
            question_type=args.question,
            horizon=args.horizon,
            as_of_date=args.as_of_date,
            benchmark_asset_id=args.benchmark_asset_id,
        )
        options = _execution_options(args)
        result = container.pipeline.run(
            ResearchRunRequest(
                task=task,
                execution_options=options,
            )
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(
            "error: research identity or execution could not be resolved: "
            f"{exc}",
            file=error_output,
        )
        return 2

    if result.status is ResearchRunStatus.BLOCKED:
        print(
            "error: research execution was blocked.",
            file=error_output,
        )
        return 2
    if result.report_path is None:
        print(
            "error: no report was published.",
            file=error_output,
        )
        return 2

    print(result.report_path, file=output)
    return 0


def _read_research_request(path: str) -> ResearchRequest:
    return ResearchRequest.from_dict(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def _cross_section_payload(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> object:
    service = container.cross_section
    remote = not args.no_remote
    symbols = (
        [item.strip().zfill(6) for item in args.symbols.split(",") if item.strip()]
        if args.symbols
        else None
    )
    if args.snapshot:
        payload = service.market_snapshot(allow_remote=remote)
    elif args.financials:
        payload = (
            service.report_period(args.period, as_of_date=args.as_of_date, allow_remote=remote)
            if args.period
            else service.latest_financials(args.as_of_date, allow_remote=remote)
        )
    elif args.valuation:
        if not symbols:
            raise ValueError("--valuation requires --symbols")
        return service.valuation_history(
            symbols,
            start_date=args.start_date,
            end_date=args.as_of_date,
            allow_remote=remote,
        )
    elif args.delisted:
        payload = {"rows": service.delisted(allow_remote=remote)}
    else:
        payload = service.index_members_on(args.index_members, args.as_of_date, allow_remote=remote)
        payload["rows"] = payload.pop("members")
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    rows = payload["rows"]
    if symbols:
        rows = [row for row in rows if row.get("symbol") in symbols]
    payload["total_rows"] = len(payload["rows"])
    payload["rows"] = rows if not args.limit else rows[: args.limit]
    return payload


def _industry_payload(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> object:
    sector = container.sector_data
    remote = not args.no_remote
    if args.symbol:
        peers = sector.peers(args.symbol, allow_remote=remote)
        if peers is None:
            raise KeyError(f"{args.symbol} is not a current Shenwan member")
        for level in ("l1", "l2", "l3"):
            node = peers["path"][level]
            node["index_asset_id"] = sector.index_asset(node["code"]).asset_id
        return peers
    if args.code:
        node = sector.industry(args.code, allow_remote=remote)
        if node is None:
            raise KeyError(f"Unknown Shenwan industry: {args.code}")
        payload: dict[str, object] = {
            **node.to_dict(),
            "index_asset_id": sector.index_asset(args.code).asset_id,
            "children": [
                item.to_dict()
                for item in sector.industries(parent_code=args.code, allow_remote=remote)
            ],
        }
        if args.members:
            payload["members"] = [
                item.to_dict() for item in sector.members(args.code, allow_remote=remote)
            ]
        if args.valuation:
            end = date.fromisoformat(args.end_date)
            payload["valuation"] = sector.valuation_history(
                args.code,
                start_date=args.start_date or (end - timedelta(days=3653)).isoformat(),
                end_date=args.end_date,
                allow_remote=remote,
            )
        return payload
    return [
        item.to_dict()
        for item in sector.industries(
            level=None if args.parent else args.level,
            parent_code=args.parent,
            allow_remote=remote,
        )
    ]


def _vision_acceptance(
    container: ResearchOrchestrationContainer,
    output: TextIO,
) -> int:
    # Question-level structure: frozen requests carry resolved IDs, so no identity lookup.
    router = QuestionRouter(recipes=container.router.recipes)
    runner = VisionAcceptanceRunner()
    rows = []
    failed = False
    for scenario in vision_question_scenarios():
        outcome = runner.evaluate(scenario, router.route(scenario.request))
        rows.extend(outcome.coverage)
        failed = failed or not outcome.passed
        print(
            f"{outcome.scenario_id}: "
            + ("pass" if outcome.passed else "fail " + "; ".join(outcome.findings)),
            file=output,
        )
    print(render_coverage_matrix(tuple(rows)), file=output)
    return 2 if failed else 0


def _parse_commodity_weight(value: str) -> tuple[str, float]:
    key, separator, raw_weight = value.partition("=")
    if not separator or not key or not raw_weight:
        raise argparse.ArgumentTypeError(
            "commodity weights must use KEY=VALUE"
        )
    try:
        weight = float(raw_weight)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "commodity weight VALUE must be a number"
        ) from exc
    return key, weight


def _default_planning_horizon(
    decision_lanes: tuple[DecisionLane, ...],
) -> ResearchHorizon:
    if DecisionLane.TIMING in decision_lanes:
        return ResearchHorizon.SHORT
    return ResearchHorizon.MEDIUM_LONG


def _resolve_asset_id(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> str:
    if args.asset_id:
        return str(args.asset_id)
    if not args.asset_type:
        raise ValueError(
            "--asset-type is required when resolving by --symbol"
        )
    matches = container.master_data.find_assets(
        standard_symbol=args.symbol,
        asset_type=args.asset_type,
    )
    bootstrapped = False
    if not matches:
        container.data_fetch.fetch_and_store_identities(
            str(args.symbol),
            asset_type=args.asset_type,
        )
        matches = container.master_data.find_assets(
            standard_symbol=args.symbol,
            asset_type=args.asset_type,
        )
        bootstrapped = True
    if args.exchange:
        matches = [
            asset
            for asset in matches
            if asset.exchange == args.exchange
        ]
    if len(matches) != 1:
        raise ValueError(
            "symbol must resolve to exactly one persisted asset"
        )
    if (
        bootstrapped
        and args.name
        and _identity_name_key(matches[0].display_name)
        != _identity_name_key(args.name)
    ):
        raise ValueError(
            "provided --name does not match the provider fund identity"
        )
    return matches[0].asset_id


def _identity_name_key(value: object) -> str:
    return (
        "".join(str(value).split())
        .replace("（", "(")
        .replace("）", ")")
        .casefold()
    )


def _execution_options(args: argparse.Namespace) -> object:
    if args.profile == PROFILE_CYCLICAL_RESOURCE:
        return CycleResearchExecutionOptions(
            provider_timeout_seconds=args.provider_timeout,
            commodity_weights=tuple(args.commodity_weight),
        )
    if args.commodity_weight:
        raise ValueError(
            "commodity weights only apply to cyclical resources"
        )
    return ResearchExecutionOptions(
        provider_timeout_seconds=args.provider_timeout,
    )


def _event_name_request(
    payload: dict[str, object],
    *,
    allow_current_name_fallback: bool,
) -> EventNameDiscoveryRunRequest:
    assets_payload = payload.get("assets")
    assets = (
        tuple(
            AssetNameSnapshot.from_dict(item)
            for item in assets_payload
        )
        if isinstance(assets_payload, list)
        else None
    )
    relation_items = payload.get("relation_assessments") or []
    catalyst_items = payload.get("catalyst_assessments") or []
    consensus_items = payload.get("consensus_assessments") or []
    attribution_items = payload.get("attribution_assessments") or []
    event_shock_payload = payload.get("event_shock_assessment")
    reaction_items = payload.get("reactions") or []
    relations = tuple(
        BusinessRelationAssessment.from_dict(item)
        for item in relation_items
    )
    catalysts = tuple(
        AlternativeCatalystAssessment.from_dict(item)
        for item in catalyst_items
    )
    consensus = tuple(
        ConsensusFormationAssessment.from_dict(item)
        for item in consensus_items
    )
    attributions = tuple(
        AttentionAttributionAssessment.from_dict(item)
        for item in attribution_items
    )
    reactions = tuple(
        MarketReactionSnapshot.from_dict(item)
        for item in reaction_items
    )
    return EventNameDiscoveryRunRequest(
        event=ExternalEventAnchor.from_dict(payload["event"]),
        spec=EventNameDiscoverySpec.from_dict(payload["spec"]),
        assets=assets,
        relation_assessments={
            item.asset_id: item for item in relations
        },
        catalyst_assessments={
            item.asset_id: item for item in catalysts
        },
        consensus_assessments={
            item.asset_id: item for item in consensus
        },
        attribution_assessments={
            item.asset_id: item for item in attributions
        },
        event_shock_assessment=(
            EventShockAssessment.from_dict(event_shock_payload)
            if event_shock_payload is not None
            else None
        ),
        precomputed_reactions={
            item.asset_id: item for item in reactions
        },
        allow_current_name_fallback=allow_current_name_fallback,
    )


if __name__ == "__main__":
    raise SystemExit(main())
