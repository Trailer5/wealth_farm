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
_Q9_STEP_IDS = {"strategy_funnel": "sector_discovery", "multi_strategy_priority": "candidate_decisions"}
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


_REGIME_NAMES = {"trend": "趋势", "volatility": "波动", "valuation": "估值"}


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


class SectorRunnerProtocol(Protocol):
    def resolve(self, ref: Any) -> Any: ...

    def definition(self, industry: Any) -> dict[str, Any]: ...

    def run(self, as_of_date: str, *, code: str) -> dict[str, Any]: ...

    def resolve_many(self, ref: Any) -> list[Any]: ...

    def view_template(
        self,
        industry: Any,
        *,
        request_id: str,
        user_id: str | None,
        as_of_date: str,
        horizon: str,
    ) -> dict[str, Any]: ...


class FunnelRunnerProtocol(Protocol):
    def resolve_scope(self, ref: Any) -> Any: ...

    def run(self, scope: Any, *, as_of_date: str) -> dict[str, Any]: ...


class ThesisCheckerProtocol(Protocol):
    def check(self, asset_id: str, *, as_of_date: str, user_id: str | None = None) -> dict[str, Any]: ...


class ExitPlanRunnerProtocol(Protocol):
    def run(self, *, user_id: str, asset_id: str, as_of_date: str) -> dict[str, Any]: ...


class PortfolioDiagnosisProtocol(Protocol):
    def run(self, *, user_id: str, as_of_date: str) -> dict[str, Any]: ...


