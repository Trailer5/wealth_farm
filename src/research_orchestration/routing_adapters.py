"""Adapters that let the question router use single-asset orchestration."""

from __future__ import annotations

from typing import Any, Protocol

from src.discovery_funnel import DEFAULT_BASE_PACKS, DiscoveryFunnelService, DiscoveryScope
from src.data_access import AssetInput, DataAccessRequest
from src.financial_data import DATASET_STOCK_INCOME_STATEMENTS, FINANCIAL_DATASETS, FinancialResearchBundle
from src.fundamental_analysis import growth_profile_signals, recurring_profit_summary
from src.master_data import ASSET_TYPE_INDEX, ASSET_TYPE_STOCK, SW_INDEX_EXCHANGE, MasterDataService
from src.portfolio_risk import InvestmentPreference, PortfolioRiskService, preference_changes
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    DecisionIntent,
    DecisionLane,
    Personalization,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchIntent,
    ResearchQuestionType,
    ResearchRequest,
    SubjectRef,
    SubjectType,
    to_payload,
)
from src.research_routing import (
    PREFERENCE_RECORDED,
    PREFERENCE_WITH_MEANING,
    RESOLVED,
    StepOutcome,
    StepOutcomeStatus,
    SubjectResolution,
)
from src.research_workflow import ResearchExecutionOptions, high_growth_unprofitable_reason
from src.sector_data import SectorDataService, SwIndustry
from src.user_space import UserContext, UserSpaceService

from .capabilities import ResearchCapabilityRegistry
from .dispatcher import MissingExecutionOptionsError
from .event_name_discovery import (
    EventNameDiscoveryOrchestrationPipeline,
    EventNameDiscoveryRunRequest,
)
from .models import ResearchRunRequest, ResearchRunStatus
from .pipeline import ResearchOrchestrationPipeline
from .request_planner import (
    ResearchPlanningRequest,
    ResearchPlanningStatus,
    ResearchRequestPlanner,
)
from .state_archive import StateArchiveRecorder


class PlannerSubjectResolver:
    def __init__(self, planner: ResearchRequestPlanner) -> None:
        self.planner = planner

    def resolve(self, ref: SubjectRef, as_of_date: str) -> SubjectResolution:
        try:
            result = self.planner.plan(
                ResearchPlanningRequest(
                    as_of_date=as_of_date,
                    asset_id=ref.resolved_id,
                    symbol=None if ref.resolved_id else ref.text,
                )
            )
        except (KeyError, ValueError) as exc:
            return SubjectResolution(ref.text, "unresolved", issues=(str(exc),))
        # Blocked plans know the identity; execution reports the missing prerequisite.
        if (
            result.status
            in (ResearchPlanningStatus.READY, ResearchPlanningStatus.BLOCKED)
            and result.identity is not None
        ):
            return SubjectResolution(
                ref.text,
                RESOLVED,
                asset_id=result.identity.asset_id,
                profile_id=result.profile_id,
                issues=result.issues,
            )
        return SubjectResolution(
            ref.text,
            result.status.value,
            candidates=tuple(item.asset_id for item in result.candidates),
            issues=result.issues,
            candidate_labels=tuple(
                f"{item.display_name or item.symbol}"
                f"（{item.asset_type}，{item.exchange or item.market or '-'}）"
                for item in result.candidates
            ),
        )


class RegistryLaneSupport:
    def __init__(self, registry: ResearchCapabilityRegistry) -> None:
        self.registry = registry

    def lane_gaps(self, profile_id: str, lane: DecisionLane) -> tuple[str, ...]:
        return self.registry.resolve(
            profile_id=profile_id,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=lane,
        ).lane_unsupported_capabilities


