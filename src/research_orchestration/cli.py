"""Command-line entry point for unified single-asset research."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, TextIO

from src.event_intel import SOURCES as INTEL_SOURCES, Observable
from src.research_debate import PERSONAS
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
from src.financial_data import SQLiteFinancialDataRepository
from src.research_orchestration.entry_timing import EntryTimingService
from src.research_orchestration.financial_fields import DEFAULT_FIELDS, available_fields, field_series
from src.research_orchestration.us_valuation import USValuationService
from src.data_src.process_executor import run_provider_task
from src.portfolio_risk import (
    AggressivenessMeaning,
    HoldingWeights,
    PreferenceHorizon,
    RiskProfile,
    TradeRecord,
    TradeSide,
    WeightItem,
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
from .agent_protocol import failure, read_json_input
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
    parser.add_argument("--envelope", action="store_true", help="Wrap any result in the common Agent JSON protocol.")
    parser.add_argument("--describe", metavar="COMMAND", help="Return machine-readable command parameters without running it.")
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
    asset.add_argument("--market", choices=("us", "hk"), help="us: a US ticker (NVDA, SMH), registered from Nasdaq on first use; hk: a Hong Kong code (00700).")
    asset.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    asset.add_argument("--report-root", default="report")
    prices = commands.add_parser(
        "prices",
        help="Total-return price history of stocks or exchange funds (A-share or US): return, drawdown, range.",
    )
    price_target = prices.add_mutually_exclusive_group(required=True)
    price_target.add_argument("--symbols", help="Comma-separated codes or US tickers.")
    price_target.add_argument("--asset-id")
    prices.add_argument("--market", choices=("us", "hk"))
    prices.add_argument("--asset-type", choices=("stock", "exchange_fund"))
    prices.add_argument("--start-date", required=True)
    prices.add_argument("--end-date", default=date.today().isoformat())
    prices.add_argument("--rows", action="store_true", help="Include the daily rows.")
    prices.add_argument("--no-remote", action="store_true")
    prices.add_argument("--request-id", help="Stable batch ID for checkpoint/resume with unchanged items and parameters.")
    prices.add_argument("--retry-failed", action="store_true")
    prices.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    prices.add_argument("--report-root", default="report")
    intel = commands.add_parser(
        "intel",
        help="Dated global news, SEC filings and Federal Register rules; page text; evidence links.",
    )
    intel_mode = intel.add_mutually_exclusive_group(required=True)
    intel_mode.add_argument("--source", choices=INTEL_SOURCES)
    intel_mode.add_argument("--fetch-url", help="Readable text of one page (official sources, SEC filings).")
    intel_mode.add_argument("--item-ids", help="Show stored items, comma-separated intel:<id>.")
    intel_mode.add_argument("--add-evidence-url", help="Register a link found elsewhere as citable evidence.")
    intel_mode.add_argument(
        "--read-items", help="Read the articles of stored items (comma-separated intel:<id>), cached, rate-limit aware."
    )
    intel_mode.add_argument("--read-urls", help="Read several pages (comma-separated URLs), cached, rate-limit aware.")
    intel.add_argument("--refresh", action="store_true", help="--read-*: fetch again even when a page is cached.")
    intel.add_argument("--query")
    intel.add_argument("--ticker", help="sec-filings: US ticker.")
    intel.add_argument("--forms", default="", help="SEC forms, comma-separated (8-K,10-Q,10-K,4).")
    intel.add_argument("--agency", help="federal-register agency slug, e.g. industry-and-security-bureau.")
    intel.add_argument("--since")
    intel.add_argument("--until")
    intel.add_argument("--lang", choices=("en", "zh"), default="en")
    intel.add_argument("--limit", type=int, default=20)
    intel.add_argument("--max-chars", type=int, default=20000)
    intel.add_argument("--title", help="--add-evidence-url: title of the page.")
    intel.add_argument("--published-on", help="--add-evidence-url: publication date YYYY-MM-DD.")
    intel.add_argument("--publisher")
    intel.add_argument("--no-remote", action="store_true")
    intel.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    intel.add_argument("--report-root", default="report")
    define_observable = commands.add_parser(
        "define-observable",
        help="Register an event or unstructured value to track by evidence, with its baseline observation.",
    )
    define_observable.add_argument("--json", required=True, dest="json_path")
    define_observable.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    define_observable.add_argument("--report-root", default="report")
    record_observation = commands.add_parser(
        "record-observation",
        help="Append one evidence-backed observation of a registered observable.",
    )
    record_observation.add_argument("--json", required=True, dest="json_path")
    record_observation.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_observation.add_argument("--report-root", default="report")
    observables = commands.add_parser(
        "observables",
        help="List tracked observables, or one observable with its observations.",
    )
    observables.add_argument("--observable-id")
    observables.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    observables.add_argument("--report-root", default="report")
    guidance = commands.add_parser(
        "position-guidance",
        help="Recommended weight of one holding (e.g. employer stock) vs the previous recommendation: add, reduce or hold.",
    )
    guidance_target = guidance.add_mutually_exclusive_group(required=True)
    guidance_target.add_argument("--symbol")
    guidance_target.add_argument("--asset-id")
    guidance.add_argument("--market", choices=("us", "hk"))
    guidance.add_argument("--asset-type", choices=("stock", "exchange_fund"))
    guidance.add_argument("--user-id", required=True)
    guidance.add_argument("--history", action="store_true", help="List earlier recommendations and their settlements.")
    guidance.add_argument("--target", help="Recommended weight as a decimal, e.g. 0.05.")
    guidance.add_argument("--range", dest="weight_range", help="low,high as decimals, e.g. 0.03,0.08.")
    guidance.add_argument("--review-on", help="Next review date.")
    guidance.add_argument("--basis", default="", help="Why this weight.")
    guidance.add_argument("--benchmark-symbol", help="Same-market benchmark the call is judged against.")
    guidance.add_argument("--refs", default="", help="Comma-separated ledger entry IDs the recommendation rests on.")
    guidance.add_argument("--current", help="User's actual weight now as a decimal; the baseline of a first recommendation.")
    guidance.add_argument("--as-of-date", default=date.today().isoformat())
    guidance.add_argument("--dry-run", action="store_true")
    guidance.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    guidance.add_argument("--report-root", default="report")
    debate = commands.add_parser(
        "debate",
        help="Debate between user, Agent and investor personas: positions, rebuttals, resolutions, signposts.",
    )
    debate.add_argument(
        "--action",
        required=True,
        choices=(
            "start", "position", "rebut", "answer", "resolve", "link", "signpost", "conclude", "amend",
            "show", "list", "personas", "brief", "import", "scorecard", "agenda", "accept-position", "rules", "cast",
        ),
    )
    debate.add_argument("--debate-id")
    debate.add_argument("--json", dest="json_path", help="Input JSON for start/position/rebut/answer/resolve/link/signpost/conclude/import.")
    debate.add_argument("--persona", help="Persona ID for brief and import.")
    debate.add_argument("--layer", choices=("world", "asset"), help="Filter for personas.")
    debate.add_argument("--user-id", default="user:default")
    debate.add_argument("--status", choices=("open", "concluded"), help="Filter for list.")
    debate.add_argument("--as-of-date", default=date.today().isoformat(), help="For agenda.")
    debate.add_argument("--days", type=int, default=7, help="Agenda look-back window.")
    debate.add_argument("--no-remote", action="store_true")
    debate.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    debate.add_argument("--report-root", default="report")
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
    valuation_model.add_argument("--market", choices=("us",), help="us: value a US stock from SEC XBRL facts.")
    valuation_model.add_argument("--risk-free", type=float, help="US only: 10-year yield as a decimal; default FRED DGS10.")
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
    entry_timing = commands.add_parser(
        "entry-timing",
        help="Pace one decided trade inside its execution window from past days in the same price state.",
    )
    entry_timing.add_argument("--symbol", required=True)
    entry_timing.add_argument("--market", choices=("us", "hk"))
    entry_timing.add_argument("--asset-type", choices=("stock", "exchange_fund"))
    entry_timing.add_argument("--side", choices=("buy", "sell"), default="buy")
    entry_timing.add_argument("--window-days", type=int, default=21, help="Execution window in trading days.")
    entry_timing.add_argument("--years", type=int, default=15, help="History length used for analogues.")
    entry_timing.add_argument("--as-of-date", default=date.today().isoformat())
    entry_timing.add_argument("--no-remote", action="store_true")
    entry_timing.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    entry_timing.add_argument("--report-root", default="report")
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
    user_context.add_argument("--as-of-date", default=date.today().isoformat())
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
    record_weights = commands.add_parser(
        "record-weights",
        help="Record the user's holdings as shares of the portfolio when they give no quantities or amounts.",
    )
    record_weights.add_argument("--user-id", required=True)
    record_weights.add_argument("--json", required=True, help="File with as_of_date, statement and items.")
    record_weights.add_argument("--dry-run", action="store_true")
    record_weights.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    record_weights.add_argument("--report-root", default="report")
    fields = commands.add_parser(
        "financial-fields",
        help="Raw statement fields (contract liabilities, inventory, payables, other income...) over recent quarters.",
    )
    fields.add_argument("--symbol", required=True, help="A-share stock code.")
    fields.add_argument("--fields", help="Comma-separated source labels; default is the leading-indicator set.")
    fields.add_argument("--quarters", type=int, default=8)
    fields.add_argument("--list", action="store_true", help="List the labels stored for this stock.")
    fields.add_argument("--as-of-date", default=date.today().isoformat())
    fields.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    fields.add_argument("--report-root", default="report")
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
    routine.add_argument("--mode", choices=("inspect", "maintain"), default="inspect")
    routine.add_argument("--only", help="Comma-separated maintenance steps; inspect only supports calendar.")
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
    for name in ("plan-register", "plan-show", "calendar", "task-resolve", "plan-review"):
        execution = commands.add_parser(name, help="Execution plan registry and global calendar (offline).")
        execution.add_argument("--user-id", required=True)
        execution.add_argument("--db-path", default="data/database/wealth_farm.sqlite3")
        execution.add_argument("--report-root", default="report")
        if name in ("plan-register", "task-resolve"):
            execution.add_argument("--json", required=True, help="UTF-8 JSON file; see the schema below.")
        if name == "plan-register":
            execution.add_argument("--key", required=True, help="Stable registration key; reuse for retries and revisions.")
            execution.add_argument("--expected-revision", type=int)
        if name in ("plan-show", "plan-review"):
            execution.add_argument("--plan-id", required=name == "plan-review")
        if name == "plan-show":
            execution.add_argument("--task-id")
            execution.add_argument("--history", action="store_true")
            execution.add_argument("--audit", action="store_true")
        if name in ("calendar", "plan-show", "plan-review"):
            execution.add_argument("--known-at", help="Historical knowledge cutoff, as an offset-aware ISO timestamp.")
        if name in ("calendar", "plan-review"):
            execution.add_argument("--at", help="ISO date (whole local day) or offset-aware timestamp; default now.")
        if name == "calendar":
            execution.add_argument("--details", action="store_true")
            execution.add_argument("--fields", help="Comma-separated result fields.")
            execution.add_argument("--limit", type=int, default=100)
            execution.add_argument("--offset", type=int, default=0)
            execution.add_argument("--plan-id")
            execution.add_argument("--task-id")
            execution.add_argument("--through", help="Include future tasks through this date/time, plus unresolved older tasks.")
            execution.add_argument("--include-closed", action="store_true")
    operations = commands.add_parser("operations", help="Preview or recover typed Agent business operations.")
    operations.add_argument("--user-id", required=True)
    operations.add_argument("--json", help="Structured request file, or '-' for stdin; preview unless --apply.")
    operations.add_argument("--request-id", help="Inspect an existing request; --apply resumes a safely retryable request.")
    operations.add_argument("--apply", action="store_true")
    operations.add_argument("--reconcile", action="store_true", help="Record checked evidence for an interrupted/ambiguous step.")
    operations.add_argument("--db-path", default="data/database/wealth_farm.sqlite3")
    operations.add_argument("--report-root", default="report")
    for name, epilog in EPILOGS.items():
        command = commands.choices[name]
        command.epilog = epilog
        command.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser


def _main(
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
        if args.command == "operations":
            if args.reconcile:
                if not args.json or args.apply:
                    raise ValueError("reconcile needs --json and cannot be combined with --apply")
                payload = container.operations.reconcile(user_id=args.user_id, **read_json_input(args.json))
            elif args.json:
                payload = container.operations.run(user_id=args.user_id, request=read_json_input(args.json), apply=args.apply)
            elif args.request_id:
                payload = container.operations.inspect(user_id=args.user_id, request_id=args.request_id)
                if args.apply:
                    payload = container.operations.run(user_id=args.user_id, request=payload["request"], apply=True)
            else:
                raise ValueError("operations needs --json or --request-id")
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0 if payload.get("state") in (None, "completed") else 2
        if args.command in ("plan-register", "plan-show", "calendar", "task-resolve", "plan-review"):
            service = container.execution_plans
            if args.command == "plan-register":
                payload = service.register(user_id=args.user_id, registration_key=args.key,
                    plan=read_json_input(args.json),
                    expected_revision=args.expected_revision)
            elif args.command == "task-resolve":
                payload = service.resolve(user_id=args.user_id,
                    **read_json_input(args.json))
            elif args.command == "calendar":
                payload = service.calendar(user_id=args.user_id, at=args.at, through=args.through,
                    known_at=args.known_at, include_closed=args.include_closed, details=args.details,
                    fields=args.fields.split(",") if args.fields else None, limit=args.limit, offset=args.offset,
                    plan_id=args.plan_id, task_id=args.task_id)
            elif args.command == "plan-review":
                payload = service.review(user_id=args.user_id, plan_id=args.plan_id, at=args.at, known_at=args.known_at)
            else:
                payload = service.show(user_id=args.user_id, plan_id=args.plan_id, known_at=args.known_at,
                    task_id=args.task_id, history=args.history, include_audit=args.audit) if args.plan_id else {
                    "plans": service.registry.list_plans(user_id=args.user_id, known_at=args.known_at), "gaps": []}
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0
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
            if args.market in ("us", "hk"):
                assets = [container.price_history.resolve(args.symbol, market=args.market, asset_type=args.asset_type)]
            else:
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
        if args.command == "prices":
            targets = [args.asset_id] if args.asset_id else [item.strip() for item in args.symbols.split(",") if item.strip()]

            def fetch_price(key):
                asset = container.master_data.get_asset(key) if args.asset_id else container.price_history.resolve(
                    key, market=args.market, asset_type=args.asset_type)
                if asset is None:
                    raise KeyError(f"unknown asset_id {key}")
                return container.price_history.history(asset, start_date=args.start_date, end_date=args.end_date,
                    rows=args.rows, allow_remote=not args.no_remote)

            parameters = {"start_date": args.start_date, "end_date": args.end_date, "market": args.market,
                          "asset_type": args.asset_type, "rows": args.rows, "no_remote": args.no_remote,
                          "asset_id": args.asset_id, "symbols": args.symbols}
            payload = container.batch.run(items=targets, parameters=parameters, execute=fetch_price,
                                          request_id=args.request_id, retry_failed=args.retry_failed)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0 if payload["status"] == "completed" else 2
        if args.command == "intel":
            payload = _intel_payload(container, args)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "define-observable":
            spec = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
            baseline = spec.pop("baseline", None)
            if not isinstance(baseline, dict):
                print("JSON 需要 baseline：观测开始时的值、period_end、available_at、evidence_refs、note", file=sys.stderr)
                return 2
            payload = container.event_intel.define_observable(Observable.from_dict(spec), baseline)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command == "record-observation":
            payload = container.event_intel.record_observation(
                json.loads(Path(args.json_path).read_text(encoding="utf-8"))
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command == "observables":
            print(json.dumps(_observables_payload(container, args), ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command == "position-guidance":
            print(json.dumps(_guidance_payload(container, args), ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
        if args.command == "debate":
            print(json.dumps(_debate_payload(container, args), ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0
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
        if args.command == "valuation-model" and args.market == "us":
            payload = USValuationService(
                fetch=lambda task, spec: run_provider_task("src.data_src.provider_tasks", task, spec, timeout_seconds=120.0),
                price_history=container.price_history,
            ).run(
                args.symbol,
                args.as_of_date,
                risk_free=args.risk_free,
                equity_risk_premium=args.erp,
                terminal_growth=args.terminal_growth,
                allow_remote=not args.no_remote,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
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
        if args.command == "entry-timing":
            payload = EntryTimingService(price_history=container.price_history).run(
                args.symbol,
                as_of_date=args.as_of_date,
                market=args.market,
                asset_type=args.asset_type,
                window_days=args.window_days,
                side=args.side,
                years=args.years,
                allow_remote=not args.no_remote,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), file=output)
            return 0 if "error" not in payload else 2
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
                            container.user_context.describe(user_id, as_of_date=args.as_of_date)
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
        if args.command == "financial-fields":
            asset = container.price_history.resolve(args.symbol, asset_type=ASSET_TYPE_STOCK)
            repository = SQLiteFinancialDataRepository(args.db_path)
            payload = (
                available_fields(repository, asset_id=asset.asset_id, as_of_date=args.as_of_date)
                if args.list
                else field_series(
                    repository,
                    asset_id=asset.asset_id,
                    fields=tuple(item.strip() for item in args.fields.split(",")) if args.fields else DEFAULT_FIELDS,
                    as_of_date=args.as_of_date,
                    quarters=args.quarters,
                )
            )
            print(json.dumps({"asset": asset.display_name, **payload}, ensure_ascii=False, sort_keys=True), file=output)
            return 0
        if args.command == "record-weights":
            print(
                json.dumps(_record_weights(container, args), ensure_ascii=False, sort_keys=True),
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
                mode=args.mode,
                only=tuple(item.strip() for item in args.only.split(",")) if args.only else None,
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
        print(json.dumps(failure(exc, operation=args.command), ensure_ascii=False), file=output)
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


def main(argv: Sequence[str] | None = None, *, container_factory=create_default_container,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Agent protocol adapter. Existing success payloads remain; --envelope makes all shapes uniform."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    output, errors = stdout or sys.stdout, stderr or sys.stderr
    if stdout is None and hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8")
    if stderr is None and hasattr(errors, "reconfigure"):
        errors.reconfigure(encoding="utf-8")
    if "--describe" in arguments:
        position = arguments.index("--describe")
        name = arguments[position + 1] if position + 1 < len(arguments) else ""
        parser = build_parser()
        subcommands = next(action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        if name not in subcommands:
            print(json.dumps(failure(ValueError("unknown command for --describe"), operation="describe")), file=output)
            return 2
        command = subcommands[name]
        payload = {"command": name, "parameters": [{"name": action.dest, "flags": action.option_strings,
            "required": action.required, "choices": action.choices, "default": action.default,
            "help": action.help, "type": getattr(action.type, "__name__", None)} for action in command._actions],
            "notes": command.epilog, "gaps": []}
        print(json.dumps(payload, ensure_ascii=False, default=str), file=output)
        return 0
    envelope = "--envelope" in arguments
    arguments = [argument for argument in arguments if argument != "--envelope"]
    captured, diagnostics = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(diagnostics):
            code = _main(arguments, container_factory=container_factory, stdout=captured, stderr=diagnostics)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code == 0:
            print(captured.getvalue(), end="", file=output)
            return 0
    except Exception as exc:
        code = 2
        captured = io.StringIO(json.dumps(failure(exc, operation=arguments[0] if arguments else "cli"), ensure_ascii=False))
    raw = captured.getvalue()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = {"report_path": raw.strip()} if code == 0 and arguments[:1] == ["run-single"] else {"text": raw.strip()}
        if code:
            payload = failure(ValueError(diagnostics.getvalue().strip() or raw.strip() or "command failed"),
                              operation=arguments[0] if arguments else "cli")
    if envelope:
        wrapped = {"ok": code == 0, "status": payload.get("status", "completed" if code == 0 else "error")
                   if isinstance(payload, dict) else "completed" if code == 0 else "error",
                   "data": payload, "error": payload.get("error") if isinstance(payload, dict) else None,
                   "gaps": payload.get("gaps", []) if isinstance(payload, dict) else [],
                   "next_actions": payload.get("next_actions", []) if isinstance(payload, dict) else []}
        print(json.dumps(wrapped, ensure_ascii=False, default=str), file=output)
    elif code == 0 and raw.strip():
        print(raw, end="", file=output)
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str), file=output)
    if diagnostics.getvalue():
        print(diagnostics.getvalue(), end="", file=errors)
    return code


def _read_research_request(path: str) -> ResearchRequest:
    return ResearchRequest.from_dict(
        read_json_input(path)
    )


def _record_weights(container: ResearchOrchestrationContainer, args: argparse.Namespace) -> dict:
    spec = json.loads(Path(args.json).read_text(encoding="utf-8"))
    items = []
    for raw in spec["items"]:
        weight = Decimal(str(raw["weight"]))
        if raw.get("symbol"):
            asset_type = raw.get("asset_type", ASSET_TYPE_STOCK)
            if raw.get("market") == "us" or asset_type != "open_fund":
                asset = container.price_history.resolve(raw["symbol"], market=raw.get("market"), asset_type=asset_type)
            else:
                matches = _symbol_matches(container, raw["symbol"], asset_type)
                if len(matches) != 1:
                    raise ValueError(f"无法唯一识别 {asset_type} {raw['symbol']}（找到 {len(matches)} 个）")
                asset = matches[0]
            items.append(WeightItem(label=raw.get("label") or asset.display_name or asset.standard_symbol, weight=weight, asset_id=asset.asset_id))
            continue
        proxy = None
        if raw.get("proxy_symbol"):
            proxy = container.price_history.resolve(
                raw["proxy_symbol"], market=raw.get("proxy_market"), asset_type=raw.get("proxy_asset_type")
            ).asset_id
        items.append(WeightItem(label=raw["label"], weight=weight, asset_class=raw["asset_class"], proxy_asset_id=proxy))
    snapshot = HoldingWeights(
        user_id=args.user_id,
        as_of_date=spec.get("as_of_date") or date.today().isoformat(),
        items=tuple(items),
        user_statement=spec["statement"],
    )
    if not args.dry_run:
        container.portfolio_risk.record_weights(container.user_space.context_for_user(args.user_id), snapshot)
    return {
        "recorded": not args.dry_run,
        "snapshot_id": snapshot.snapshot_id,
        "total": str(snapshot.total()),
        "items": [
            {"label": item.label, "weight": str(item.weight), "asset_id": item.asset_id, "asset_class": item.asset_class}
            for item in snapshot.items
        ],
    }


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


def _guidance_payload(container: ResearchOrchestrationContainer, args: argparse.Namespace) -> object:
    resolve = container.price_history.resolve
    asset_id = args.asset_id or resolve(args.symbol, market=args.market, asset_type=args.asset_type).asset_id
    service = container.position_guidance
    if args.history:
        return service.history(user_id=args.user_id, asset_id=asset_id)
    if not (args.target and args.weight_range and args.review_on):
        raise ValueError("记录推荐仓位需要 --target、--range 和 --review-on")
    low, high = (Decimal(item.strip()) for item in args.weight_range.split(","))
    benchmark = resolve(args.benchmark_symbol, market=args.market).asset_id if args.benchmark_symbol else None
    return service.record(
        user_id=args.user_id,
        asset_id=asset_id,
        as_of_date=args.as_of_date,
        target=Decimal(args.target),
        low=low,
        high=high,
        review_on=args.review_on,
        basis=args.basis,
        benchmark_id=benchmark,
        refs=tuple(item.strip() for item in args.refs.split(",") if item.strip()),
        current=Decimal(args.current) if args.current else None,
        dry_run=args.dry_run,
    )


def _debate_payload(container: ResearchOrchestrationContainer, args: argparse.Namespace) -> object:
    workflow = container.debates
    service = workflow.debates
    action = args.action
    if action == "personas":
        return [item.to_dict() for item in PERSONAS.values() if args.layer in (None, item.layer)]
    if action == "list":
        return service.list_debates(user_id=args.user_id, status=args.status)
    if action == "scorecard":
        return workflow.scorecard(user_id=args.user_id, debate_id=args.debate_id)
    if action == "agenda":
        return workflow.agenda(
            user_id=args.user_id, as_of_date=args.as_of_date, days=args.days, allow_remote=not args.no_remote
        )
    if action == "rules":
        return {"rules": [rule for rule in workflow.rules(user_id=args.user_id)
                          if args.debate_id is None or rule["source_id"] == args.debate_id],
                "gaps": [], "notes": ["使用 rule_id/content_hash 引用当前规则；变更后必须复核关联执行节点。"]}
    if action != "start" and not args.debate_id:
        raise ValueError(f"--action {action} 需要 --debate-id")
    if action == "show":
        return workflow.show(args.debate_id)
    if action == "brief":
        if not args.persona:
            raise ValueError("--action brief 需要 --persona（见 --action personas）")
        return service.brief(args.debate_id, args.persona)
    if not args.json_path:
        raise ValueError(f"--action {action} 需要 --json <path>（字段见 debate --help）")
    spec = read_json_input(args.json_path)
    refs = lambda key: tuple(spec.get(key) or ())  # noqa: E731
    if action == "start":
        if not spec.get("personas"):
            raise ValueError("start 需要 personas：本场参与的视角（每层最多 3 个，按本题主要的不确定性挑，见 --action personas 的 use_when）")
        debate = service.start(
            user_id=spec.get("user_id") or args.user_id,
            topic=spec.get("topic", ""),
            trigger=spec.get("trigger", ""),
            as_of_date=spec.get("as_of_date") or date.today().isoformat(),
            evidence_refs=refs("evidence_refs"),
            parent_debate_id=spec.get("parent_debate_id"),
            layers=tuple(spec.get("layers") or ("world",)),
            subjects=refs("subjects"),
            significance=spec.get("significance") or "major",
            personas=refs("personas"),
        )
        return workflow.show(debate.debate_id)
    if action == "cast":
        return service.cast(args.debate_id, personas=refs("personas"), reason=spec.get("reason", ""))
    if action == "import":
        if not args.persona:
            raise ValueError("--action import 需要 --persona")
        return service.import_persona(args.debate_id, args.persona, spec)
    if action == "position":
        item = service.add_position(
            args.debate_id,
            party=spec.get("party", ""),
            statement=spec.get("statement", ""),
            mechanism=spec.get("mechanism", ""),
            horizon=spec.get("horizon", ""),
            falsifiers=refs("falsifiers"),
            evidence_refs=refs("evidence_refs"),
            supersedes=spec.get("supersedes"),
            layer=spec.get("layer") or "world",
            subject=spec.get("subject"),
            original_text=spec.get("original_text"),
            source_position_id=spec.get("source_position_id"),
            user_confirmation=spec.get("user_confirmation"),
        )
    elif action == "accept-position":
        item = service.accept_position(args.debate_id, position_id=spec.get("position_id", ""),
            statement=spec.get("statement", ""), confirmation_ref=spec.get("confirmation_ref", ""))
    elif action == "rebut":
        item = service.rebut(
            args.debate_id,
            position_id=spec.get("position_id", ""),
            party=spec.get("party", ""),
            argument=spec.get("argument", ""),
            kind=spec.get("kind", ""),
            evidence_refs=refs("evidence_refs"),
        )
    elif action == "answer":
        item = service.answer(
            args.debate_id,
            rebuttal_id=spec.get("rebuttal_id", ""),
            accepted=bool(spec.get("accepted")),
            response=spec.get("response", ""),
        )
    elif action == "resolve":
        item = service.resolve(
            args.debate_id,
            position_id=spec.get("position_id", ""),
            status=spec.get("status", ""),
            note=spec.get("note", ""),
            ledger_refs=refs("ledger_refs"),
        )
    elif action == "link":
        item = service.link(args.debate_id, position_id=spec.get("position_id", ""), ledger_refs=refs("ledger_refs"))
    elif action == "signpost":
        item = service.add_signpost(
            args.debate_id,
            observation=spec.get("observation", ""),
            trigger=spec.get("trigger", ""),
            action=spec.get("action", ""),
            refs=refs("refs"),
            position_ids=refs("position_ids"),
            due_on=spec.get("due_on"),
            replaces=spec.get("replaces"),
        )
    elif action == "amend":
        return service.amend(
            args.debate_id,
            reason=spec.get("reason", ""),
            as_of_date=spec.get("as_of_date", ""),
            decisions=tuple(spec.get("decisions") or ()),
        )
    else:
        return service.conclude(
            args.debate_id,
            conclusion=spec.get("conclusion", ""),
            decisions=tuple(spec.get("decisions") or ()),
            blockers=refs("blockers"),
        )
    return {"recorded": item.to_dict(), "next_actions": service.show(args.debate_id)["next_actions"]}


def _intel_payload(container: ResearchOrchestrationContainer, args: argparse.Namespace) -> object:
    intel = container.event_intel
    if args.read_items or args.read_urls:
        split = lambda text: [item.strip() for item in (text or "").split(",") if item.strip()]  # noqa: E731
        return intel.read_pages(
            urls=split(args.read_urls),
            item_ids=split(args.read_items),
            max_chars=args.max_chars if args.max_chars != 20000 else 6000,
            refresh=args.refresh,
        )
    if args.fetch_url:
        return intel.page_text(args.fetch_url, max_chars=args.max_chars)
    if args.item_ids:
        return intel.items([item.strip() for item in args.item_ids.split(",") if item.strip()])
    if args.add_evidence_url:
        if not args.published_on:
            raise ValueError("--add-evidence-url 需要 --published-on（页面的发布日期）")
        return intel.add_evidence(
            url=args.add_evidence_url,
            title=args.title or "",
            published_on=args.published_on,
            publisher=args.publisher,
        )
    return intel.search(
        args.source,
        query=args.query,
        ticker=args.ticker,
        forms=tuple(item.strip().upper() for item in args.forms.split(",") if item.strip()),
        agency=args.agency,
        since=args.since,
        until=args.until,
        language=args.lang,
        limit=args.limit,
        allow_remote=not args.no_remote,
    )


def _observables_payload(container: ResearchOrchestrationContainer, args: argparse.Namespace) -> object:
    intel = container.event_intel
    if args.observable_id:
        observable = intel.observable(args.observable_id)
        if observable is None:
            raise ValueError(f"未登记的可观测量 {args.observable_id}")
        return {
            "observable": observable.to_dict(),
            "observations": [item.to_dict() for item in intel.observations(args.observable_id)],
            "notes": [
                "同一 period_end 有多次记录时取最后一次（更正）；结算只用 available_at 不晚于检查点的观测。",
            ],
        }
    rows = []
    for observable in intel.observables():
        history = intel.observations(observable.observable_id)
        rows.append(
            observable.to_dict()
            | {"latest_observation": history[-1].to_dict() if history else None, "observation_count": len(history)}
        )
    return rows


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