class StrategyRunnerProtocol(Protocol):
    def find(self, text: str, as_of_date: str) -> str | None: ...

    def templates(self) -> dict[str, Any]: ...

    def show(self, strategy_id: str, as_of_date: str) -> dict[str, Any]: ...

    def evaluate(self, strategy_id: str, as_of_date: str) -> dict[str, Any]: ...

    def track(self, strategy_id: str, as_of_date: str) -> dict[str, Any]: ...


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
        sector: SectorRunnerProtocol | None = None,
        funnel: FunnelRunnerProtocol | None = None,
        thesis: ThesisCheckerProtocol | None = None,
        exit_plan: ExitPlanRunnerProtocol | None = None,
        portfolio_diagnosis: PortfolioDiagnosisProtocol | None = None,
        allocation: PortfolioDiagnosisProtocol | None = None,
        adjustment: PortfolioDiagnosisProtocol | None = None,
        plan: PortfolioDiagnosisProtocol | None = None,
        decision_review: PortfolioDiagnosisProtocol | None = None,
        strategy: StrategyRunnerProtocol | None = None,
    ) -> None:
        self.router = router
        self.single_asset = single_asset
        self.ledger = ledger
        self.discovery = discovery
        self.portfolio = portfolio
        self.market = market
        self.macro = macro
        self.analogs = analogs
        self.sector = sector
        self.funnel = funnel
        self.thesis = thesis
        self.exit_plan = exit_plan
        self.portfolio_diagnosis = portfolio_diagnosis
        self.allocation = allocation
        self.adjustment = adjustment
        self.plan = plan
        self.decision_review = decision_review
        self.strategy = strategy

    def run(
        self,
        request: ResearchRequest,
        *,
        discovery_request: Any | None = None,
        cash: float | None = None,
    ) -> RecipeRunResult:
        """Route, then run executable steps; blocked routing has no side effects.

        ``cash`` is uninvested cash the user stated, passed to the portfolio steps.
        """

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
            if _SINGLE_ASSET_RECIPES[recipe_id]:
                outcomes = (
                    *outcomes,
                    *self._thesis_check(request, routing.subjects),
                    *self._exit_plan(request, routing.subjects),
                )
        elif recipe_id == "view_test":
            outcomes = self._test_view(request)
        elif recipe_id == "sector_view_recommendation":
            outcomes = self._sector_view(request)
        elif recipe_id in _VIEW_RECIPES:
            outcomes = (self._record_user_view(request),)
        elif recipe_id == "plan_schedule":
            outcomes = (self._summarize_plan(request), *self._plan(request, cash))
        elif recipe_id == "portfolio_rebalance":
            outcomes = (
                self._confirm_preference(request),
                self._diagnose(request, cash),
                *(self._adjust(request, cash) if self.adjustment is not None else (self._allocate(request, cash),)),
            )
        elif recipe_id == "portfolio_review":
            outcomes = (self._diagnose(request, cash),)
        elif recipe_id == "decision_review":
            outcomes = self._decision_review(request, cash)
        elif recipe_id == "sector_discovery":
            outcomes = (
                (self._discover(discovery_request),)
                if discovery_request is not None or self.funnel is None or not request.subject_refs
                else self._funnel(request)
            )
        elif recipe_id == "sector_brief":
            outcomes = self._sector_brief(request)
        elif recipe_id == "strategy_evaluation":
            outcomes = self._strategy(request)
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
                entry_id
                for item in outcomes
                for entry_id in (
                    item.artifacts.get("ledger_entry_id"),
                    *(a.get("ledger_entry_id") for a in item.artifacts.get("archives", ())),
                    *(a["entry_id"] for a in item.artifacts.get("ledger_entries", ())),
                )
                if entry_id
            ),
        )

    def _strategy(self, request: ResearchRequest) -> tuple[StepOutcome, ...]:
        """Archive, complete validation (samples, costs, neighbourhood, regimes, deflated significance) and the
        forward record of each named strategy. Unregistered descriptions ask for registration via a template."""

        if self.strategy is None:
            return (StepOutcome("strategy_spec", StepOutcomeStatus.GAP, "策略入口未接入当前执行器。"),)
        if not request.subject_refs:
            return (
                StepOutcome(
                    "strategy_spec",
                    StepOutcomeStatus.NEEDS_INPUT,
                    "请说明要评估的策略：已登记策略的 id，或具体规则（标的、买卖条件、参数）。",
                    next_actions=("strategy-archive", "strategy-archive --templates"),
                ),
            )
        outcomes: list[StepOutcome] = []
        for ref in request.subject_refs:
            strategy_id = self.strategy.find(ref.resolved_id or ref.text, request.as_of_date)
            if strategy_id is None:
                templates = self.strategy.templates()
                outcomes.append(
                    StepOutcome(
                        "strategy_spec",
                        StepOutcomeStatus.NEEDS_INPUT,
                        f"没有已登记的策略“{ref.text}”。常见策略先对照模板登记为“模板 + 参数”，再用策略 id 重新运行；"
                        "对不上模板或落在 not_expressible 的规则按缺口回答。",
                        artifacts={
                            "templates": [item["template_id"] for item in templates["templates"]],
                            "not_expressible": templates["not_expressible"],
                        },
                        next_actions=(
                            "strategy-archive --templates",
                            "strategy-archive --from-template <模板> --strategy-id <id> --asset <代码:类型> --set <参数=值>",
                        ),
                    )
                )
                continue
            outcomes.extend(self._strategy_steps(strategy_id, request.as_of_date))
        return tuple(outcomes)

    def _strategy_steps(self, strategy_id: str, as_of_date: str) -> tuple[StepOutcome, ...]:
        shown = self.strategy.show(strategy_id, as_of_date)
        archive = shown["archive"]
        spec = StepOutcome(
            "strategy_spec",
            StepOutcomeStatus.COMPLETED,
            f"{archive['name']}（{archive['version']}）：{shown['state_label']}",
            artifacts={
                "strategy_id": strategy_id,
                "state": shown["state"],
                "mechanism": archive["mechanism"],
                "universe": archive["universe"],
                "parameters": archive["parameters"],
                "source_refs": archive["source_refs"],
                "failure_conditions": archive["failure_conditions"],
                "limitations": archive["limitations"],
                "family_backtests": shown.get("family_backtests"),
            },
        )
        samples = (
            StepOutcome(
                "selection_samples",
                StepOutcomeStatus.GAP,
                "选股池按当时已上市未退市的股票与已公告年报构造，历史指数成分尚未接入（阶段 10）；"
                "行业范围仍有幸存者偏差，引用时说明",
            )
            if archive.get("kind") == "selection"
            else StepOutcome(
                "selection_samples",
                StepOutcomeStatus.COMPLETED,
                "不适用：这个策略不从股票池选股，不需要历史成分样本；路由里 samples 的 partial 不影响它",
            )
        )
        try:
            evaluation = self.strategy.evaluate(strategy_id, as_of_date)
        except (ValueError, RuntimeError, OSError) as exc:
            reason = f"无法回测：{exc}"
            return (
                spec,
                samples,
                *(
                    StepOutcome(step, StepOutcomeStatus.GAP, reason)
                    for step in ("backtest_validation", "regime_analysis", "attempt_discount", "forward_tracking")
                ),
            )
        full = evaluation["full_sample"]
        oos, neighbourhood = evaluation["rolling_oos"], evaluation["neighbourhood"]
        validation = StepOutcome(
            "backtest_validation",
            StepOutcomeStatus.COMPLETED,
            f"{oos['verdict']}；{neighbourhood.get('verdict')}",
            artifacts={
                "evaluation_id": evaluation["evaluation_id"],
                "sample": full["sample"],
                "metrics": full["metrics"],
                "benchmarks": full.get("benchmarks"),
                "comparison": full["comparison"],
                "capacity": full["capacity"],
                "costs": full.get("costs"),
                "selection": full.get("selection"),
                "rolling_oos": oos,
                "neighbourhood": neighbourhood,
                "validation": evaluation["validation"],
                "gaps": evaluation["gaps"],
                "limitations": evaluation["limitations"],
            },
        )
        regimes = evaluation["regimes"]
        driven = set(regimes.get("exposure_driven") or ())
        readable = [
            f"{row['label']}：{row['reading']}"
            for name, rows in regimes.get("dimensions", {}).items()
            if name != "combined" and name not in driven
            for row in rows
            if row["readable"]
        ]
        current = regimes.get("current") or {}
        notes = [
            *(
                [f"策略仓位几乎由{'、'.join(_REGIME_NAMES.get(d, d) for d in sorted(driven))}维度决定，该维度的比较只是复述规则，不解读"]
                if driven
                else []
            ),
            *(
                [f"截至 {current['date']} 处于：" + "、".join(
                    value for key, value in current.items() if key != "date" and value
                )]
                if current.get("date")
                else []
            ),
        ]
        regime = StepOutcome(
            "regime_analysis",
            StepOutcomeStatus.GAP if regimes.get("error") else StepOutcomeStatus.COMPLETED,
            regimes.get("error") or "；".join([*(readable or ["各状态样本都不足，只列不读"]), *notes]),
            artifacts={"regimes": regimes},
        )
        significance = full["significance"]
        discount = StepOutcome(
            "attempt_discount",
            StepOutcomeStatus.COMPLETED if "dsr" in significance else StepOutcomeStatus.GAP,
            significance.get("verdict") or significance.get("error", "无法计算显著性"),
            artifacts={"significance": significance},
        )
        if evaluation["lifecycle_state"] in ("forward_tracking", "enabled"):
            tracked = self.strategy.track(strategy_id, as_of_date)
            forward = StepOutcome(
                "forward_tracking",
                StepOutcomeStatus.COMPLETED,
                f"自 {tracked['forward']['tracking_since']} 起跟踪，已结算信号 {tracked['forward']['settled_signals']} 个；"
                f"当前信号（{tracked.get('rule_signal_date')}）：{tracked.get('position')}",
                artifacts={
                    "forward": tracked["forward"],
                    "signal": {
                        key: tracked.get(key)
                        for key in ("status", "signal_date", "rule_signal_date", "rule_reason", "target_weights", "position")
                    },
                    "next_blockers": tracked["next_blockers"],
                },
            )
        else:
            forward = StepOutcome(
                "forward_tracking",
                StepOutcomeStatus.GAP,
                f"处于“{evaluation['state_label']}”，尚未进入前向跟踪，没有前向记录",
                artifacts={"next_state": evaluation["next_state"], "next_blockers": evaluation["next_blockers"]},
                next_actions=(
                    (f"strategy-archive --strategy-id {strategy_id} --transition {evaluation['next_state']} --reason \"…\"",)
                    if evaluation["next_state"] and not evaluation["next_blockers"]
                    else ()
                ),
            )
        return (spec, samples, validation, regime, discount, forward)

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
        if request.subject_refs and request.subject_type is SubjectType.SECTOR:
            # The user's wording, so adding resolved industries later does not record the view again.
            subject = request.subject_refs[0].text
        elif request.subject_refs:
            subject = request.subject_refs[0].resolved_id or request.subject_refs[0].text
        else:
            subject = f"{request.subject_type.value}:general"
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

    def _confirm_preference(self, request: ResearchRequest) -> StepOutcome:
        reader = getattr(self.portfolio, "preference_summary", None)
        refs = tuple(ref for ref in request.personal_refs if ref.startswith("preference:"))
        summary = (
            reader(request.user_id, refs)
            if reader is not None and request.user_id is not None
            else None
        )
        if summary is None:
            return StepOutcome(
                "confirm_preference",
                StepOutcomeStatus.GAP,
                "请求没有引用该用户已记录的偏好版本：先确认“激进”的含义，用 record-preference 记录，"
                "再把返回的 preference_id 写进 personal_refs。",
            )
        changes = "；".join(
            f"{item['label']} {item['before']} → {item['after']}" for item in summary["changes"]
        )
        return StepOutcome(
            "confirm_preference",
            StepOutcomeStatus.COMPLETED,
            ("本次变化：" + changes + "。" if changes else "")
            + "；".join(summary["readable"])
            + ("" if summary["is_latest"] else "。注意：引用的不是最新版本偏好"),
            artifacts=summary,
        )

    def _diagnose(self, request: ResearchRequest, cash: float | None = None) -> StepOutcome:
        if self.portfolio_diagnosis is None or request.user_id is None:
            return StepOutcome("portfolio_diagnosis", StepOutcomeStatus.GAP, "组合诊断未接入当前执行器。")
        try:
            result = self.portfolio_diagnosis.run(user_id=request.user_id, as_of_date=request.as_of_date, cash=cash)
        except (KeyError, ValueError, RuntimeError) as exc:
            return StepOutcome("portfolio_diagnosis", StepOutcomeStatus.BLOCKED, f"组合诊断未完成：{exc}")
        return StepOutcome(
            "portfolio_diagnosis",
            StepOutcomeStatus.COMPLETED,
            "；".join(result["main_risks"]) or "未发现突出的风险来源",
            artifacts=result,
            next_actions=(
                "回答时引用诊断报告里的数值与穿透覆盖率，写明“未穿透”部分和用 Beta 代理的持仓",
                "用户有未投资的现金时，用 run-recipe --cash <金额> 或 portfolio-diagnose --cash <金额> 计入",
            ),
        )

    def _decision_review(self, request: ResearchRequest, cash: float | None = None) -> tuple[StepOutcome, ...]:
        step_ids = ("track_record", "plan_review", "preference_calibration")
        if self.decision_review is None or request.user_id is None:
            return tuple(StepOutcome(item, StepOutcomeStatus.GAP, "决策复盘未接入当前执行器。") for item in step_ids)
        try:
            result = self.decision_review.run(user_id=request.user_id, as_of_date=request.as_of_date, cash=cash)
        except (KeyError, ValueError, RuntimeError) as exc:
            return tuple(StepOutcome(item, StepOutcomeStatus.BLOCKED, f"决策复盘未完成：{exc}") for item in step_ids)
        calibration = result.get("calibration")
        summary = result["summary"]
        due = result["review_nodes"]["due"]
        execution = result["execution"]
        plan_text = (
            f"已到期复盘节点 {len(due)} 个" if due else "没有到期的计划复盘节点"
        ) + (
            "；没有交易流水，无法核对执行"
            if execution is None
            else "；计划动作执行：" + ("、".join(f"{key} {value}" for key, value in execution["counts"].items()) or "无到期动作")
            + f"，计划外交易 {len(execution['unplanned_trades'])} 笔"
        )
        return (
            StepOutcome(
                "track_record",
                StepOutcomeStatus.COMPLETED,
                "；".join(item for item in summary if item.startswith(("观点", "动作", "账本中", "已包含"))),
                artifacts={"decisions": result["decisions"], "report_path": result.get("report_path")},
                next_actions=("样本少的分组只列出不解读；复盘时把结果和当时的依据分开谈",),
            ),
            StepOutcome(
                "plan_review",
                StepOutcomeStatus.COMPLETED,
                plan_text,
                artifacts={"review_nodes": result["review_nodes"], "execution": execution},
                next_actions=("用户说出实际买卖时用 record-trade 记录，再运行 decision-review",),
            ),
            StepOutcome(
                "preference_calibration",
                StepOutcomeStatus.COMPLETED if calibration else StepOutcomeStatus.GAP,
                (
                    "偏好与表现、行为一致"
                    if calibration and calibration["consistent"]
                    else "；".join(calibration["findings"])
                    if calibration
                    else "；".join(result["gaps"]) or "未做偏好校准"
                ),
                artifacts={"calibration": calibration},
                next_actions=("偏好不会自动修改：把不一致之处讲给用户，由用户选择调整偏好或调整组合",),
            ),
        )

    def _adjust(self, request: ResearchRequest, cash: float | None = None) -> tuple[StepOutcome, ...]:
        step_ids = ("risk_budget", "adjustment_plan", "migration_path", "stress_test")
        try:
            result = self.adjustment.run(user_id=request.user_id, as_of_date=request.as_of_date, cash=cash)
        except (KeyError, ValueError, RuntimeError) as exc:
            return tuple(
                StepOutcome(step_id, StepOutcomeStatus.BLOCKED, f"调整方案未完成：{exc}") for step_id in step_ids
            )
        if result.get("status") == "conflict":
            question = "；".join(result["summary"][-4:])
            return (
                StepOutcome(
                    "risk_budget",
                    StepOutcomeStatus.COMPLETED,
                    "；".join(result["allocation"]["summary"]),
                    artifacts={**result["allocation"], "report_path": result.get("report_path")},
                ),
                *(
                    StepOutcome(
                        step_id,
                        StepOutcomeStatus.NEEDS_INPUT,
                        question,
                        artifacts={"conflict": result["conflict"], "before": result["before"]},
                        next_actions=(
                            "把两个选项和各自的代价讲给用户，由用户选；选定后用 record-preference 记录新版本，再重新运行",
                        ),
                    )
                    for step_id in step_ids[1:]
                ),
            )
        migration = result["migration"]
        return (
            StepOutcome(
                "risk_budget",
                StepOutcomeStatus.COMPLETED,
                "；".join(result["allocation"]["summary"]),
                artifacts=result["allocation"],
            ),
            StepOutcome(
                "adjustment_plan",
                StepOutcomeStatus.COMPLETED,
                "；".join(result["summary"]),
                artifacts={"targets": result["targets"], "report_path": result.get("report_path")},
                next_actions=(
                    "增加部分只给到用户点名的候选、已持有的基金或默认宽基 ETF；用户有已研究的标的时用 "
                    "adjustment-proposal --candidate <代码>[:<类型>] 重新生成",
                ),
            ),
            StepOutcome(
                "migration_path",
                StepOutcomeStatus.COMPLETED,
                f"分 {len(migration['steps'])} 步，预计成本约 {migration['total_cost']:,.0f} 元"
                + (f"，资金缺口约 {migration['cash_shortfall']:,.0f} 元" if migration["cash_shortfall"] > 1 else ""),
                artifacts={"migration": migration, "buy_valuation": result["buy_valuation"]},
            ),
            StepOutcome(
                "stress_test",
                StepOutcomeStatus.COMPLETED,
                "；".join(
                    f"{left['label']} {left['portfolio_return']:.1%}→{right['portfolio_return']:.1%}"
                    for left, right in zip(result["before"]["stress"], result["after"]["stress"])
                ),
                artifacts={"before": result["before"], "after": result["after"]},
            ),
        )

    def _allocate(self, request: ResearchRequest, cash: float | None = None) -> StepOutcome:
        if self.allocation is None or request.user_id is None:
            return StepOutcome("risk_budget", StepOutcomeStatus.GAP, "配置与风险预算未接入当前执行器。")
        try:
            result = self.allocation.run(user_id=request.user_id, as_of_date=request.as_of_date, cash=cash)
        except (KeyError, ValueError, RuntimeError) as exc:
            return StepOutcome("risk_budget", StepOutcomeStatus.BLOCKED, f"配置与风险预算未完成：{exc}")
        return StepOutcome(
            "risk_budget",
            StepOutcomeStatus.COMPLETED,
            "；".join(result["summary"]),
            artifacts=result,
            next_actions=(
                "目标区间来自偏好与风险预算，回答时写明起作用的约束；发生冲突时让用户取舍，不替用户选",
            ),
        )

    def _plan(self, request: ResearchRequest, cash: float | None = None) -> tuple[StepOutcome, ...]:
        if self.plan is None or request.user_id is None:
            return (StepOutcome("plan_object", StepOutcomeStatus.GAP, "组合计划未接入当前执行器。"),)
        try:
            result = self.plan.run(user_id=request.user_id, as_of_date=request.as_of_date, cash=cash)
        except (KeyError, ValueError, RuntimeError) as exc:
            kind = getattr(exc, "kind", None)
            if kind == "conflict":
                return (
                    StepOutcome(
                        "plan_object",
                        StepOutcomeStatus.NEEDS_INPUT,
                        f"组合计划未生成：{exc}",
                        next_actions=("把两个选项讲给用户选择，用 record-preference 记录后重新运行",),
                    ),
                )
            missing_preference = "还没有记录投资偏好" in str(exc)
            return (
                StepOutcome(
                    "plan_object",
                    StepOutcomeStatus.GAP if missing_preference else StepOutcomeStatus.BLOCKED,
                    f"组合计划未生成：{exc}",
                    next_actions=(
                        ("计划需要已记录的偏好：先用 record-preference 记录后重试",)
                        if missing_preference
                        else ("取数或计算失败：稍后重试，或用 portfolio-plan --no-remote 只用本地已有价格",)
                    ),
                ),
            )
        plan = result["plan"]
        outcomes = [
            StepOutcome(
                "plan_object",
                StepOutcomeStatus.COMPLETED,
                f"计划预览（未入账）：{len(plan['actions'])} 个动作、{len(plan['review_nodes'])} 个复盘节点、"
                f"{len(plan['targets'])} 个目标区间；排除 {len(plan['excluded_actions'])} 个动作。",
                artifacts=result,
                next_actions=(
                    "把节点、目标与冲突处理讲给用户确认后，用 portfolio-plan --user-id <id> --record 入账",
                ),
            ),
            StepOutcome(
                "review_nodes",
                StepOutcomeStatus.COMPLETED,
                "；".join(f"{item['review_on']} {item['focus']}" for item in plan["review_nodes"][:3])
                + ("……" if len(plan["review_nodes"]) > 3 else ""),
                artifacts={"review_nodes": plan["review_nodes"]},
            ),
        ]
        if "html_path" in result:
            outcomes.append(
                StepOutcome(
                    "html_timeline",
                    StepOutcomeStatus.COMPLETED if result["html_path"] else StepOutcomeStatus.BLOCKED,
                    result["html_path"] or "HTML 与计划不一致，未发布：" + "；".join(result["html_problems"]),
                    artifacts={"html_path": result["html_path"], "html_problems": result["html_problems"]},
                )
            )
        return tuple(outcomes)

    def _summarize_plan(self, request: ResearchRequest) -> StepOutcome:
        """The user's own active actions and directional views; objective entries belong to no plan."""

        actions = [
            entry
            for entry in self.ledger.list_entries(
                user_id=request.user_id,
                entry_type=LedgerEntryType.ACTION,
                active_on=request.as_of_date,
            )
            if entry.user_id == request.user_id
        ]
        views = [
            entry
            for entry in self.ledger.list_entries(
                user_id=request.user_id,
                entry_type=LedgerEntryType.VIEW,
                active_on=request.as_of_date,
            )
            if entry.user_id == request.user_id and entry.artifact.claim is not None
        ]
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
            "账本中有效动作与复查节点的汇总；完整计划见 plan_object。",
            artifacts={
                "actions": [to_payload(item.artifact) for item in actions],
                "review_dates": review_dates,
            },
        )

    def _thesis_check(
        self,
        request: ResearchRequest,
        subjects: tuple[SubjectResolution, ...],
    ) -> tuple[StepOutcome, ...]:
        if self.thesis is None:
            return ()
        outcomes = []
        for subject in subjects:
            if subject.asset_id is None:
                continue
            result = self.thesis.check(subject.asset_id, as_of_date=request.as_of_date, user_id=request.user_id)
            if not result["theses"]:
                outcomes.append(
                    StepOutcome(
                        "thesis_check",
                        StepOutcomeStatus.GAP,
                        "账本中没有该持仓的论点：请用户说出当初的买入理由，改写为可观察指标后用 "
                        "record-thesis 记录（reconstructed=true），再运行 thesis-check。",
                        artifacts=result,
                    )
                )
                continue
            outcomes.append(
                StepOutcome(
                    "thesis_check",
                    StepOutcomeStatus.COMPLETED,
                    "；".join(
                        f"{item['summary']}：{item['status_label']}"
                        + ("（事后重建）" if item["reconstructed"] else "")
                        for item in result["theses"]
                    ),
                    artifacts=result,
                )
            )
        return tuple(outcomes)

    def _exit_plan(
        self,
        request: ResearchRequest,
        subjects: tuple[SubjectResolution, ...],
    ) -> tuple[StepOutcome, ...]:
        if self.exit_plan is None:
            return ()
        if request.user_id is None:
            message = "退出计划与行为偏差提示需要读取用户持仓：请求须带 user_id。"
            return (
                StepOutcome("exit_plan", StepOutcomeStatus.GAP, message),
                StepOutcome("behavioral_review", StepOutcomeStatus.GAP, message),
            )
        outcomes = []
        for subject in subjects:
            if subject.asset_id is None:
                continue
            try:
                result = self.exit_plan.run(
                    user_id=request.user_id,
                    asset_id=subject.asset_id,
                    as_of_date=request.as_of_date,
                )
            except (KeyError, ValueError, RuntimeError) as exc:
                outcomes += [
                    StepOutcome("exit_plan", StepOutcomeStatus.GAP, str(exc)),
                    StepOutcome("behavioral_review", StepOutcomeStatus.GAP, str(exc)),
                ]
                continue
            plan = result["plan"]
            outcomes.append(
                StepOutcome(
                    "exit_plan",
                    StepOutcomeStatus.COMPLETED,
                    "；".join(result["lines"]),
                    artifacts={
                        "plan": plan,
                        "ledger_entries": result["ledger_entries"],
                    },
                    next_actions=(
                        "把 plan 传给 final_decision.assemble_and_publish(exit_plan=ExitPlan.from_dict(plan))，"
                        "继续持有的结论必须带退出条件",
                    ),
                )
            )
            hints = plan["behavioral_hints"]
            outcomes.append(
                StepOutcome(
                    "behavioral_review",
                    StepOutcomeStatus.COMPLETED,
                    "；".join(f"{item['title']}：{item['evidence']}" for item in hints) or "没有触发规则型偏差提示",
                    artifacts={"hints": hints},
                    next_actions=(
                        "用户说出打算加仓、减仓或卖出时，用 exit-plan --intended-action 重新检查对应偏差",
                    ),
                )
            )
        return tuple(outcomes)

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
                "已读取持仓与最近一次组合估值。"
                if step_id == "portfolio_valuation"
                else "已读取持仓与最近一次组合估值；情景到组合暴露的映射尚未建立，"
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

    def _sector_brief(self, request: ResearchRequest) -> tuple[StepOutcome, ...]:
        if self.sector is None or not request.subject_refs:
            return (
                StepOutcome(
                    "sector_definition",
                    StepOutcomeStatus.GAP,
                    "行业档案未接入当前执行器，或请求没有行业引用。",
                ),
            )
        ref = request.subject_refs[0]
        try:
            industry = self.sector.resolve(ref)
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return (
                StepOutcome(
                    "sector_definition",
                    StepOutcomeStatus.BLOCKED,
                    f"申万行业目录不可用：{type(exc).__name__}: {exc}",
                ),
            )
        if industry is None:
            return (
                StepOutcome(
                    "sector_definition",
                    StepOutcomeStatus.NEEDS_INPUT,
                    f"“{ref.text}”不是申万行业名称：用 cli industry 找到对应行业，在 subject_refs 的 "
                    "resolved_id 写 sector:sw:<行业代码>；主题跨多个行业时逐个行业请求并写明映射依据。",
                ),
            )
        definition = StepOutcome(
            "sector_definition",
            StepOutcomeStatus.COMPLETED,
            f"“{ref.text}”对应申万{industry.level}级行业{industry.name}（{industry.code}），成分为申万当前成分。",
            artifacts=self.sector.definition(industry),
            next_actions=("用户说的领域与申万口径不一致时，说明差异或改用更合适的层级",),
        )
        try:
            archive = self.sector.run(request.as_of_date, code=industry.code)
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return (
                definition,
                StepOutcome(
                    "sector_fundamentals",
                    StepOutcomeStatus.BLOCKED,
                    f"行业档案数据不可用：{type(exc).__name__}: {exc}",
                ),
            )
        artifacts = {"archive": archive}
        if archive.get("ledger_entry_id"):
            artifacts["ledger_entry_id"] = archive["ledger_entry_id"]
        dimensions = "、".join(item["name"] for item in archive["assessment"].get("dimensions", ()))
        return (
            definition,
            StepOutcome(
                "sector_fundamentals",
                StepOutcomeStatus.COMPLETED,
                f"已生成行业档案并作为状态评估入账（{dimensions}）："
                f"{archive['assessment']['label']}。缺失维度、景气、产业链、资金流与政策见 archive.gaps。",
                artifacts=artifacts,
                next_actions=(
                    "逐个维度引用数值、分位窗口和样本数，不只引用标签",
                    "代表性标的用 industry --code <代码> --members 与 valuation-position 比较",
                ),
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

    def _sector_view(self, request: ResearchRequest) -> tuple[StepOutcome, ...]:
        recorded = self._record_user_view(request)
        if recorded.status is not StepOutcomeStatus.COMPLETED:
            return (recorded,)
        if self.sector is None or not request.subject_refs:
            return (
                recorded,
                StepOutcome("sector_brief", StepOutcomeStatus.GAP, "行业档案未接入当前执行器。"),
            )
        ref = request.subject_refs[0]
        try:
            industries = self.sector.resolve_many(ref)
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return (
                recorded,
                StepOutcome(
                    "sector_brief",
                    StepOutcomeStatus.BLOCKED,
                    f"申万行业目录不可用：{type(exc).__name__}: {exc}",
                ),
            )
        if not industries:
            return (
                recorded,
                StepOutcome(
                    "sector_brief",
                    StepOutcomeStatus.NEEDS_INPUT,
                    f"“{ref.text}”不是申万行业名称：用 cli industry 找到对应的一个或多个行业，"
                    "在 subject_refs[0].resolved_id 写 sector:sw:<代码>[,<代码>...]（保留用户原话作为 text），"
                    "写明映射依据后重跑；用户观点已入账，重跑不会重复入账。",
                ),
            )
        archives, templates, failures = [], [], []
        for industry in industries:
            try:
                archive = self.sector.run(request.as_of_date, code=industry.code)
                template = self.sector.view_template(
                    industry,
                    request_id=request.request_id,
                    user_id=request.user_id,
                    as_of_date=request.as_of_date,
                    horizon=request.horizon.value,  # type: ignore[union-attr]
                )
            except Exception as exc:  # noqa: BLE001 - one industry failing does not stop the others
                failures.append(f"{industry.name}（{industry.code}）：{type(exc).__name__}: {exc}")
                continue
            archives.append(
                {
                    "industry": {"code": industry.code, "name": industry.name, "level": industry.level},
                    "label": archive["assessment"]["label"],
                    "archive": archive,
                    "ledger_entry_id": archive.get("ledger_entry_id"),
                }
            )
            templates.append(template)
        names = "、".join(f"{item.name}（{item.code}）" for item in industries)
        sector_outcome = StepOutcome(
            "sector_brief",
            StepOutcomeStatus.BLOCKED if not archives else StepOutcomeStatus.COMPLETED,
            (
                f"“{ref.text}”按申万{names}分析，生成 {len(archives)} 份行业档案并入账"
                + (f"；失败：{'；'.join(failures)}" if failures else "")
                + "。"
            ),
            artifacts={"archives": archives, "view_templates": templates, "failures": failures},
            next_actions=(
                "把用户观点改写为行业指数相对沪深300 的可证伪命题：填写 view_templates 中的 proposition 与 "
                "expires_on，用 cli record-view --view-json <path> --supersedes-entry-id <用户观点条目> 入账",
                "逐个行业引用档案维度的数值与分位，先写反对用户观点的证据",
            ),
        )
        outcomes = [recorded, sector_outcome]
        if self.funnel is not None:
            discovery = self._funnel(request)
            outcomes.extend(
                StepOutcome(
                    _Q9_STEP_IDS.get(item.step_id, item.step_id),
                    item.status,
                    item.summary,
                    artifacts=item.artifacts,
                    next_actions=item.next_actions,
                )
                for item in discovery
                if item.step_id not in ("research_scope", "event_name_discovery")
            )
        return tuple(outcomes)

    def _funnel(self, request: ResearchRequest) -> tuple[StepOutcome, ...]:
        ref = request.subject_refs[0]
        try:
            scope = self.funnel.resolve_scope(ref)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return (
                StepOutcome(
                    "research_scope",
                    StepOutcomeStatus.BLOCKED,
                    f"申万行业目录不可用：{type(exc).__name__}: {exc}",
                ),
            )
        if scope is None:
            return (
                StepOutcome(
                    "research_scope",
                    StepOutcomeStatus.NEEDS_INPUT,
                    f"“{ref.text}”没有对应到发现范围：在 subject_refs 的 resolved_id 写 "
                    "sector:sw:<申万代码>、index:<指数代码>、market:cn_a 或 symbols:<代码,...>；"
                    "主题先映射为行业或标的清单并写明依据，风格（如低估值高股息）先选范围再看策略。",
                ),
            )
        try:
            run = self.funnel.run(scope, as_of_date=request.as_of_date)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - reported to the Agent as a blocked step
            return (
                StepOutcome(
                    "strategy_funnel",
                    StepOutcomeStatus.BLOCKED,
                    f"发现漏斗未能运行：{type(exc).__name__}: {exc}",
                ),
            )
        computed = [s["name"] for s in run["strategies"] if s["computed"]]
        packs = run.get("base_packs", [])
        done = [p for p in packs if p["status"] == StepOutcomeStatus.COMPLETED.value]
        failed = [f"{p['symbol']}：{p['summary']}" for p in packs if p not in done]
        shown = min(len(run["candidates"]), 10)
        return (
            StepOutcome(
                "research_scope",
                StepOutcomeStatus.COMPLETED,
                f"范围：{run['scope']['label']}，{run['scope']['member_count']} 只。",
                artifacts={"scope": run["scope"], "excluded": run["excluded"]},
            ),
            StepOutcome(
                "strategy_funnel",
                StepOutcomeStatus.COMPLETED,
                f"策略 {'、'.join(computed)} 共选出 {run['candidate_count']} 个候选（运行 {run['run_id']}）；"
                f"这里只列前 {shown} 个，全量用 cli discover --run-id <运行> --limit 0。",
                artifacts={
                    "run_id": run["run_id"],
                    "context": run["context"],
                    "excluded": run["excluded"],
                    "rule_history_basis": run.get("rule_history_basis"),
                    "strategies": run["strategies"],
                    "candidates": run["candidates"][:10],
                    "gaps": run["gaps"],
                },
                next_actions=(
                    "逐个候选引用入选理由、反证和数据缺口；候选是研究邀请，不是买入结论",
                    "引用规则历史表现时写明样本数；insufficient_samples 的不得作为依据",
                    "未入选标的的原因用 cli discover --run-id <id> --symbols <代码> 查询",
                ),
            ),
            StepOutcome(
                "multi_strategy_priority",
                StepOutcomeStatus.COMPLETED if packs else StepOutcomeStatus.GAP,
                (
                    f"前 {len(packs)} 名已生成基础包：{len(done)} 个完成"
                    + (f"；未完成：{'；'.join(failed)}" if failed else "")
                    + "。"
                )
                if packs
                else "没有候选，未生成基础包。",
                artifacts={"base_packs": packs},
                next_actions=("从基础包开始深度研究；未完成的包按 summary 处理或用 run-single 重跑",) if packs else (),
            ),
            StepOutcome(
                "event_name_discovery",
                StepOutcomeStatus.GAP,
                "本次未运行事件名称发现：它需要带来源的事件输入（run-recipe --discovery-input-json）。",
            ),
        )

    def _discover(self, discovery_request: Any | None) -> StepOutcome:
        if discovery_request is None or self.discovery is None:
            return StepOutcome(
                "event_name_discovery",
                StepOutcomeStatus.GAP,
                "事件名称发现需要事件、关系证据和反应数据输入；"
                "多策略发现漏斗未接入当前执行器或请求没有范围引用。",
            )
        return self.discovery.run(discovery_request)


def _view_checkpoints(view: View) -> tuple[str, ...]:
    if view.settlement_rule is None:
        return (view.expires_on,)
    return settlement_checkpoints(view.horizon, view.as_of_date, view.expires_on)