class UserSpaceContextReader:
    """Read-only view of one user's recorded holdings and constraints."""

    def __init__(
        self,
        *,
        user_space: UserSpaceService,
        portfolio_risk: PortfolioRiskService,
    ) -> None:
        self.user_space = user_space
        self.portfolio_risk = portfolio_risk

    def has_positions(self, user_id: str, asset_id: str | None) -> bool:
        return any(
            asset_id is None or item["asset_id"] == asset_id
            for item in self.portfolio_summary(user_id)["holdings"]
        )

    def portfolio_summary(self, user_id: str) -> dict[str, Any]:
        context = self._context(user_id)
        if context is None:
            return {"holdings": [], "latest_valuation": None}
        master_data = self.portfolio_risk.research_state.master_data
        holdings = []
        for item in self.portfolio_risk.research_state.list_holdings(context):
            if item.quantity <= 0:
                continue
            asset = master_data.get_asset(item.asset_id)
            holdings.append(
                {
                    **to_payload(item),
                    "symbol": asset.standard_symbol if asset else None,
                    "name": asset.display_name if asset else None,
                    "asset_type": asset.asset_type if asset else None,
                }
            )
        snapshots = self.portfolio_risk.list_snapshots(context)
        latest = (
            self.portfolio_risk.get_snapshot(
                context,
                snapshots[0].snapshot_id,
            )
            if snapshots
            else None
        )
        return {
            "holdings": holdings,
            "latest_valuation": (
                to_payload(latest) if latest is not None else None
            ),
        }

    def describe(self, user_id: str) -> dict[str, Any]:
        user = self.user_space.get_user(user_id)
        if user is None:
            raise KeyError(f"Unknown user_id: {user_id}")
        context = UserContext(user_id=user_id)
        preferences = self.portfolio_risk.list_preferences(context)
        return {
            "user": to_payload(user),
            **self.portfolio_summary(user_id),
            "preference": (
                {**preferences[-1].to_dict(), "readable": preferences[-1].describe()["lines"]}
                if preferences
                else None
            ),
            "preference_history": [
                {
                    "preference_id": item.preference_id,
                    "version": item.version,
                    "recorded_on": item.recorded_on,
                    "user_statement": item.user_statement,
                    "meanings": [meaning.value for meaning in item.meanings],
                }
                for item in preferences
            ],
            "mandates": [
                item.to_dict()
                for item in self.portfolio_risk.list_mandates(context)
            ],
            "constraint_snapshots": [
                item.to_dict()
                for item in self.portfolio_risk.list_constraint_snapshots(
                    context
                )
            ],
        }

    def preference_status(self, user_id: str, ref: str) -> str | None:
        """Whether the ref names one of this user's recorded preference versions."""

        preference = self._preference(user_id, ref)
        if preference is None:
            return None
        return PREFERENCE_WITH_MEANING if preference.meanings else PREFERENCE_RECORDED

    def preference_summary(self, user_id: str, refs: tuple[str, ...]) -> dict[str, Any] | None:
        """The referenced preference (latest when several), with what changed from its predecessor."""

        context = self._context(user_id)
        if context is None:
            return None
        history = self.portfolio_risk.list_preferences(context)
        by_id = {item.preference_id: item for item in history}
        chosen = [by_id[ref] for ref in refs if ref in by_id]
        if not chosen:
            return None
        preference = max(chosen, key=lambda item: item.version)
        previous = by_id.get(preference.supersedes_preference_id or "")
        return {
            "preference": preference.to_dict(),
            "readable": preference.describe()["lines"],
            "changes": preference_changes(previous, preference),
            "is_latest": preference.preference_id == history[-1].preference_id,
        }

    def _preference(self, user_id: str, ref: str) -> InvestmentPreference | None:
        context = self._context(user_id)
        if context is None:
            return None
        return next(
            (item for item in self.portfolio_risk.list_preferences(context) if item.preference_id == ref),
            None,
        )

    def _context(self, user_id: str) -> UserContext | None:
        if self.user_space.get_user(user_id) is None:
            return None
        return UserContext(user_id=user_id)


class DecisionBriefStoreProtocol(Protocol):
    def save_decision_brief(
        self,
        context: UserContext,
        *,
        asset_id: str,
        decision_intent: DecisionIntent,
        decision_lanes: tuple[DecisionLane, ...],
        horizon: ResearchHorizon,
        as_of_date: str,
        position_state: PositionState | None = None,
        assumptions: tuple[str, ...] = (),
    ) -> ResearchDecisionBrief: ...


