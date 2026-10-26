"""Execute the currently available steps of a routed recipe."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from src.research_contracts import (
    Confidence,
    ResearchRequest,
    SubjectType,
    View,
    ViewOrigin,
    last_default_checkpoint,
    settlement_checkpoints,
    to_payload,
)
from src.research_ledger import LedgerEntryType

from .models import (
    RoutingResult,
    RoutingStatus,
    StepStatus,
    SubjectResolution,
)
from .router import QuestionRouter

_SINGLE_ASSET_RECIPES = {
    "asset_decision": False,
    "asset_understanding": False,
    "holding_review": True,
}
_VIEW_RECIPES = frozenset({"view_test", "sector_view_recommendation"})
# Steps whose available part is reading the user's holdings and valuation.
_PORTFOLIO_READ_STEPS = frozenset(
    {"portfolio_overlay", "portfolio_impact", "portfolio_valuation"}
)
_NOT_EXECUTABLE = frozenset(
    {
        RoutingStatus.UNRESOLVED,
        RoutingStatus.UNRESOLVED_SUBJECT,
        RoutingStatus.NEEDS_USER_CONTEXT,
    }
)


class StepOutcomeStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"
    GAP = "gap"


class RecipeRunStatus(str, Enum):
    EXECUTED = "executed"
    BLOCKED = "blocked"
    NOT_EXECUTED = "not_executed"


@dataclass(frozen=True)
class StepOutcome:
    step_id: str
    status: StepOutcomeStatus
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    next_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipeRunResult:
    routing: RoutingResult
    status: RecipeRunStatus
    step_outcomes: tuple[StepOutcome, ...] = ()
    ledger_entry_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing": self.routing.to_dict(),
            "status": self.status.value,
            "step_outcomes": [to_payload(item) for item in self.step_outcomes],
            "ledger_entry_ids": list(self.ledger_entry_ids),
        }


class SingleAssetRunnerProtocol(Protocol):
    def run_baseline(
        self,
        *,
        request: ResearchRequest,
        subject: SubjectResolution,
        holding: bool,
    ) -> tuple[StepOutcome, ...]: ...


class DiscoveryRunnerProtocol(Protocol):
    def run(self, discovery_request: Any) -> StepOutcome: ...


class LedgerProtocol(Protocol):
    def record_user_view(self, view: View, *, source_ref: str) -> Any: ...

    def list_entries(self, **filters: Any) -> tuple[Any, ...]: ...


class PortfolioReaderProtocol(Protocol):
    def portfolio_summary(self, user_id: str) -> dict[str, Any]: ...


class MarketStateRunnerProtocol(Protocol):
    def run(self, as_of_date: str) -> dict[str, Any]: ...


class AnalogRunnerProtocol(Protocol):
    def market_defaults(self, as_of_date: str) -> dict[str, Any]: ...


class RecipeExecutor:
    def __init__(
        self,
        *,
        router: QuestionRouter,
        single_asset: SingleAssetRunnerProtocol,
        ledger: LedgerProtocol,
        discovery: DiscoveryRunnerProtocol | None = None,
        portfolio: PortfolioReaderProtocol | None = None,
        market: MarketStateRunnerProtocol | None = None,
        macro: MarketStateRunnerProtocol | None = None,
        analogs: AnalogRunnerProtocol | None = None,
    ) -> None:
        self.router = router
        self.single_asset = single_asset
        self.ledger = ledger
        self.discovery = discovery
        self.portfolio = portfolio
        self.market = market
        self.macro = macro
        self.analogs = analogs

    def run(
        self,
        request: ResearchRequest,
        *,
        discovery_request: Any | None = None,
    ) -> RecipeRunResult:
        """Route, then run executable steps; blocked routing has no side effects."""

        routing = self.router.route(request)
        if routing.status in _NOT_EXECUTABLE:
            return RecipeRunResult(routing, RecipeRunStatus.NOT_EXECUTED)
        recipe_id = routing.recipe_ids[0]
        if recipe_id in _SINGLE_ASSET_RECIPES:
            outcomes = tuple(
                outcome
                for subject in routing.subjects
                for outcome in self.single_asset.run_baseline(
                    request=request,
                    subject=subject,
                    holding=_SINGLE_ASSET_RECIPES[recipe_id],
                )
            )
        elif recipe_id == "view_test":
            outcomes = self._test_view(request)
        elif recipe_id in _VIEW_RECIPES:
            outcomes = (self._record_user_view(request),)
        elif recipe_id == "plan_schedule":
            outcomes = (self._summarize_plan(request),)
        elif recipe_id == "sector_discovery":
            outcomes = (self._discover(discovery_request),)
        elif recipe_id == "market_brief":
            outcomes = (
                self._state_archive("market_state", "市场", self.market, request),
                self._state_archive("macro_state", "宏观", self.macro, request),
                self._historical_position(request),
            )
        else:
            outcomes = ()
        outcomes = (*outcomes, *self._read_portfolio(routing))
        statuses = {item.status for item in outcomes}
        if StepOutcomeStatus.BLOCKED in statuses:
            status = RecipeRunStatus.BLOCKED
        elif StepOutcomeStatus.COMPLETED in statuses:
            status = RecipeRunStatus.EXECUTED
        else:
            status = RecipeRunStatus.NOT_EXECUTED
        return RecipeRunResult(
            routing=routing,
            status=status,
            step_outcomes=outcomes,
            ledger_entry_ids=tuple(
                item.artifacts["ledger_entry_id"]
                for item in outcomes
                if "ledger_entry_id" in item.artifacts
            ),
        )

    def _test_view(self, request: ResearchRequest) -> tuple[StepOutcome, ...]:
        recorded = self._record_user_view(request)
        if recorded.status is not StepOutcomeStatus.COMPLETED:
            return (recorded,)
        runner, subject = (
            (self.market, "市场")
            if request.subject_type is SubjectType.MARKET
            else (self.macro, "宏观")
        )
        return (
            recorded,
            self._state_archive("leading_indicators", subject, runner, request),
            self._historical_position(request, step_id="base_rate"),
        )

    def _record_user_view(self, request: ResearchRequest) -> StepOutcome:
        if request.horizon is None:
            return StepOutcome(
                "record_user_view",
                StepOutcomeStatus.NEEDS_INPUT,
                "观点需要时间窗口才能成为可证伪命题；请补充 horizon 后重试。",
            )
        subject = (
            request.subject_refs[0].resolved_id or request.subject_refs[0].text
            if request.subject_refs
            else f"{request.subject_type.value}:general"
        )
        entry = self.ledger.record_user_view(
            View(
                view_id=f"view:user:{request.request_id}",
                subject_type=request.subject_type,
                subject_id=subject,
                origin=ViewOrigin.USER,
                user_id=request.user_id,
                proposition=request.user_view,
                horizon=request.horizon,
                as_of_date=request.as_of_date,
                expires_on=last_default_checkpoint(
                    request.horizon,
                    request.as_of_date,
                ),
                confidence=Confidence.MEDIUM,
            ),
            source_ref=f"request:{request.request_id}",
        )
        return StepOutcome(
            "record_user_view",
            StepOutcomeStatus.COMPLETED,
            "用户观点已作为待检验观点入账，仅本人可见；正反证据按路由结果中的缺口收集。",
            artifacts={"ledger_entry_id": entry.entry_id},
            next_actions=(
                "把观点改写为可证伪命题（程度与时间窗口），用 cli record-view "
                "--supersedes-entry-id <本条> 入账可结算版本：指标类 observed_id 写 "
                "macro:<series_id>，价格类写标的 ID",
                "先收集反方证据，再收集支持证据，并标注来源与发布日期",
            ),
        )

    def _summarize_plan(self, request: ResearchRequest) -> StepOutcome:
        actions = self.ledger.list_entries(
            user_id=request.user_id,
            entry_type=LedgerEntryType.ACTION,
            active_on=request.as_of_date,
        )
        views = self.ledger.list_entries(
            user_id=request.user_id,
            entry_type=LedgerEntryType.VIEW,
            active_on=request.as_of_date,
        )
        review_dates = sorted(
            {
                checkpoint
                for entry in views
                for checkpoint in _view_checkpoints(entry.artifact)
                if checkpoint >= request.as_of_date
            }
        )
        return StepOutcome(
            "ledger_actions",
            StepOutcomeStatus.COMPLETED,
            "这只是账本中有效动作与复查节点的汇总，不是完整计划："
            "目标区间、组合层冲突处理和 HTML 时间表尚未建立。",
            artifacts={
                "actions": [to_payload(item.artifact) for item in actions],
                "review_dates": review_dates,
            },
        )

    def _read_portfolio(
        self,
        routing: RoutingResult,
    ) -> tuple[StepOutcome, ...]:
        user_id = routing.request.user_id
        step_ids = [
            item.step.step_id
            for item in routing.steps
            if item.step.step_id in _PORTFOLIO_READ_STEPS
            and item.status is not StepStatus.PLANNED
        ]
        if not step_ids or user_id is None or self.portfolio is None:
            return ()
        summary = self.portfolio.portfolio_summary(user_id)
        if not summary["holdings"]:
            return tuple(
                StepOutcome(
                    step_id,
                    StepOutcomeStatus.GAP,
                    "用户空间没有持仓记录，只能给通用应对框架；"
                    "用户提供持仓后用 record-holding 记录。",
                )
                for step_id in step_ids
            )
        return tuple(
            StepOutcome(
                step_id,
                StepOutcomeStatus.COMPLETED,
                "已读取持仓与最近一次组合估值；情景到组合暴露的映射尚未建立，"
                "影响分析由 Agent 完成并标注。",
                artifacts=summary,
            )
            for step_id in step_ids
        )

    def _state_archive(
        self,
        step_id: str,
        subject: str,
        runner: MarketStateRunnerProtocol | None,
        request: ResearchRequest,
    ) -> StepOutcome:
        if runner is None:
            return StepOutcome(
                step_id,
                StepOutcomeStatus.GAP,
                f"{subject}档案未接入当前执行器。",
            )
        try:
            archive = runner.run(request.as_of_date)
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return StepOutcome(
                step_id,
                StepOutcomeStatus.BLOCKED,
                f"{subject}档案数据不可用：{type(exc).__name__}: {exc}",
            )
        artifacts = {"archive": archive}
        if archive.get("ledger_entry_id"):
            artifacts["ledger_entry_id"] = archive["ledger_entry_id"]
        return StepOutcome(
            step_id,
            StepOutcomeStatus.COMPLETED,
            f"已生成{subject}档案并作为状态评估入账："
            f"{archive['assessment']['label']}。对资产类别和时间尺度的含义"
            "尚未建立，由 Agent 基于档案与同类历史分布撰写并标注。",
            artifacts=artifacts,
            next_actions=(
                "逐个维度引用档案的数值、分位窗口和样本数，不只引用标签",
                "写出会改变判断的信号，并说明 gaps 中缺失的维度",
            ),
        )

    def _historical_position(
        self,
        request: ResearchRequest,
        *,
        step_id: str = "historical_position",
    ) -> StepOutcome:
        if self.analogs is None:
            return StepOutcome(
                step_id,
                StepOutcomeStatus.GAP,
                "同类历史分布未接入当前执行器。",
            )
        try:
            analogs = self.analogs.market_defaults(request.as_of_date)
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a gap
            return StepOutcome(
                step_id,
                StepOutcomeStatus.GAP,
                f"同类历史分布不可用：{type(exc).__name__}: {exc}",
            )
        return StepOutcome(
            step_id,
            StepOutcomeStatus.COMPLETED,
            "已计算估值、股债利差和 60 日趋势处于当前分位区间时，沪深300 之后 60 与 250 个交易日的"
            "收益分布及无条件基准。只引用不重叠样本的统计；status 为 insufficient_samples 的周期不得下结论。",
            artifacts={"analogs": analogs},
            next_actions=(
                "比较条件分布与基准分布的中位数和上涨比例，而不是只看条件均值",
                "需要其他指标或区间时用 cli analog 查询",
            ),
        )

    def _discover(self, discovery_request: Any | None) -> StepOutcome:
        if discovery_request is None or self.discovery is None:
            return StepOutcome(
                "event_name_discovery",
                StepOutcomeStatus.GAP,
                "事件名称发现需要事件、关系证据和反应数据输入；"
                "质量—估值分级漏斗尚无正式入口。",
            )
        return self.discovery.run(discovery_request)


def _view_checkpoints(view: View) -> tuple[str, ...]:
    if view.settlement_rule is None:
        return (view.expires_on,)
    return settlement_checkpoints(view.horizon, view.as_of_date, view.expires_on)
