"""Adapters that let the question router use single-asset orchestration."""

from __future__ import annotations

from typing import Any, Protocol

from src.portfolio_risk import PortfolioRiskService
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchIntent,
    ResearchQuestionType,
    ResearchRequest,
    SubjectRef,
    to_payload,
)
from src.research_routing import (
    RESOLVED,
    StepOutcome,
    StepOutcomeStatus,
    SubjectResolution,
)
from src.research_workflow import ResearchExecutionOptions
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
        holdings = [
            to_payload(item)
            for item in self.portfolio_risk.research_state.list_holdings(
                context
            )
            if item.quantity > 0
        ]
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
        return {
            "user": to_payload(user),
            **self.portfolio_summary(user_id),
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


def _run_artifacts(result) -> dict[str, str]:
    artifacts = {}
    if result.workflow_run is not None:
        artifacts["workflow_run_id"] = result.workflow_run.workflow_run_id
    if result.handoff is not None:
        artifacts["handoff_id"] = result.handoff.handoff_id
    if result.report_path is not None:
        artifacts["report_path"] = str(result.report_path)
    return artifacts