class SingleAssetRecipeRunner:
    """Plan, then run the baseline pipeline exactly as plan-single/run-single do."""

    def __init__(
        self,
        *,
        planner: ResearchRequestPlanner,
        pipeline: ResearchOrchestrationPipeline,
        decision_briefs: DecisionBriefStoreProtocol | None = None,
    ) -> None:
        self.planner = planner
        self.pipeline = pipeline
        self.decision_briefs = decision_briefs

    def run_baseline(
        self,
        *,
        request: ResearchRequest,
        subject: SubjectResolution,
        holding: bool,
        asset_type: str | None = None,
    ) -> tuple[StepOutcome, ...]:
        if ResearchIntent.UNDERSTAND in request.intents:
            intent, lanes = DecisionIntent.UNDERSTANDING, ()
        else:
            intent = (
                DecisionIntent.REVIEW_HOLDING
                if holding
                else DecisionIntent.EVALUATE_PURCHASE
            )
            lanes = tuple(DecisionLane)
        planning = self.planner.plan(
            ResearchPlanningRequest(
                as_of_date=request.as_of_date,
                asset_id=subject.asset_id,
                symbol=None if subject.asset_id else subject.text,
                asset_type=None if subject.asset_id else asset_type,
                decision_intent=intent,
                decision_lanes=lanes,
                horizon=request.horizon or ResearchHorizon.MEDIUM_LONG,
            )
        )
        if (
            planning.status is not ResearchPlanningStatus.READY
            or planning.task is None
        ):
            return (
                StepOutcome(
                    "identity_and_plan",
                    StepOutcomeStatus.BLOCKED,
                    f"规划未就绪（{planning.status.value}）："
                    + "；".join(planning.issues or planning.routing_notes),
                ),
            )
        planned = StepOutcome(
            "identity_and_plan",
            StepOutcomeStatus.COMPLETED,
            f"{subject.text} 解析为 {planning.task.asset_id}，"
            f"profile {planning.profile_id}",
            artifacts={"task": planning.task.to_dict()},
        )
        if planning.profile_id == PROFILE_CYCLICAL_RESOURCE:
            return (
                planned,
                StepOutcome(
                    "baseline",
                    StepOutcomeStatus.NEEDS_INPUT,
                    "周期资源公司需要商品权重，请用 run-single --commodity-weight 运行基础研究。",
                ),
            )
        try:
            brief = self._save_brief(
                request=request,
                intent=intent,
                holding=holding,
                lanes=planning.effective_decision_lanes,
                task=planning.task,
            )
        except KeyError as exc:
            return (
                planned,
                StepOutcome(
                    "baseline",
                    StepOutcomeStatus.BLOCKED,
                    f"决策简报无法保存：{exc}",
                ),
            )
        try:
            result = self.pipeline.run(
                ResearchRunRequest(
                    task=planning.task,
                    execution_options=ResearchExecutionOptions(),
                    decision_brief_id=(
                        brief.brief_id if brief is not None else None
                    ),
                    decision_lanes=planning.effective_decision_lanes,
                )
            )
        except MissingExecutionOptionsError as exc:
            return (
                planned,
                StepOutcome(
                    "baseline",
                    StepOutcomeStatus.NEEDS_INPUT,
                    str(exc),
                ),
            )
        except (KeyError, ValueError) as exc:
            return (
                planned,
                StepOutcome(
                    "baseline",
                    StepOutcomeStatus.BLOCKED,
                    f"基础研究未能启动：{exc}",
                ),
            )
        if result.status is ResearchRunStatus.BLOCKED:
            return (
                planned,
                StepOutcome(
                    "baseline",
                    StepOutcomeStatus.BLOCKED,
                    "基础研究被阻断，没有生成 handoff。",
                    artifacts=_run_artifacts(result),
                ),
            )
        artifacts = _run_artifacts(result)
        if brief is not None:
            artifacts["decision_brief_id"] = brief.brief_id
        return (
            planned,
            StepOutcome(
                "baseline",
                StepOutcomeStatus.COMPLETED,
                f"基础研究完成（{result.status.value}）。",
                artifacts=artifacts,
                next_actions=(
                    "用 container.exploration 启动探索会话，先查反方证据",
                    "探索 ready 后装配适配、时点、仓位并发布最终报告（发布即入账）",
                ),
            ),
        )

    def _save_brief(
        self,
        *,
        request: ResearchRequest,
        intent: DecisionIntent,
        holding: bool,
        lanes: tuple[DecisionLane, ...],
        task,
    ) -> ResearchDecisionBrief | None:
        """Record the user's decision framing; missing context stays missing."""

        if self.decision_briefs is None or request.user_id is None:
            return None
        if intent is DecisionIntent.REVIEW_HOLDING or holding:
            position_state = PositionState.HELD
        else:
            position_state = None
        assumptions = (
            *(
                ()
                if request.horizon is not None
                else ("用户未给出期限，按中长期默认期限研究。",)
            ),
            *(
                ()
                if position_state is not None
                else ("持仓状态按用户空间记录推定，未记录即视为未持有。",)
            ),
        )
        return self.decision_briefs.save_decision_brief(
            UserContext(user_id=request.user_id),
            asset_id=task.asset_id,
            decision_intent=intent,
            decision_lanes=lanes,
            horizon=task.horizon,
            as_of_date=task.as_of_date,
            position_state=position_state,
            assumptions=assumptions,
        )


