"""Command-line entry point for unified single-asset research."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
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
    InvestmentThesis,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchRequest,
    View,
)
from src.master_data import ASSET_TYPE_STOCK
from src.portfolio_risk import (
    AggressivenessMeaning,
    PreferenceHorizon,
    RiskProfile,
    TradeRecord,
    TradeSide,
    derive_preference,
    initial_preference,
    preference_changes,
)
from src.analog_history import ANALOG_METRICS
from src.discovery_funnel import DEFAULT_BASE_PACKS, STRATEGIES, DiscoveryScope
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
from .cli_help import EPILOGS
from .strategy_archive import StrategyArchiveError
from src.strategy import LifecycleState
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
    record_thesis = commands.add_parser(
        "record-thesis",
        help="Record an investment thesis (InvestmentThesis JSON) and its metric views.",
    )
    record_thesis.add_argument(
        "--thesis-json",
        required=True,
        help="Path to an InvestmentThesis JSON file; decimals are strings.",
    )
    record_thesis.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_thesis.add_argument("--report-root", default="report")
    thesis_check = commands.add_parser(
        "thesis-check",
        help="Check recorded theses of a stock against reports published by a date.",
    )
    thesis_target = thesis_check.add_mutually_exclusive_group(required=True)
    thesis_target.add_argument("--asset-id")
    thesis_target.add_argument("--symbol", help="Stock code; resolved to the stock asset.")
    thesis_check.add_argument("--user-id", help="Include this user's personal theses.")
    thesis_check.add_argument("--thesis-entry-id", help="Check one thesis ledger entry.")
    thesis_check.add_argument("--as-of-date", default=date.today().isoformat())
    thesis_check.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    thesis_check.add_argument("--report-root", default="report")
    exit_plan = commands.add_parser(
        "exit-plan",
        help="Exit plan and behavioral hints for a recorded holding; records the sell conditions.",
    )
    exit_target = exit_plan.add_mutually_exclusive_group(required=True)
    exit_target.add_argument("--asset-id")
    exit_target.add_argument("--symbol", help="Stock code; resolved to the stock asset.")
    exit_plan.add_argument("--user-id", required=True)
    exit_plan.add_argument("--as-of-date", default=date.today().isoformat())
    exit_plan.add_argument(
        "--alternatives",
        default="",
        help="Comma-separated stock codes of same-risk alternatives to compare implied returns with.",
    )
    exit_plan.add_argument(
        "--intended-action",
        choices=("hold", "add", "reduce", "exit"),
        help="What the user says they intend to do; drives the behavioral hints.",
    )
    exit_plan.add_argument("--no-record", action="store_true")
    exit_plan.add_argument("--no-remote", action="store_true")
    exit_plan.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    exit_plan.add_argument("--report-root", default="report")
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
    valuation_position = commands.add_parser(
        "valuation-position",
        help="A stock's PE/PB/PS percentiles against its own history and Shenwan peers.",
    )
    valuation_position.add_argument("--symbol", required=True)
    valuation_position.add_argument("--as-of-date", default=date.today().isoformat())
    valuation_position.add_argument("--years", type=int, default=8)
    valuation_position.add_argument("--no-remote", action="store_true")
    valuation_position.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    valuation_position.add_argument("--report-root", default="report")
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
    sector_archive = commands.add_parser(
        "sector-archive",
        help="Build a Shenwan industry archive as of a date and record it.",
    )
    sector_archive.add_argument("--code", required=True, help="Shenwan industry code, e.g. 801120.")
    sector_archive.add_argument("--as-of-date", default=date.today().isoformat())
    sector_archive.add_argument(
        "--no-record",
        action="store_true",
        help="Do not record the state assessment in the ledger.",
    )
    sector_archive.add_argument("--no-remote", action="store_true")
    sector_archive.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    sector_archive.add_argument("--report-root", default="report")
    valuation_model = commands.add_parser(
        "valuation-model",
        help="Implied expectations (reverse DCF / PB-ROE) and scenario values for one stock; records them.",
    )
    valuation_model.add_argument("--symbol", required=True)
    valuation_model.add_argument("--as-of-date", default=date.today().isoformat())
    valuation_model.add_argument("--erp", type=float, help="Equity risk premium override, default 0.05.")
    valuation_model.add_argument("--terminal-growth", type=float, help="Terminal growth override, default 0.02.")
    valuation_model.add_argument(
        "--no-record",
        action="store_true",
        help="Do not record the assessment in the ledger.",
    )
    valuation_model.add_argument("--no-remote", action="store_true")
    valuation_model.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    valuation_model.add_argument("--report-root", default="report")
    discover = commands.add_parser(
        "discover",
        help="Multi-strategy discovery funnel in a scope, with base packs for the top candidates.",
    )
    discover.add_argument(
        "--scope",
        help="industry:<Shenwan code>, index:<index code>, market, or symbols:<code,...>.",
    )
    discover.add_argument("--label", help="Readable scope name, e.g. the theme a symbol list stands for.")
    discover.add_argument("--as-of-date", default=date.today().isoformat())
    discover.add_argument("--strategies", help="Comma-separated strategy IDs; default all.")
    discover.add_argument("--base-packs", type=int, default=DEFAULT_BASE_PACKS)
    discover.add_argument("--no-history", action="store_true", help="Skip rule history replay.")
    discover.add_argument("--no-remote", action="store_true")
    discover.add_argument("--limit", type=int, default=20, help="Candidates printed; 0 prints all.")
    discover.add_argument("--output", help="Write the full run JSON to this path.")
    discover.add_argument("--run-id", help="Read a stored run instead of running.")
    discover.add_argument("--symbols", help="With --run-id: per-strategy outcome of these members.")
    discover.add_argument("--list-strategies", action="store_true")
    discover.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    discover.add_argument("--report-root", default="report")
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
        "--cash",
        type=float,
        help="Uninvested cash the user stated, in yuan, for the portfolio recipes.",
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
    holding_subject = record_holding.add_mutually_exclusive_group(required=True)
    holding_subject.add_argument("--asset-id")
    holding_subject.add_argument("--symbol", help="Six-digit code; combine with --asset-type when the code is shared.")
    record_holding.add_argument(
        "--asset-type",
        default=ASSET_TYPE_STOCK,
        help="Asset type for --symbol: stock (default), open_fund or exchange_fund.",
    )
    record_holding.add_argument("--quantity", required=True, type=float)
    record_holding.add_argument("--average-cost", type=float)
    record_holding.add_argument(
        "--first-bought-on",
        help="Date of the first purchase as the user states it (YYYY-MM-DD).",
    )
    record_holding.add_argument("--currency", default="CNY")
    record_holding.add_argument("--note", default="")
    record_holding.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_holding.add_argument("--report-root", default="report")
    record_preference = commands.add_parser(
        "record-preference",
        help=(
            "Record the next version of a user's portfolio preference: a preset profile, "
            "or confirmed aggressiveness meanings applied to the latest version."
        ),
    )
    record_preference.add_argument("--user-id", required=True)
    preference_basis = record_preference.add_mutually_exclusive_group(required=True)
    preference_basis.add_argument(
        "--profile",
        choices=[item.value for item in RiskProfile],
        help="Start from (or restate to) a preset profile.",
    )
    preference_basis.add_argument(
        "--meaning",
        action="append",
        choices=[item.value for item in AggressivenessMeaning],
        help="Confirmed meaning of 'more aggressive'; repeat for several.",
    )
    record_preference.add_argument(
        "--horizon",
        choices=[item.value for item in PreferenceHorizon],
        help="Required with --profile.",
    )
    record_preference.add_argument(
        "--statement",
        required=True,
        help="The user's own words this version is based on.",
    )
    record_preference.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="User-stated value overriding the derived one, e.g. max_drawdown=0.25.",
    )
    record_preference.add_argument("--liquidity-need-amount")
    record_preference.add_argument("--liquidity-need-by")
    record_preference.add_argument("--as-of-date", default=date.today().isoformat())
    record_preference.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the derived version without recording it, for the user to confirm.",
    )
    record_preference.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_preference.add_argument("--report-root", default="report")
    diagnose = commands.add_parser(
        "portfolio-diagnose",
        help="Diagnose a user's recorded holdings: exposures, overlap, correlation, risk contribution, stress.",
    )
    diagnose.add_argument("--user-id", required=True)
    diagnose.add_argument("--as-of-date", default=date.today().isoformat())
    diagnose.add_argument("--cash", type=float, help="Uninvested cash the user stated, in yuan.")
    diagnose.add_argument("--no-remote", action="store_true")
    diagnose.add_argument("--no-report", action="store_true")
    diagnose.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    diagnose.add_argument("--report-root", default="report")
    allocation = commands.add_parser(
        "allocation-proposal",
        help="Target equity range and risk-budget reference weights from the user's latest preference.",
    )
    allocation.add_argument("--user-id", required=True)
    allocation.add_argument("--as-of-date", default=date.today().isoformat())
    allocation.add_argument("--cash", type=float, help="Uninvested cash the user stated, in yuan.")
    allocation.add_argument("--no-remote", action="store_true")
    allocation.add_argument("--no-report", action="store_true")
    allocation.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    allocation.add_argument("--report-root", default="report")
    adjustment = commands.add_parser(
        "adjustment-proposal",
        help="Target weight ranges, a stepwise migration path with costs, and the adjusted portfolio's risk.",
    )
    adjustment.add_argument("--user-id", required=True)
    adjustment.add_argument("--as-of-date", default=date.today().isoformat())
    adjustment.add_argument("--cash", type=float, help="Uninvested cash the user stated, in yuan.")
    adjustment.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="SYMBOL[:ASSET_TYPE]",
        help="Researched or user-named asset to add; default asset type stock. Replaces the default broad ETFs.",
    )
    adjustment.add_argument("--no-remote", action="store_true")
    adjustment.add_argument("--no-report", action="store_true")
    adjustment.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    adjustment.add_argument("--report-root", default="report")
    portfolio_plan = commands.add_parser(
        "portfolio-plan",
        help="Assemble the portfolio plan (migration steps, recorded actions, review nodes, targets); --record to ledger it.",
    )
    portfolio_plan.add_argument("--user-id", required=True)
    portfolio_plan.add_argument("--as-of-date", default=date.today().isoformat())
    portfolio_plan.add_argument("--cash", type=float)
    portfolio_plan.add_argument("--candidate", action="append", default=[], metavar="SYMBOL[:ASSET_TYPE]")
    portfolio_plan.add_argument(
        "--record",
        action="store_true",
        help="Record the plan and its new actions after the user confirmed them.",
    )
    portfolio_plan.add_argument("--no-remote", action="store_true")
    portfolio_plan.add_argument("--no-report", action="store_true")
    portfolio_plan.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    portfolio_plan.add_argument("--report-root", default="report")
    record_trade = commands.add_parser(
        "record-trade",
        help=(
            "Log one trade the user says they made (optional trade log for decision review); "
            "does not change holdings, update those with record-holding."
        ),
    )
    record_trade.add_argument("--user-id", required=True)
    trade_subject = record_trade.add_mutually_exclusive_group(required=True)
    trade_subject.add_argument("--asset-id")
    trade_subject.add_argument("--symbol")
    record_trade.add_argument("--asset-type", default=ASSET_TYPE_STOCK)
    record_trade.add_argument("--side", required=True, choices=[item.value for item in TradeSide])
    record_trade.add_argument("--date", required=True, help="Trade date YYYY-MM-DD.")
    record_trade.add_argument("--quantity", required=True, help="Units traded, positive.")
    record_trade.add_argument("--price")
    record_trade.add_argument("--note", default="")
    record_trade.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_trade.add_argument("--report-root", default="report")
    decision_review = commands.add_parser(
        "decision-review",
        help=(
            "Review settled ledger judgements (hit rate, timing contribution), plan review nodes and execution, "
            "and compare the stated preference with measured risk and logged trades."
        ),
    )
    decision_review.add_argument("--user-id", required=True)
    decision_review.add_argument("--as-of-date", default=date.today().isoformat())
    decision_review.add_argument("--cash", type=float)
    decision_review.add_argument(
        "--no-calibration",
        action="store_true",
        help="Skip the preference calibration (no price fetching).",
    )
    decision_review.add_argument("--no-remote", action="store_true")
    decision_review.add_argument("--no-report", action="store_true")
    decision_review.add_argument(
        "--include-dev-seed",
        action="store_true",
        help="Count development sample judgements (source_ref dev-seed:) too; for development checks only.",
    )
    decision_review.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    decision_review.add_argument("--report-root", default="report")
    routine = commands.add_parser(
        "routine",
        help=(
            "Manual upkeep in one run: settle due ledger checkpoints, refresh market and macro archives, "
            "check the user's active theses and list due plan review nodes; follow next_actions."
        ),
    )
    routine.add_argument("--user-id", required=True)
    routine.add_argument("--as-of-date", default=date.today().isoformat())
    routine.add_argument("--no-remote", action="store_true")
    routine.add_argument(
        "--skip-archives",
        action="store_true",
        help="Do not rebuild the market and macro archives.",
    )
    routine.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    routine.add_argument("--report-root", default="report")
    strategy_archive = commands.add_parser(
        "strategy-archive",
        help="List strategy archives, show one, register a strategy version, or move its lifecycle state.",
    )
    strategy_archive.add_argument("--strategy-id")
    strategy_archive.add_argument("--version")
    strategy_archive.add_argument("--register-json", help="Path to a StrategyArchive JSON file.")
    strategy_archive.add_argument("--templates", action="store_true", help="List strategy templates.")
    strategy_archive.add_argument("--from-template", help="Register a strategy as template + parameters.")
    strategy_archive.add_argument("--asset", action="append", default=[], metavar="SYMBOL:TYPE[:ROLE]")
    strategy_archive.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    strategy_archive.add_argument("--name")
    strategy_archive.add_argument(
        "--transition",
        choices=[item.value for item in LifecycleState],
        help="Target lifecycle state (needs --strategy-id and --reason).",
    )
    strategy_archive.add_argument("--reason", default="")
    strategy_archive.add_argument("--evidence-ref", action="append", default=[])
    strategy_archive.add_argument("--as-of-date", default=date.today().isoformat())
    strategy_archive.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    strategy_archive.add_argument("--report-root", default="report")
    strategy_backtest = commands.add_parser(
        "strategy-backtest",
        help="Backtest an archived strategy with costs, capacity and four simple benchmarks; logs the attempt.",
    )
    strategy_backtest.add_argument("--strategy-id", required=True)
    strategy_backtest.add_argument("--version")
    strategy_backtest.add_argument("--start-date")
    strategy_backtest.add_argument("--end-date")
    strategy_backtest.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    strategy_backtest.add_argument("--initial-cash", type=float, default=1_000_000.0)
    strategy_backtest.add_argument("--lots", action="store_true", help="Trade whole lots of 100.")
    strategy_backtest.add_argument("--no-remote", action="store_true")
    strategy_backtest.add_argument("--as-of-date", default=date.today().isoformat())
    strategy_backtest.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    strategy_backtest.add_argument("--report-root", default="report")
    strategy_evaluate = commands.add_parser(
        "strategy-evaluate",
        help="Complete validation: rolling out-of-sample, parameter neighbourhood and deflated significance.",
    )
    strategy_evaluate.add_argument("--strategy-id", required=True)
    strategy_evaluate.add_argument("--version")
    strategy_evaluate.add_argument("--start-date")
    strategy_evaluate.add_argument("--end-date")
    strategy_evaluate.add_argument("--train-years", type=float, default=3.0)
    strategy_evaluate.add_argument("--test-years", type=float, default=2.0)
    strategy_evaluate.add_argument("--gap-days", type=int, default=21)
    strategy_evaluate.add_argument("--lots", action="store_true", help="Trade whole lots of 100.")
    strategy_evaluate.add_argument("--no-remote", action="store_true")
    strategy_evaluate.add_argument("--as-of-date", default=date.today().isoformat())
    strategy_evaluate.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    strategy_evaluate.add_argument("--report-root", default="report")
    strategy_track = commands.add_parser(
        "strategy-track",
        help="Record today's forward signal of a strategy in forward tracking (or all of them) and its forward record.",
    )
    strategy_track.add_argument("--strategy-id")
    strategy_track.add_argument("--version")
    strategy_track.add_argument("--all", action="store_true", help="Track every strategy in forward tracking or enabled.")
    strategy_track.add_argument("--no-remote", action="store_true")
    strategy_track.add_argument("--as-of-date", default=date.today().isoformat())
    strategy_track.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    strategy_track.add_argument("--report-root", default="report")
    for name, epilog in EPILOGS.items():
        command = commands.choices[name]
        command.epilog = epilog
        command.formatter_class = argparse.RawDescriptionHelpFormatter
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
    # Before parsing: --help prints Chinese epilogs and exits inside parse_args.
    if stdout is None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if stderr is None and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser.parse_args(argv)
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
        if args.command == "record-thesis":
            payload = container.thesis.record(
                InvestmentThesis.from_dict(
                    json.loads(Path(args.thesis_json).read_text(encoding="utf-8"))
                )
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command in ("thesis-check", "exit-plan"):
            asset_id = args.asset_id
            if asset_id is None:
                stocks = container.master_data.find_assets(
                    standard_symbol=args.symbol,
                    asset_type=ASSET_TYPE_STOCK,
                )
                if len(stocks) != 1:
                    print(f"无法唯一识别股票 {args.symbol}；用 asset --symbol 查 asset_id", file=sys.stderr)
                    return 2
                asset_id = stocks[0].asset_id
            if args.command == "exit-plan":
                payload = container.exit_plan.run(
                    user_id=args.user_id,
                    asset_id=asset_id,
                    as_of_date=args.as_of_date,
                    alternatives=tuple(item for item in args.alternatives.split(",") if item),
                    intended_action=args.intended_action,
                    record=not args.no_record,
                    allow_remote=not args.no_remote,
                )
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
                return 0
            payload = container.thesis.check(
                asset_id,
                as_of_date=args.as_of_date,
                user_id=args.user_id,
                thesis_entry_id=args.thesis_entry_id,
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
        if args.command == "valuation-position":
            payload = container.valuation_position.position(
                args.symbol,
                as_of_date=args.as_of_date,
                years=args.years,
                allow_remote=not args.no_remote,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "discover":
            payload = _discover_payload(container, args)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0 if payload is not None else 2
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
        if args.command == "sector-archive":
            payload = container.sector_state.run(
                args.as_of_date,
                record=not args.no_record,
                allow_remote=not args.no_remote,
                code=args.code,
            )
            print(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                file=output,
            )
            return 0
        if args.command == "valuation-model":
            payload = container.valuation_models.run(
                args.symbol,
                args.as_of_date,
                equity_risk_premium=args.erp,
                terminal_growth=args.terminal_growth,
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
                cash=args.cash,
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
            asset_id = args.asset_id
            if asset_id is None:
                matches = _symbol_matches(container, args.symbol, args.asset_type)
                if len(matches) != 1:
                    print(
                        f"无法唯一识别 {args.asset_type} {args.symbol}（找到 {len(matches)} 个）；核对代码与 --asset-type，或用 asset --symbol 查 asset_id",
                        file=sys.stderr,
                    )
                    return 2
                asset_id = matches[0].asset_id
            container.portfolio_risk.research_state.upsert_holding(
                container.user_space.context_for_user(args.user_id),
                asset_id=asset_id,
                quantity=args.quantity,
                average_cost=args.average_cost,
                currency=args.currency,
                note=args.note,
                first_bought_on=args.first_bought_on,
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
        if args.command == "record-preference":
            print(
                json.dumps(
                    _record_preference(container, args),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=output,
            )
            return 0
        if args.command == "record-trade":
            asset_id = args.asset_id
            if asset_id is None:
                matches = _symbol_matches(container, args.symbol, args.asset_type)
                if len(matches) != 1:
                    print(
                        f"无法唯一识别 {args.asset_type} {args.symbol}（找到 {len(matches)} 个）；核对代码与 --asset-type，或用 asset --symbol 查 asset_id",
                        file=sys.stderr,
                    )
                    return 2
                asset_id = matches[0].asset_id
            trade = TradeRecord(
                user_id=args.user_id,
                asset_id=asset_id,
                side=TradeSide(args.side),
                trade_date=args.date,
                quantity=Decimal(args.quantity),
                price=Decimal(args.price) if args.price else None,
                note=args.note,
            )
            added = container.portfolio_risk.record_trade(
                container.user_space.context_for_user(args.user_id), trade
            )
            print(
                json.dumps({"trade": trade.to_dict(), "recorded": added}, ensure_ascii=False, sort_keys=True),
                file=output,
            )
            return 0
        if args.command == "decision-review":
            payload = container.decision_review.run(
                user_id=args.user_id,
                as_of_date=args.as_of_date,
                cash=args.cash,
                calibrate=not args.no_calibration,
                allow_remote=not args.no_remote,
                write_report=not args.no_report,
                include_dev_seed=args.include_dev_seed,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "strategy-archive":
            service = container.strategy_archive
            try:
                if args.templates:
                    payload = service.templates()
                elif args.from_template:
                    if not args.strategy_id:
                        print("--from-template 需要 --strategy-id", file=error_output)
                        return 2
                    payload = service.register_from_template(
                        args.from_template,
                        strategy_id=args.strategy_id,
                        as_of_date=args.as_of_date,
                        universe_assets=tuple(args.asset),
                        parameters=dict(item.split("=", 1) for item in args.set),
                        name=args.name,
                        version=args.version or "v1",
                    )
                elif args.register_json:
                    payload = service.register(
                        json.loads(Path(args.register_json).read_text(encoding="utf-8")), args.as_of_date
                    )
                elif args.transition:
                    if not args.strategy_id:
                        print("--transition 需要 --strategy-id", file=error_output)
                        return 2
                    payload = service.transition(
                        args.strategy_id,
                        LifecycleState(args.transition),
                        as_of_date=args.as_of_date,
                        reason=args.reason,
                        version=args.version,
                        evidence_refs=tuple(args.evidence_ref),
                    )
                elif args.strategy_id:
                    payload = service.show(args.strategy_id, args.as_of_date, args.version)
                else:
                    payload = service.list_archives(args.as_of_date)
            except StrategyArchiveError as exc:
                print(str(exc), file=error_output)
                return 2
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0 if payload.get("status") != "rejected" else 3
        if args.command == "strategy-backtest":
            overrides = dict(item.split("=", 1) for item in args.set)
            try:
                payload = container.strategy_backtest.run(
                    args.strategy_id,
                    as_of_date=args.as_of_date,
                    version=args.version,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    overrides=overrides,
                    initial_cash=args.initial_cash,
                    lots=args.lots,
                    allow_remote=not args.no_remote,
                )
            except StrategyArchiveError as exc:
                print(str(exc), file=error_output)
                return 2
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "strategy-evaluate":
            try:
                payload = container.strategy_evaluate.evaluate(
                    args.strategy_id,
                    as_of_date=args.as_of_date,
                    version=args.version,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    train_years=args.train_years,
                    test_years=args.test_years,
                    gap_days=args.gap_days,
                    lots=args.lots,
                    allow_remote=not args.no_remote,
                )
            except StrategyArchiveError as exc:
                print(str(exc), file=error_output)
                return 2
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "strategy-track":
            try:
                if args.all:
                    payload = {
                        "as_of_date": args.as_of_date,
                        "strategies": container.strategy_track.track_all(
                            as_of_date=args.as_of_date, allow_remote=not args.no_remote
                        ),
                    }
                elif args.strategy_id:
                    payload = container.strategy_track.track(
                        args.strategy_id,
                        as_of_date=args.as_of_date,
                        version=args.version,
                        allow_remote=not args.no_remote,
                    )
                else:
                    print("strategy-track 需要 --strategy-id 或 --all", file=error_output)
                    return 2
            except StrategyArchiveError as exc:
                print(str(exc), file=error_output)
                return 2
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "routine":
            payload = container.routine.run(
                user_id=args.user_id,
                as_of_date=args.as_of_date,
                allow_remote=not args.no_remote,
                archives=not args.skip_archives,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command in ("adjustment-proposal", "portfolio-plan"):
            candidates = tuple(
                (symbol, asset_type or ASSET_TYPE_STOCK)
                for symbol, _, asset_type in (item.partition(":") for item in args.candidate)
            )
            common = {
                "user_id": args.user_id,
                "as_of_date": args.as_of_date,
                "cash": args.cash,
                "candidates": candidates,
                "allow_remote": not args.no_remote,
                "write_report": not args.no_report,
            }
            payload = (
                container.adjustment_proposal.run(**common)
                if args.command == "adjustment-proposal"
                else container.portfolio_plan.run(**common, record=args.record)
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command in ("portfolio-diagnose", "allocation-proposal"):
            service = (
                container.portfolio_diagnosis
                if args.command == "portfolio-diagnose"
                else container.allocation_proposal
            )
            payload = service.run(
                user_id=args.user_id,
                as_of_date=args.as_of_date,
                cash=args.cash,
                allow_remote=not args.no_remote,
                write_report=not args.no_report,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
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
            "error: invalid input or execution failed: "
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


def _record_preference(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> dict[str, object]:
    context = container.user_space.context_for_user(args.user_id)
    service = container.portfolio_risk
    previous = service.latest_preference(context)
    overrides = dict(_parse_key_value(item) for item in args.set)
    if args.profile:
        if not args.horizon:
            raise ValueError("--profile needs --horizon")
        preference = initial_preference(
            user_id=args.user_id,
            recorded_on=args.as_of_date,
            risk_profile=RiskProfile(args.profile),
            horizon=PreferenceHorizon(args.horizon),
            user_statement=args.statement,
            overrides=overrides,
            liquidity_need_amount=(
                Decimal(args.liquidity_need_amount) if args.liquidity_need_amount else None
            ),
            liquidity_need_by=args.liquidity_need_by,
            previous=previous,
        )
    else:
        if previous is None:
            raise ValueError(
                "no recorded preference to make more aggressive: record the current one with --profile first"
            )
        if args.horizon:
            overrides["horizon"] = args.horizon
        preference = derive_preference(
            previous,
            meanings=tuple(AggressivenessMeaning(item) for item in args.meaning),
            recorded_on=args.as_of_date,
            user_statement=args.statement,
            overrides=overrides,
        )
    if not args.dry_run:
        service.record_preference(context, preference)
    return {
        "recorded": not args.dry_run,
        "preference": preference.to_dict(),
        "readable": preference.describe()["lines"],
        "changes": preference_changes(previous, preference),
        "personal_ref": preference.preference_id,
    }


def _parse_key_value(value: str) -> tuple[str, str]:
    key, separator, item = value.partition("=")
    if not separator or not key.strip() or not item.strip():
        raise ValueError(f"expected FIELD=VALUE, got {value!r}")
    return key.strip(), item.strip()


def _discover_payload(
    container: ResearchOrchestrationContainer,
    args: argparse.Namespace,
) -> object:
    runner = container.discovery_funnel
    if args.list_strategies:
        return [item.describe() for item in STRATEGIES]
    if args.run_id:
        run = runner.funnel.get(args.run_id)
        if run is None:
            return None
        if args.symbols:
            wanted = [item.strip().zfill(6) for item in args.symbols.split(",") if item.strip()]
            return {symbol: run["members"].get(symbol, "不在本次范围内") for symbol in wanted}
    else:
        if not args.scope:
            raise ValueError("discover needs --scope, --run-id or --list-strategies")
        run = runner.run(
            DiscoveryScope.parse(args.scope, label=args.label),
            as_of_date=args.as_of_date,
            base_packs=args.base_packs,
            strategy_ids=tuple(args.strategies.split(",")) if args.strategies else None,
            history=not args.no_history,
            allow_remote=not args.no_remote,
        )
    if args.output:
        Path(args.output).write_text(
            json.dumps(run, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    payload = {key: value for key, value in run.items() if key != "members"}
    if args.limit:
        payload["candidates"] = run["candidates"][: args.limit]
    return payload


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


def _symbol_matches(container: ResearchOrchestrationContainer, symbol: str, asset_type: str) -> list:
    """Persisted identities for a code, registering them from the listings first when none is stored."""

    matches = container.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
    if not matches:
        container.data_fetch.fetch_and_store_identities(symbol, asset_type=asset_type)
        matches = container.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
    return matches


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
