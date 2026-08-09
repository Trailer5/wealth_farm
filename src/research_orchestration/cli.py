"""Command-line entry point for unified single-asset research."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
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
from src.research_workflow import (
    PROFILE_CYCLICAL_RESOURCE,
    CycleResearchExecutionOptions,
    ResearchExecutionOptions,
    ResearchHorizon,
    ResearchQuestionType,
)

from .container import (
    ResearchOrchestrationContainer,
    create_default_container,
)
from .models import ResearchRunRequest, ResearchRunStatus
from .event_name_discovery import EventNameDiscoveryRunRequest


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
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        print(
            "error: research identity or execution could not be resolved.",
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


def _resolve_asset_id(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> str:
    if args.asset_id:
        return str(args.asset_id)
    matches = container.master_data.find_assets(
        standard_symbol=args.symbol,
        asset_type=args.asset_type,
    )
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
    return matches[0].asset_id


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