class EventDiscoveryRecipeRunner:
    def __init__(
        self,
        pipeline: EventNameDiscoveryOrchestrationPipeline,
    ) -> None:
        self.pipeline = pipeline

    def run(
        self,
        discovery_request: EventNameDiscoveryRunRequest,
    ) -> StepOutcome:
        result = self.pipeline.run(discovery_request)
        return StepOutcome(
            "event_name_discovery",
            StepOutcomeStatus.COMPLETED,
            f"事件名称发现完成，生成 {len(result.exploration_sessions)} 个探索交接。",
            artifacts={"discovery_run_id": result.discovery_run.run_id},
        )


class SectorBriefRunner:
    """Resolve a sector reference to a Shenwan industry and build its archive."""

    def __init__(self, *, sector_data: SectorDataService, recorder: StateArchiveRecorder) -> None:
        self.sector_data = sector_data
        self.recorder = recorder

    def resolve(self, ref: SubjectRef) -> SwIndustry | None:
        if ref.resolved_id and ref.resolved_id.startswith("sector:sw:"):
            return self.sector_data.industry(ref.resolved_id.removeprefix("sector:sw:"))
        wanted = _industry_name(ref.text)
        matches = [
            item for item in self.sector_data.industries() if _industry_name(item.name) == wanted
        ]
        return min(matches, key=lambda item: item.level) if matches else None

    def definition(self, industry: SwIndustry) -> dict[str, Any]:
        return {
            "industry": industry.to_dict(),
            "children": [item.to_dict() for item in self.sector_data.industries(parent_code=industry.code)],
            "member_count": len(self.sector_data.members(industry.code)),
            "index_asset_id": self.sector_data.index_asset(industry.code).asset_id,
        }

    def run(self, as_of_date: str, *, code: str) -> dict[str, Any]:
        return self.recorder.run(as_of_date, code=code)

    def resolve_many(self, ref: SubjectRef) -> list[SwIndustry]:
        """`sector:sw:<code>[,<code>...]` maps a theme to several industries; otherwise one name match."""

        if ref.resolved_id and ref.resolved_id.startswith("sector:sw:"):
            codes = [c.strip() for c in ref.resolved_id.removeprefix("sector:sw:").split(",") if c.strip()]
            found = [self.sector_data.industry(code) for code in codes]
            return [item for item in found if item is not None] if all(found) else []
        industry = self.resolve(ref)
        return [] if industry is None else [industry]

    def view_template(
        self,
        industry: SwIndustry,
        *,
        request_id: str,
        user_id: str | None,
        as_of_date: str,
        horizon: str,
    ) -> dict[str, Any]:
        """A settleable sector view skeleton: the Shenwan index against CSI 300."""

        broad = [
            asset
            for asset in self.sector_data.master_data.find_assets(
                standard_symbol=_CSI300, asset_type=ASSET_TYPE_INDEX
            )
            if asset.exchange != SW_INDEX_EXCHANGE
        ]
        return {
            "view_id": f"view:user:{request_id}:{industry.code}",
            "subject_type": "sector",
            "subject_id": f"sector:sw:{industry.code}",
            "origin": "user",
            "user_id": user_id,
            "proposition": f"<改写后的命题，例如：截至 expires_on，申万{industry.name}指数跑赢沪深300>",
            "horizon": horizon,
            "as_of_date": as_of_date,
            "expires_on": "<检查点日期>",
            "confidence": "medium",
            "claim": {"metric_key": "total_return", "comparison": "outperform"},
            "settlement_rule": {
                "method": "price_relative",
                "observed_id": self.index_asset_id(industry),
                "benchmark_id": broad[0].asset_id if broad else "<沪深300 asset_id：cli asset --symbol 000300 --asset-type index>",
            },
        }

    def index_asset_id(self, industry: SwIndustry) -> str:
        return self.sector_data.index_asset(industry.code).asset_id


def _industry_name(text: str) -> str:
    return text.strip().rstrip("ⅠⅡⅢ").removeprefix("申万").strip()


_CSI300 = "000300"


class RecurringProfitLookup:
    """Per-stock 扣非 profit for discovery candidates, read through unified financial data."""

    def __init__(self, *, master_data: MasterDataService, data_access: Any, data_fetch: Any) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.data_fetch = data_fetch

    def __call__(self, symbol: str, as_of_date: str) -> dict[str, Any] | None:
        stocks = self.master_data.find_assets(standard_symbol=symbol, asset_type=ASSET_TYPE_STOCK)
        if not stocks:
            self.data_fetch.fetch_and_store_identities(symbol, asset_type=ASSET_TYPE_STOCK)
            stocks = self.master_data.find_assets(standard_symbol=symbol, asset_type=ASSET_TYPE_STOCK)
        if len(stocks) != 1:
            return None
        return recurring_profit_summary(_income_bundle(self.data_access, stocks[0], as_of_date))


class GrowthProfileDetector:
    """Planner hook: does a stock planned as an ordinary company meet the high-growth unprofitable rule?"""

    def __init__(self, *, data_access: Any) -> None:
        self.data_access = data_access

    def __call__(self, asset: Any, as_of_date: str) -> str | None:
        signals = growth_profile_signals(_income_bundle(self.data_access, asset, as_of_date))
        if signals is None:
            return None
        return high_growth_unprofitable_reason(
            earnings_ttm=signals["earnings_ttm"],
            revenue_growth=signals["revenue_growth"],
            display_name=asset.display_name,
            symbol=asset.standard_symbol,
        )


def _income_bundle(data_access: Any, asset: Any, as_of_date: str) -> FinancialResearchBundle:
    """Income statements only (about 6 s a stock); the other statements are not needed for profit and revenue."""

    start = f"{int(as_of_date[:4]) - 3}{as_of_date[4:]}"
    response = data_access.get(
        DataAccessRequest(
            asset=AssetInput(asset_id=asset.asset_id),
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            start_date=start,
            end_date=as_of_date,
            as_of_date=as_of_date,
            max_age_seconds=86400,
            allow_stale_on_error=True,
            remote_sources=("akshare",),
        )
    )
    return FinancialResearchBundle(
        asset=asset,
        as_of_date=as_of_date,
        start_date=start,
        end_date=as_of_date,
        records_by_dataset={
            dataset: response.records if dataset == DATASET_STOCK_INCOME_STATEMENTS else ()
            for dataset in FINANCIAL_DATASETS
        },
        sources=tuple(response.sources),
        complete=response.complete,
    )


class DiscoveryFunnelRunner:
    """Run the discovery funnel, then baseline packs for the top candidates."""

    def __init__(
        self,
        *,
        funnel: DiscoveryFunnelService,
        single_asset: SingleAssetRecipeRunner,
        master_data: MasterDataService | None = None,
        sector: SectorBriefRunner | None = None,
    ) -> None:
        self.funnel = funnel
        self.single_asset = single_asset
        self.master_data = master_data
        self.sector = sector

    def resolve_scope(self, ref: SubjectRef) -> DiscoveryScope | None:
        resolved = ref.resolved_id or ""
        if resolved.startswith("sector:sw:") and "," in resolved and self.sector is not None:
            industries = self.sector.resolve_many(ref)
            if not industries:
                return None
            symbols = sorted(
                {m.symbol for item in industries for m in self.sector.sector_data.members(item.code)}
            )
            names = "、".join(item.name for item in industries)
            return DiscoveryScope(
                "symbols",
                symbols=tuple(symbols),
                label=f"{ref.text}（申万{names}）",
                origin=f"范围为申万{names}的当前成分并集",
            )
        if resolved.startswith("sector:sw:"):
            return DiscoveryScope("industry", code=resolved.removeprefix("sector:sw:"), label=None)
        if resolved.startswith(("index:", "symbols:")):
            return DiscoveryScope.parse(resolved, label=ref.text if resolved.startswith("symbols:") else None)
        if resolved == "market:cn_a":
            return DiscoveryScope("market")
        industry = self.sector.resolve(ref) if self.sector is not None else None
        return None if industry is None else DiscoveryScope("industry", code=industry.code)

    def run(
        self,
        scope: DiscoveryScope,
        *,
        as_of_date: str,
        base_packs: int = DEFAULT_BASE_PACKS,
        **options: Any,
    ) -> dict[str, Any]:
        payload = self.funnel.run(scope, as_of_date=as_of_date, base_pack_count=base_packs, **options)
        payload["base_packs"] = [
            self._base_pack(symbol, payload["run_id"], as_of_date)
            for symbol in payload["selected_for_base_pack"]
        ]
        self.funnel.save(payload)
        return payload

    def _base_pack(self, symbol: str, run_id: str, as_of_date: str) -> dict[str, Any]:
        request = ResearchRequest(
            request_id=f"{run_id}:{symbol}",
            subject_type=SubjectType.ASSET,
            subject_refs=(SubjectRef(symbol),),
            intents=(ResearchIntent.UNDERSTAND,),
            personalization=Personalization.NONE,
            as_of_date=as_of_date,
        )
        # Six-digit stock codes also exist as open-fund codes; pass the stock identity when known.
        stocks = (
            self.master_data.find_assets(standard_symbol=symbol, asset_type=ASSET_TYPE_STOCK)
            if self.master_data is not None
            else []
        )
        try:
            outcomes = self.single_asset.run_baseline(
                request=request,
                subject=SubjectResolution(
                    text=symbol,
                    status=RESOLVED,
                    asset_id=stocks[0].asset_id if len(stocks) == 1 else None,
                ),
                holding=False,
                asset_type=ASSET_TYPE_STOCK,
            )
        except Exception as exc:  # noqa: BLE001 - one failed pack must not stop the others
            return {"symbol": symbol, "status": "blocked", "summary": f"{type(exc).__name__}: {exc}"}
        last = outcomes[-1]
        return {
            "symbol": symbol,
            "status": last.status.value,
            "summary": last.summary,
            **{
                key: last.artifacts[key]
                for key in ("report_path", "workflow_run_id", "handoff_id")
                if key in last.artifacts
            },
        }


def _run_artifacts(result) -> dict[str, str]:
    artifacts = {}
    if result.workflow_run is not None:
        artifacts["workflow_run_id"] = result.workflow_run.workflow_run_id
    if result.handoff is not None:
        artifacts["handoff_id"] = result.handoff.handoff_id
    if result.report_path is not None:
        artifacts["report_path"] = str(result.report_path)
    return artifacts
