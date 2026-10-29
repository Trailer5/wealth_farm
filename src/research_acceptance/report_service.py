"""Map formal execution outputs into structured research reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from src.benchmark_analysis import (
    BROAD_MARKET_FALLBACK_ROLES,
    BROAD_MARKET_SYMBOL,
)
from src.data_access import (
    DATASET_INDEX_DAILY_BARS,
    DATASET_OPEN_FUND_NAV,
    TOTAL_RETURN_BAR_DATASETS,
)
from src.fund_research import DisclosureExtractionStatus
from src.fundamental_analysis import MetricStatus, valuation_model_lines
from src.master_data import ASSET_TYPE_OPEN_FUND, MasterDataService
from src.peer_analysis import (
    PeerComparisonBundle,
    PeerComparisonStatus,
)
from src.research_evidence import EvidenceTier
from src.research_workflow.execution_models import (
    ResearchExecutionResult,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)
from src.research_workflow.profiles import default_profile_registry
from src.research_contracts import PROFILE_HIGH_GROWTH_UNPROFITABLE, ResearchQuestionType
from src.strategy import (
    ConditionGroup,
    ConditionOperator,
    MetricCondition,
    StrategyRegistry,
    StrategyResult,
    StrategyRunStatus,
    StrategySpec,
    default_strategy_registry,
)

from .models import (
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    InvalidationCondition,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchFact,
    ResearchFactKind,
    ResearchFactSection,
    ResearchGap,
    ResearchScenario,
    ScenarioKind,
)
from .service import RepresentativeResearchAssembler


@dataclass(frozen=True)
class OpenFundFactRequirement:
    requirement_id: str
    fact_labels: tuple[str, ...] = ()
    section_id: str | None = None
    minimum_fact_count: int = 0
    artifact_kinds: tuple[str, ...] = ()
    blocking: bool = False
    message: str = ""


OPEN_FUND_REQUIRED_FACT_MATRIX = (
    OpenFundFactRequirement(
        "identity",
        fact_labels=("基金身份", "产品类型"),
        blocking=True,
        message="基金身份或产品类型未形成可追溯事实。",
    ),
    OpenFundFactRequirement(
        "official_nav",
        fact_labels=("最新单位净值", "净值覆盖"),
        blocking=True,
        message="缺少可追溯的正式净值或净值覆盖事实。",
    ),
    OpenFundFactRequirement(
        "standard_performance",
        section_id="open_fund_standard_returns",
        minimum_fact_count=2,
        blocking=True,
        message="可用的标准区间收益事实不足。",
    ),
    OpenFundFactRequirement(
        "risk",
        fact_labels=("区间年化波动率", "区间最大回撤"),
        blocking=True,
        message="缺少基础风险事实。",
    ),
    OpenFundFactRequirement(
        "official_profile",
        fact_labels=("基金经理与任期", "持续运作费率", "业绩比较基准"),
        message="官方产品资料中的基金经理、费率或业绩比较基准字段不完整。",
    ),
    OpenFundFactRequirement(
        "official_periodic",
        fact_labels=("期末份额与净资产", "资产配置（占总资产）"),
        message="官方定期报告中的最新份额、净资产或配置字段不完整。",
    ),
    OpenFundFactRequirement(
        "portfolio_summary",
        fact_labels=("报告期净份额流量", "前十大持仓集中度"),
        message="份额变化或持仓集中度事实不完整。",
    ),
    OpenFundFactRequirement(
        "charts",
        artifact_kinds=(
            "cumulative_return",
            "drawdown",
            "rolling_risk",
        ),
        message="累计收益、回撤或滚动风险静态图表不完整。",
    ),
)


class ResearchReportAssemblyService:
    VERSION = "research_report_v1"

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        assembler: RepresentativeResearchAssembler | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ) -> None:
        self.master_data = master_data
        self.assembler = (
            assembler or RepresentativeResearchAssembler()
        )
        self.strategy_registry = (
            strategy_registry or default_strategy_registry()
        )

    def assemble(
        self,
        execution: ResearchExecutionResult,
        *,
        peer_comparison: PeerComparisonBundle | None = None,
    ) -> ResearchAcceptanceReport:
        asset = self.master_data.get_asset(
            execution.task.asset_id
        )
        if asset is None:
            raise KeyError(
                f"Unknown asset_id: {execution.task.asset_id}"
            )
        strategy = execution.strategy_result
        gaps = [
            ResearchGap(
                module=item.module,
                code=item.code,
                message=item.message,
                blocking=item.blocking,
            )
            for item in execution.gaps
        ]
        comparison_step = _comparison_step(
            _phase(execution, "benchmark"),
            peer_comparison,
            gaps,
        )
        if (
            asset.asset_type == ASSET_TYPE_OPEN_FUND
            and _phase(execution, "benchmark").status
            is ResearchPhaseStatus.NOT_APPLICABLE
            and not any(
                item.code.startswith("open_fund_benchmark_")
                for item in gaps
            )
        ):
            gaps.append(
                ResearchGap(
                    module="benchmark_analysis",
                    code="open_fund_benchmark_definition_missing",
                    message=(
                        "官方基准关系尚未建立，当前基础包"
                        "不包含相对表现。"
                    ),
                    blocking=False,
                )
            )
        steps = (
            _step("task", _phase(execution, "task")),
            _combined_step(
                "data_quality",
                (
                    _phase(execution, "bars"),
                    _phase(execution, "quality"),
                ),
            ),
            _combined_step(
                "metrics",
                (
                    _phase(execution, "factors"),
                    _phase(execution, "fundamental"),
                ),
                ignore_not_applicable=True,
            ),
            _step("charts", _phase(execution, "charts")),
            comparison_step,
            _step("strategy", _phase(execution, "strategy")),
            _step("evidence", _phase(execution, "evidence")),
        )
        if strategy is None:
            if not (
                execution.task.question_type
                is ResearchQuestionType.UNDERSTANDING
                and _phase(execution, "strategy").status
                is ResearchPhaseStatus.NOT_APPLICABLE
            ):
                gaps.append(
                    ResearchGap(
                        module="strategy",
                        code="strategy_result_missing",
                        message="执行未产生研究规则结果。",
                        blocking=True,
                    )
                )
        understanding_without_strategy = (
            strategy is None
            and execution.task.question_type
            is ResearchQuestionType.UNDERSTANDING
        )
        rule = _RuleView.of(
            strategy,
            (
                self.strategy_registry.get(
                    strategy.strategy_id,
                    strategy.strategy_version,
                )
                if strategy is not None
                else None
            ),
        )
        scenarios = _scenarios(
            rule,
            not_applicable=understanding_without_strategy,
        )
        counterargument = _counterargument(
            rule,
            not_applicable=understanding_without_strategy,
        )
        if execution.fund_bundle is not None:
            fact_sections = _fact_sections(execution)
        else:
            benchmark_name, broad_fallback = self._benchmark_context(
                execution
            )
            fact_sections = _security_fact_sections(
                execution,
                rule=rule,
                benchmark_name=benchmark_name,
                broad_fallback=broad_fallback,
            )
        _append_disclosure_gaps(execution, gaps)
        _append_open_fund_fact_matrix_gaps(
            execution,
            fact_sections,
            gaps,
        )
        invalidation_condition = (
            "刷新数据后，硬性门槛或否决条件的判定发生变化"
            if rule is not None
            else (
                "刷新后的净值、规模、配置或申赎状态"
                "发生重大变化"
            )
        )
        invalidation_evidence = (
            f"重新运行{rule.name}"
            if rule is not None
            else "刷新基金数据集与官方披露"
        )
        return self.assembler.assemble(
            acceptance_version=self.VERSION,
            task=execution.task,
            asset_name=(
                asset.display_name or asset.standard_symbol
            ),
            asset_type=asset.asset_type,
            steps=steps,
            scenarios=scenarios,
            strongest_counterargument=counterargument,
            invalidation_conditions=(
                InvalidationCondition(
                    condition=invalidation_condition,
                    evidence_required=invalidation_evidence,
                    severity="thesis_break",
                ),
            ),
            conclusions=_conclusions(execution, rule),
            gaps=tuple(gaps),
            fact_sections=fact_sections,
        )

    def _benchmark_context(
        self,
        execution: ResearchExecutionResult,
    ) -> tuple[str | None, bool]:
        benchmark_id = execution.task.benchmark_asset_id
        if benchmark_id is None:
            return None, False
        benchmark = self.master_data.get_asset(benchmark_id)
        if benchmark is None:
            return None, False
        profile = default_profile_registry().get(
            execution.task.profile_id
        )
        broad_fallback = (
            profile is not None
            and profile.default_benchmark_role
            in BROAD_MARKET_FALLBACK_ROLES
            and benchmark.standard_symbol == BROAD_MARKET_SYMBOL
        )
        return (
            benchmark.display_name or benchmark.standard_symbol,
            broad_fallback,
        )


def _phase(
    execution: ResearchExecutionResult,
    phase_id: str,
) -> ResearchPhaseResult:
    return next(
        item
        for item in execution.phases
        if item.phase_id == phase_id
    )


def _status(
    phase: ResearchPhaseResult,
) -> AcceptanceStepStatus:
    mapping = {
        ResearchPhaseStatus.SUCCESS: AcceptanceStepStatus.PASS,
        ResearchPhaseStatus.GAP: AcceptanceStepStatus.GAP,
        ResearchPhaseStatus.FAILED: AcceptanceStepStatus.FAIL,
        ResearchPhaseStatus.SKIPPED: AcceptanceStepStatus.GAP,
        ResearchPhaseStatus.NOT_APPLICABLE: (
            AcceptanceStepStatus.NOT_APPLICABLE
        ),
    }
    return mapping[phase.status]


def _step(
    step_id: str,
    phase: ResearchPhaseResult,
) -> AcceptanceStepResult:
    status = _status(phase)
    sources = phase.sources
    if status is AcceptanceStepStatus.PASS and not sources:
        sources = (f"execution:{phase.phase_id}",)
    return AcceptanceStepResult(
        step_id=step_id,
        status=status,
        summary=phase.summary,
        sources=sources,
        metric_versions=phase.metric_versions,
        artifacts=phase.artifacts,
        limitations=((phase.error,) if phase.error else ()),
    )


def _combined_step(
    step_id: str,
    phases: tuple[ResearchPhaseResult, ...],
    *,
    ignore_not_applicable: bool = False,
) -> AcceptanceStepResult:
    relevant = tuple(
        phase
        for phase in phases
        if not (
            ignore_not_applicable
            and phase.status is ResearchPhaseStatus.NOT_APPLICABLE
        )
    )
    statuses = {_status(phase) for phase in relevant}
    if AcceptanceStepStatus.FAIL in statuses:
        status = AcceptanceStepStatus.FAIL
    elif AcceptanceStepStatus.GAP in statuses:
        status = AcceptanceStepStatus.GAP
    elif statuses == {AcceptanceStepStatus.NOT_APPLICABLE}:
        status = AcceptanceStepStatus.NOT_APPLICABLE
    else:
        status = AcceptanceStepStatus.PASS
    sources = tuple(
        sorted(
            {
                source
                for phase in relevant
                for source in phase.sources
            }
        )
    )
    if status is AcceptanceStepStatus.PASS and not sources:
        sources = (f"execution:{step_id}",)
    return AcceptanceStepResult(
        step_id=step_id,
        status=status,
        summary="; ".join(phase.summary for phase in relevant),
        sources=sources,
        metric_versions=tuple(
            sorted(
                {
                    version
                    for phase in relevant
                    for version in phase.metric_versions
                }
            )
        ),
        artifacts=tuple(
            artifact
            for phase in relevant
            for artifact in phase.artifacts
        ),
        limitations=tuple(
            phase.error
            for phase in relevant
            if phase.error
        ),
    )


def _comparison_step(phase, peer, gaps):
    base = _step("comparison", phase)
    if peer is None:
        return base
    unavailable = [
        item.metric_name
        for item in peer.comparisons
        if item.status is not PeerComparisonStatus.AVAILABLE
    ]
    if unavailable:
        gaps.append(
            ResearchGap(
                module="peer_analysis",
                code="peer_comparison_incomplete",
                message=(
                    "同行比较中以下指标不可用："
                    f"{'、'.join(_metric_label(name) for name in unavailable)}。"
                ),
                blocking=False,
            )
        )
        status = AcceptanceStepStatus.GAP
    else:
        status = base.status
    return AcceptanceStepResult(
        step_id="comparison",
        status=status,
        summary=(
            f"{base.summary}; "
            f"{len(peer.comparisons)} peer comparisons"
        ),
        sources=tuple(
            sorted({*base.sources, "peer_analysis"})
        ),
        metric_versions=base.metric_versions,
        artifacts=base.artifacts,
        limitations=base.limitations,
    )


_RULE_STATUS_LABELS = {
    StrategyRunStatus.PASS: "通过",
    StrategyRunStatus.FAIL: "未通过",
    StrategyRunStatus.VETOED: "被否决",
    StrategyRunStatus.INSUFFICIENT_DATA: "数据不足，无法判定",
}

_PHASE_LABELS = {
    "bars": "行情",
    "quality": "数据质量检查",
    "factors": "因子与风险指标",
    "fundamental": "基本面指标",
    "benchmark": "相对基准",
    "evidence": "公告与研报证据",
    "charts": "静态图表",
    "strategy": "研究规则",
}


@dataclass(frozen=True)
class _RuleView:
    result: StrategyResult
    spec: StrategySpec | None

    @classmethod
    def of(
        cls,
        result: StrategyResult | None,
        spec: StrategySpec | None,
    ) -> "_RuleView | None":
        if result is None:
            return None
        return cls(result, spec)

    @property
    def name(self) -> str:
        return self.spec.name if self.spec is not None else "研究规则"

    @property
    def status_label(self) -> str:
        return _RULE_STATUS_LABELS[self.result.status]

    @property
    def reference(self) -> str:
        return f"strategy:{self.result.strategy_id}"

    def leaves(self) -> dict[str, MetricCondition]:
        if self.spec is None:
            return {}
        nodes = (
            *self.spec.hard_filters,
            *self.spec.vetoes,
            *(rule.condition for rule in self.spec.score_rules),
        )
        return {
            leaf.condition_id: leaf
            for node in nodes
            for leaf in _condition_leaves(node)
        }

    def describe(
        self,
        condition_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        leaves = self.leaves()
        return tuple(
            dict.fromkeys(
                _condition_text(leaves[item])
                for item in condition_ids
                if item in leaves
            )
        )


def _join_conditions(items: tuple[str, ...]) -> str:
    text = "；".join(items[:3])
    return f"{text}等" if len(items) > 3 else text


def _scenarios(rule, *, not_applicable=False):
    if rule is None:
        if not_applicable:
            claim = ResearchClaim(
                kind=ClaimKind.NOT_APPLICABLE,
                text="理解型基础研究不生成投资情景。",
            )
            return tuple(
                ResearchScenario(kind=kind, claims=(claim,))
                for kind in ScenarioKind
            )
        claim = ResearchClaim(
            kind=ClaimKind.UNKNOWN,
            text="缺少研究规则结果，暂无法生成情景。",
        )
        return tuple(
            ResearchScenario(kind=kind, claims=(claim,))
            for kind in ScenarioKind
        )
    result = rule.result
    reference = f"{rule.reference}:{result.status.value}"
    matched = rule.describe(result.matched_conditions)
    adverse = rule.describe(
        (*result.vetoed_conditions, *result.failed_conditions)
    )
    return (
        ResearchScenario(
            ScenarioKind.OPTIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    (
                        f"已满足的条件（{_join_conditions(matched)}）"
                        "在下次刷新后继续成立。"
                        if matched
                        else "未满足的条件在后续数据中改善并转为满足。"
                    ),
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.BASE,
            (
                ResearchClaim(
                    ClaimKind.FACT,
                    f"当前{rule.name}结论：{rule.status_label}。",
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.PESSIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    (
                        "未满足或已触发的条件"
                        f"（{_join_conditions(adverse)}）继续恶化，"
                        "或已满足的条件在刷新后失效。"
                        if adverse
                        else (
                            "已满足的条件在刷新后失效，"
                            "或规则本身的限制变得重要。"
                        )
                    ),
                    (reference,),
                ),
            ),
        ),
    )


def _counterargument(rule, *, not_applicable=False):
    if rule is None:
        if not_applicable:
            return ResearchClaim(
                ClaimKind.NOT_APPLICABLE,
                "理解型基础研究不自动生成投资反方。",
            )
        return ResearchClaim(
            ClaimKind.UNKNOWN,
            "缺少研究规则结果，最强反方暂不可知。",
        )
    result = rule.result
    vetoed = rule.describe(result.vetoed_conditions)
    failed = rule.describe(result.failed_conditions)
    if vetoed:
        text = f"已触发否决条件：{_join_conditions(vetoed)}。"
        if failed:
            text += f"另有未满足条件：{_join_conditions(failed)}。"
    elif failed:
        text = f"未满足的规则条件：{_join_conditions(failed)}。"
    elif result.missing_metrics:
        text = (
            "缺少必需输入："
            f"{'、'.join(_metric_label(name) for name in result.missing_metrics)}"
            "，规则结论不完整。"
        )
    elif result.limitations:
        text = f"规则本身的主要限制：{result.limitations[0]}"
    else:
        text = "规则未发现反向证据；结论仍只是研究规则结果。"
    return ResearchClaim(
        ClaimKind.INFERENCE,
        text,
        (rule.reference,),
    )


def _conclusions(execution, rule):
    if execution.fund_bundle is not None:
        completed = "已取得正式基金净值并完成质量检查。"
        evidence_refs = ["execution:fund-data"]
        if execution.risk_result is not None:
            completed = (
                "已取得正式基金净值，并完成质量检查与"
                "基础风险计算。"
            )
            evidence_refs.append("execution:risk-result")
        return (
            ResearchClaim(
                kind=ClaimKind.FACT,
                text=completed,
                evidence_refs=tuple(evidence_refs),
            ),
            ResearchClaim(
                kind=ClaimKind.INFERENCE,
                text=(
                    "本基础包只描述自动收集事实，不构成"
                    "投资建议。"
                ),
                evidence_refs=("execution:fund-data",),
            ),
        )
    completed = [
        _PHASE_LABELS[phase.phase_id]
        for phase in execution.phases
        if phase.phase_id in _PHASE_LABELS
        and phase.status is ResearchPhaseStatus.SUCCESS
    ]
    pending = [
        _PHASE_LABELS[phase.phase_id]
        for phase in execution.phases
        if phase.phase_id in _PHASE_LABELS
        and phase.status
        in {
            ResearchPhaseStatus.GAP,
            ResearchPhaseStatus.FAILED,
            ResearchPhaseStatus.SKIPPED,
        }
    ]
    collected = (
        f"自动收集已完成：{'、'.join(completed)}。"
        if completed
        else "自动收集未完成任何阶段。"
    )
    if pending:
        collected += f"未完成：{'、'.join(pending)}，详见缺口。"
    return (
        ResearchClaim(
            kind=ClaimKind.FACT,
            text=collected,
            evidence_refs=(
                f"task:{execution.task.task_id}",
                "execution:phases",
            ),
        ),
        ResearchClaim(
            kind=ClaimKind.INFERENCE,
            text=(
                (
                    f"{rule.name}结论为“{rule.status_label}”；"
                    "这是研究规则结果，不是投资指令。"
                )
                if rule is not None
                else "本基础包只描述自动收集事实，不构成投资建议。"
            ),
            evidence_refs=(
                (
                    rule.reference
                    if rule is not None
                    else "execution:phases"
                ),
            ),
        ),
    )


def _fact_sections(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFactSection, ...]:
    bundle = execution.fund_bundle
    if bundle is None:
        return ()
    sections = []
    identity = _open_fund_identity_facts(execution)
    if identity:
        sections.append(
            ResearchFactSection(
                section_id="open_fund_identity",
                title="基金身份",
                facts=identity,
            )
        )
    coverage = _coverage_facts(bundle.nav_records)
    if coverage:
        sections.append(
            ResearchFactSection(
                section_id="fund_nav",
                title="净值与数据覆盖",
                facts=coverage,
            )
        )
    risk_facts = _risk_facts(execution)
    if risk_facts:
        sections.append(
            ResearchFactSection(
                section_id="risk_metrics",
                title="收益与风险指标",
                facts=risk_facts,
            )
        )
    standard_facts = _open_fund_standard_facts(execution)
    if standard_facts:
        sections.append(
            ResearchFactSection(
                section_id="open_fund_standard_returns",
                title="标准区间表现",
                facts=standard_facts,
            )
        )
    benchmark_facts = _benchmark_facts(execution)
    if benchmark_facts:
        sections.append(
            ResearchFactSection(
                section_id="benchmark_metrics",
                title="相对基准表现",
                facts=benchmark_facts,
            )
        )
    fund_facts = _fund_record_facts(execution)
    if fund_facts:
        sections.append(
            ResearchFactSection(
                section_id="fund_records",
                title="基金状态、规模与配置",
                facts=fund_facts,
            )
        )
    historical_facts = _open_fund_historical_facts(execution)
    if historical_facts:
        sections.append(
            ResearchFactSection(
                section_id="open_fund_historical_summary",
                title="份额、规模与集中度",
                facts=historical_facts,
            )
        )
    sections.extend(_open_fund_decision_sections(execution))
    profile_facts = _profile_facts(bundle.profile_snapshot)
    if profile_facts:
        sections.append(
            ResearchFactSection(
                section_id="official_fund_profile",
                title="官方产品资料",
                facts=profile_facts,
            )
        )
    periodic_facts = _periodic_facts(bundle.periodic_snapshot)
    if periodic_facts:
        sections.append(
            ResearchFactSection(
                section_id="official_periodic_disclosure",
                title="官方定期报告",
                facts=periodic_facts,
            )
        )
    return tuple(sections)


def _open_fund_decision_sections(
    execution: ResearchExecutionResult,
) -> list[ResearchFactSection]:
    """申赎规则、费率拖累、现任经理任期与底层估值；口径见 docs/modules/3-分析工具/基金与ETF研究数据模块.md。"""

    facts = execution.open_fund_decision
    if not facts:
        return []
    as_of = execution.task.as_of_date
    asset_id = execution.task.asset_id
    sections = []
    dealing = facts.get("dealing")
    if dealing:
        page = (dealing["source_urls"] or ["eastmoney:fundf10"])[0]
        limits = (f"天天基金页面 {dealing['fetched_on']} 的当前规则。",)
        channel = ("销售渠道不同，申购费折扣可能不同。",)
        status = "、".join(
            item for item in (dealing["purchase_status"], dealing["redemption_status"]) if item
        ) or "未知"
        ladder = "；".join(
            f"{item['period_text']} {_percent_text(item['rate'])}" for item in dealing["redemption_fees"]
        )
        rows = [
            ("申赎状态", f"{status}；申购起点 {dealing['min_purchase_amount'] or '未知'} 元；日累计申购限额 {dealing['daily_purchase_limit'] or '未知'}"),
            (
                "成交与确认",
                "交易日 15:00 前提交按当日净值成交（未知价），之后顺延到下一交易日；"
                f"申购 T+{dealing['purchase_confirm_days'] if dealing['purchase_confirm_days'] is not None else '?'} 确认，"
                f"赎回 T+{dealing['redemption_confirm_days'] if dealing['redemption_confirm_days'] is not None else '?'} 确认",
            ),
            (
                "申购费率",
                f"原费率 {_percent_text(dealing['purchase_rate_original'])}，天天基金优惠费率 {_percent_text(dealing['purchase_rate_discounted'])}（100 万元以下档）",
            ),
            ("赎回费率", ladder or "未取得"),
        ]
        if dealing["fee_free_from_days"] is not None:
            rows.append(("免赎回费", f"持有满 {dealing['fee_free_from_days']} 天后赎回不收赎回费"))
        if dealing["round_trips"]:
            rows.append(
                (
                    "往返交易成本",
                    "；".join(
                        f"持有 {item['holding_days']} 天 {_percent_text(item['round_trip_cost'])}" for item in dealing["round_trips"]
                    ),
                )
            )
        sections.append(
            ResearchFactSection(
                section_id="open_fund_dealing",
                title="申赎规则与交易成本",
                facts=tuple(
                    ResearchFact(
                        label=label,
                        value=value,
                        as_of_date=min(dealing["fetched_on"], as_of),
                        kind=ResearchFactKind.DERIVED if label == "往返交易成本" else ResearchFactKind.REPORTED,
                        evidence_refs=(
                            (f"derived:open_fund_dealing:{asset_id}:{dealing['fetched_on']}",)
                            if label == "往返交易成本"
                            else (page,)
                        ),
                        limitations=(
                            limits + channel + ("前端申购费按净额外扣法：成本 = 1 − (1 − 赎回费率) ÷ (1 + 申购费率)。",)
                            if label == "往返交易成本"
                            else limits + channel
                            if label == "申购费率"
                            else limits
                        ),
                    )
                    for label, value in rows
                ),
            )
        )
        if dealing["fee_drag"]:
            drag = "；".join(
                f"{item['years']} 年 {_percent_text(item['ongoing_drag'])}"
                + (f"（含申赎 {_percent_text(item['total_drag'])}）" if item["total_drag"] else "")
                for item in dealing["fee_drag"]
            )
            sections.append(
                ResearchFactSection(
                    section_id="open_fund_fee_drag",
                    title="费率累计拖累",
                    facts=(
                        ResearchFact(
                            label="持续运作费率",
                            value=f"{_percent_text(dealing['ongoing_fee_rate'])}/年（管理费 + 托管费 + 销售服务费）",
                            as_of_date=min(dealing["fetched_on"], as_of),
                            kind=ResearchFactKind.REPORTED,
                            evidence_refs=(page,),
                            limitations=limits,
                        ),
                        ResearchFact(
                            label="累计拖累（占资产）",
                            value=drag,
                            as_of_date=as_of,
                            kind=ResearchFactKind.DERIVED,
                            evidence_refs=(f"derived:open_fund_fee_drag:{asset_id}:{dealing['fetched_on']}",),
                            limitations=(
                                "持续费用已从净值中扣除，历史收益是费后的；这里说明费用在长期中拿走了多少。",
                                "拖累 = 1 − (1 − 年费率)^年数；含申赎时再乘以该持有期的往返成本。",
                            ),
                        ),
                    ),
                )
            )
    tenure = facts.get("manager_tenure")
    if tenure and tenure["periods"]:
        sections.append(
            ResearchFactSection(
                section_id="open_fund_manager_tenure",
                title="现任经理任期表现",
                facts=tuple(
                    ResearchFact(
                        label=f"{item['label']}（{item['start_date']} 起）",
                        value=(
                            f"累计 {item['total_return']:.2%}"
                            + (
                                f"，年化 {item['annualized_return']:.2%}"
                                if item["annualized_return"] is not None
                                else "（不足一年不年化）"
                            )
                            + f"，最大回撤 {item['max_drawdown']:.2%}，共 {item['years']:.1f} 年（至 {item['end_date']}）"
                        ),
                        as_of_date=item["end_date"],
                        kind=ResearchFactKind.DERIVED,
                        evidence_refs=(f"derived:open_fund_manager_tenure:{asset_id}:{item['start_date']}",),
                        limitations=(
                            f"现任经理：{'、'.join(tenure['current_managers'])}；任期来自天天基金基金经理变动表。"
                            "增减共同经理也算一次变动，主经理是否更换看“连续管理”一行。",
                            "按复权净值计算，不含申赎费；未与同期业绩基准对照。",
                        ),
                    )
                    for item in tenure["periods"]
                ),
            )
        )
    valuation = facts.get("underlying_valuation")
    if valuation:
        valuation_facts = []
        index = valuation.get("index")
        if index and index.get("pe_ttm"):
            pe = index["pe_ttm"]
            basis = "跟踪指数" if valuation["basis"] == "tracking_index" else "业绩基准权益部分"
            weight = valuation.get("component_weight")
            text = (
                f"{index['name']}（{basis}" + (f"，权重 {weight:.0%}" if weight else "") + "）"
                f"滚动市盈率 {pe['value']:.2f} 倍（{pe['window_end']}），近 10 年分位 {pe['percentile']:.0%}（{pe['sample_count']} 个观测）"
            )
            if index.get("pb"):
                text += f"；市净率 {index['pb']['value']:.2f} 倍，分位 {index['pb']['percentile']:.0%}"
            valuation_facts.append(
                ResearchFact(
                    label="底层指数估值",
                    value=text,
                    as_of_date=pe["window_end"],
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=(f"macro:{pe['series_id']}",),
                    limitations=tuple(item for item in (valuation.get("note"), index["source"]) if item),
                )
            )
        else:
            context = (valuation.get("market_context") or {}).get("pe_ttm")
            value = valuation.get("note") or "底层指数估值不可用"
            if context:
                value += (
                    f"；沪深300 滚动市盈率 {context['value']:.2f} 倍，近 10 年分位 {context['percentile']:.0%}（{context['window_end']}）"
                )
            valuation_facts.append(
                ResearchFact(
                    label="底层指数估值",
                    value=value,
                    as_of_date=as_of,
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=("macro:csi300_pe_ttm",) if context else ("derived:open_fund_underlying_valuation",),
                    limitations=("市场整体估值不代表基金底层。",),
                )
            )
        sections.append(
            ResearchFactSection(
                section_id="open_fund_underlying_valuation",
                title="底层估值位置",
                facts=tuple(valuation_facts),
            )
        )
    return sections


def _percent_text(value: object) -> str:
    if value is None:
        return "未知"
    return f"{float(value):.2%}"


def _open_fund_identity_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    bundle = execution.fund_bundle
    assert bundle is not None
    snapshot = bundle.profile_snapshot
    official_identity = (
        snapshot is not None
        and snapshot.extraction_status
        is not DisclosureExtractionStatus.EXTRACTION_FAILED
        and bool(
            snapshot.share_class_name.strip()
            and snapshot.share_class_code.strip()
        )
    )
    if official_identity:
        name = snapshot.share_class_name
        code = snapshot.share_class_code
        value_date = snapshot.effective_date
        kind = ResearchFactKind.OFFICIAL
        refs = (_snapshot_reference(snapshot),)
    else:
        name = (
            bundle.asset.display_name
            or bundle.asset.standard_symbol
        )
        code = bundle.asset.standard_symbol
        value_date = execution.task.as_of_date
        kind = ResearchFactKind.REPORTED
        refs = ("master_data:open_fund_identity",)
    return (
        ResearchFact(
            label="基金身份",
            value=f"{name}（{code}）",
            as_of_date=value_date,
            kind=kind,
            evidence_refs=refs,
            limitations=(
                ()
                if official_identity
                else ("官方产品资料未形成可用身份字段，暂用主数据身份。",)
            ),
        ),
        ResearchFact(
            label="产品类型",
            value="开放式基金",
            as_of_date=value_date,
            kind=kind,
            evidence_refs=refs,
        ),
    )


def _profile_facts(snapshot) -> tuple[ResearchFact, ...]:
    if snapshot is None:
        return ()
    if (
        snapshot.extraction_status
        is DisclosureExtractionStatus.EXTRACTION_FAILED
    ):
        return (
            _disclosure_status_fact(
                snapshot,
                "产品资料概要",
                "解析失败；已保留官方文档元数据，不能解释为未披露。",
            ),
        )
    limitations = (
        ("部分字段未能从 PDF 稳定提取。",)
        if snapshot.extraction_status
        is DisclosureExtractionStatus.PARTIAL
        else ()
    )
    facts = []
    if snapshot.manager_name or snapshot.custodian_name:
        values = []
        if snapshot.manager_name:
            values.append(f"管理人：{snapshot.manager_name}")
        if snapshot.custodian_name:
            values.append(f"托管人：{snapshot.custodian_name}")
        facts.append(
            ResearchFact(
                label="管理人与托管人",
                value="；".join(values),
                as_of_date=snapshot.effective_date,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=(
                    _snapshot_reference(
                        snapshot,
                        snapshot.page_numbers.get("manager_name")
                        or snapshot.page_numbers.get(
                            "custodian_name"
                        ),
                    ),
                ),
                limitations=limitations,
            )
        )
    if snapshot.fund_managers:
        facts.append(
            ResearchFact(
                label="基金经理与任期",
                value="；".join(
                    f"{item.name}（{item.start_date}起）"
                    for item in snapshot.fund_managers
                ),
                as_of_date=snapshot.effective_date,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in snapshot.fund_managers
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    if snapshot.benchmark_text:
        facts.append(
            ResearchFact(
                label="业绩比较基准",
                value=snapshot.benchmark_text,
                as_of_date=snapshot.effective_date,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=(
                    _snapshot_reference(
                        snapshot,
                        snapshot.page_numbers.get("benchmark_text"),
                    ),
                ),
                limitations=limitations,
            )
        )
    fee_values = []
    for label, value in (
        ("管理费", snapshot.management_fee_rate),
        ("托管费", snapshot.custody_fee_rate),
        ("销售服务费", snapshot.sales_service_fee_rate),
    ):
        if value is not None:
            fee_values.append(
                f"{label} {_ratio_percent(value)}%/年"
            )
    if fee_values:
        facts.append(
            ResearchFact(
                label="持续运作费率",
                value="；".join(fee_values),
                as_of_date=snapshot.effective_date,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(snapshot, page)
                            for key, page in snapshot.page_numbers.items()
                            if key.endswith("_fee_rate")
                        }
                    )
                )
                or (_snapshot_reference(snapshot),),
                limitations=limitations,
            )
        )
    if limitations:
        facts.append(
            _disclosure_status_fact(
                snapshot,
                "产品资料概要",
                "部分提取完成；缺失字段不会按零值或未披露处理。",
            )
        )
    return tuple(facts)


def _periodic_facts(snapshot) -> tuple[ResearchFact, ...]:
    if snapshot is None:
        return ()
    if (
        snapshot.extraction_status
        is DisclosureExtractionStatus.EXTRACTION_FAILED
    ):
        return (
            _disclosure_status_fact(
                snapshot,
                "定期报告",
                "解析失败；已保留官方文档元数据，不能解释为未披露。",
            ),
        )
    limitations = (
        ("部分表格未能从 PDF 稳定提取。",)
        if snapshot.extraction_status
        is DisclosureExtractionStatus.PARTIAL
        else ()
    )
    facts = []
    if snapshot.performance_comparisons:
        comparisons = snapshot.performance_comparisons[:6]
        facts.append(
            ResearchFact(
                label="官方披露区间业绩",
                value="；".join(
                    (
                        f"{item.share_class_name + ' ' if item.share_class_name else ''}"
                        f"{item.period_label}：基金"
                        f"{_ratio_percent(item.fund_return)}%，"
                        f"基准{_ratio_percent(item.benchmark_return)}%，"
                        f"差值{_ratio_percent(item.tracking_difference)}%"
                    )
                    for item in comparisons
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in comparisons
                        }
                    )
                ),
                limitations=("沿用定期报告披露的区间与计算口径。",),
            )
        )
    if snapshot.share_classes:
        facts.append(
            ResearchFact(
                label="期末份额与净资产",
                value="；".join(
                    _share_class_summary(item)
                    for item in snapshot.share_classes
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in snapshot.share_classes
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    allocations = [
        item
        for item in snapshot.asset_allocations
        if item.category != "合计"
    ]
    if allocations:
        allocations.sort(key=lambda item: item.ratio, reverse=True)
        facts.append(
            ResearchFact(
                label="资产配置（占总资产）",
                value="；".join(
                    f"{item.category} {_ratio_percent(item.ratio)}%"
                    for item in allocations[:6]
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in allocations[:6]
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    industries = sorted(
        snapshot.industry_allocations,
        key=lambda item: item.net_asset_ratio,
        reverse=True,
    )
    if industries:
        facts.append(
            ResearchFact(
                label="行业配置（占净资产）",
                value="；".join(
                    (
                        f"{_industry_scope(item)}{item.category} "
                        f"{_ratio_percent(item.net_asset_ratio)}%"
                    )
                    for item in industries[:8]
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in industries[:8]
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    if snapshot.top_holdings:
        facts.append(
            ResearchFact(
                label="前十大股票",
                value="；".join(
                    (
                        f"{item.rank}.{item.security_name} "
                        f"{_ratio_percent(item.net_asset_ratio)}%"
                    )
                    for item in snapshot.top_holdings
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in snapshot.top_holdings
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    if snapshot.share_flows:
        facts.append(
            ResearchFact(
                label="报告期份额变化",
                value="；".join(
                    (
                        f"{item.share_class_name}："
                        f"{_number(item.opening_shares / Decimal('100000000'), 2)}"
                        f"亿份→"
                        f"{_number(item.ending_shares / Decimal('100000000'), 2)}"
                        "亿份"
                    )
                    for item in snapshot.share_flows
                ),
                as_of_date=snapshot.report_period_end,
                kind=ResearchFactKind.OFFICIAL,
                evidence_refs=tuple(
                    sorted(
                        {
                            _snapshot_reference(
                                snapshot,
                                item.page_number,
                            )
                            for item in snapshot.share_flows
                        }
                    )
                ),
                limitations=limitations,
            )
        )
    if limitations:
        facts.append(
            _disclosure_status_fact(
                snapshot,
                "定期报告",
                "部分提取完成；缺失字段不会按零值或未披露处理。",
            )
        )
    return tuple(facts)


def _append_disclosure_gaps(
    execution: ResearchExecutionResult,
    gaps: list[ResearchGap],
) -> None:
    bundle = execution.fund_bundle
    if bundle is None:
        return
    for kind, snapshot in (
        ("profile", bundle.profile_snapshot),
        ("periodic", bundle.periodic_snapshot),
    ):
        if snapshot is None:
            continue
        if (
            snapshot.extraction_status
            is DisclosureExtractionStatus.EXTRACTED
        ):
            continue
        failed = (
            snapshot.extraction_status
            is DisclosureExtractionStatus.EXTRACTION_FAILED
        )
        gaps.append(
            ResearchGap(
                module="fund_research",
                code=(
                    f"fund_{kind}_extraction_failed"
                    if failed
                    else f"fund_{kind}_extraction_partial"
                ),
                message=(
                    f"官方{snapshot.source_title}"
                    f"{'解析失败' if failed else '仅部分解析'}；"
                    "文档元数据已保留。"
                ),
                blocking=False,
            )
        )


def _append_open_fund_fact_matrix_gaps(
    execution: ResearchExecutionResult,
    sections: tuple[ResearchFactSection, ...],
    gaps: list[ResearchGap],
) -> None:
    if execution.fund_bundle is None:
        return
    facts_by_label = {
        fact.label: fact
        for section in sections
        for fact in section.facts
    }
    sections_by_id = {
        section.section_id: section for section in sections
    }
    artifact_kinds = {
        artifact.split(":", 1)[0]
        for phase in execution.phases
        if phase.phase_id == "charts"
        for artifact in phase.artifacts
    }
    existing_codes = {gap.code for gap in gaps}
    for requirement in OPEN_FUND_REQUIRED_FACT_MATRIX:
        labels_complete = all(
            label in facts_by_label
            for label in requirement.fact_labels
        )
        section = (
            sections_by_id.get(requirement.section_id)
            if requirement.section_id is not None
            else None
        )
        section_complete = (
            requirement.section_id is None
            or (
                section is not None
                and len(section.facts)
                >= requirement.minimum_fact_count
            )
        )
        artifacts_complete = set(
            requirement.artifact_kinds
        ).issubset(artifact_kinds)
        if labels_complete and section_complete and artifacts_complete:
            continue
        code = f"open_fund_required_{requirement.requirement_id}_missing"
        if code in existing_codes:
            continue
        gaps.append(
            ResearchGap(
                module="research_acceptance",
                code=code,
                message=requirement.message,
                blocking=requirement.blocking,
            )
        )
        existing_codes.add(code)

    has_benchmark_facts = "benchmark_metrics" in sections_by_id
    has_benchmark_gap = any(
        code.startswith("open_fund_benchmark_")
        for code in existing_codes
    )
    if not has_benchmark_facts and not has_benchmark_gap:
        gaps.append(
            ResearchGap(
                module="research_acceptance",
                code="open_fund_required_benchmark_outcome_missing",
                message=(
                    "既没有形成相对基准事实，也没有记录基准定义、"
                    "来源或覆盖缺口。"
                ),
                blocking=False,
            )
        )


def _disclosure_status_fact(
    snapshot,
    document_kind: str,
    message: str,
) -> ResearchFact:
    return ResearchFact(
        label=f"{document_kind}解析状态",
        value=message,
        as_of_date=snapshot.announcement_date,
        kind=ResearchFactKind.OFFICIAL,
        evidence_refs=(_snapshot_reference(snapshot),),
        limitations=("需修复解析或人工核验后才能补齐字段。",),
    )


def _snapshot_reference(snapshot, page: int | None = None) -> str:
    suffix = f"#page={page}" if page is not None else ""
    return f"{snapshot.source_url}{suffix}"


def _ratio_percent(value: Decimal) -> str:
    return _number(value * Decimal("100"), 2)


def _share_class_summary(item) -> str:
    values = [
        (
            f"{item.share_class_name}（{item.share_class_code}）"
            f" {_number(item.ending_shares / Decimal('100000000'), 2)}亿份"
        )
    ]
    if item.net_asset_value is not None:
        values.append(
            f"净资产{_number(item.net_asset_value / Decimal('100000000'), 2)}亿元"
        )
    if item.unit_nav is not None:
        values.append(f"份额净值{_number(item.unit_nav, 4)}元")
    return "，".join(values)


def _industry_scope(item) -> str:
    if item.market_scope == "hong_kong":
        return "港股-"
    if item.portfolio_scope == "index":
        return "指数投资-"
    if item.portfolio_scope == "active":
        return "积极投资-"
    return ""


def _coverage_facts(
    records: tuple[dict[str, Any], ...],
) -> tuple[ResearchFact, ...]:
    dated = [
        (record, _record_date(record))
        for record in records
        if _record_date(record) is not None
    ]
    if not dated:
        return ()
    dated.sort(key=lambda item: item[1])
    first_date = dated[0][1]
    latest_record, latest_date = dated[-1]
    assert first_date is not None and latest_date is not None
    facts = [
        ResearchFact(
            label="净值覆盖",
            value=(
                f"{len(dated)}条，{first_date}至{latest_date}"
            ),
            as_of_date=latest_date,
            kind=ResearchFactKind.REPORTED,
            evidence_refs=(_record_source(latest_record, "open_fund_nav"),),
        )
    ]
    latest_nav = _first_value(
        latest_record,
        "unit_nav",
        "nav",
        "close",
    )
    if latest_nav is not None:
        facts.insert(
            0,
            ResearchFact(
                label="最新单位净值",
                value=_number(latest_nav, digits=4),
                unit="元",
                as_of_date=latest_date,
                kind=ResearchFactKind.REPORTED,
                evidence_refs=(
                    _record_source(latest_record, "open_fund_nav"),
                ),
            ),
        )
    return tuple(facts)


_RISK_FACTS = (
    ("annualized_return", "区间年化收益率", "percent"),
    ("annualized_volatility", "区间年化波动率", "percent"),
    ("max_drawdown", "区间最大回撤", "percent"),
    ("sharpe_ratio", "区间夏普比率", "ratio"),
    ("sortino_ratio", "区间索提诺比率", "ratio"),
    ("calmar_ratio", "区间卡玛比率", "ratio"),
    ("drawdown_duration_days", "最长回撤持续时间", "days"),
)


def _risk_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    result = execution.risk_result
    if result is None:
        return ()
    by_name = {item.factor_name: item for item in result.metrics}
    facts = []
    for factor_name, label, display_kind in _RISK_FACTS:
        metric = by_name.get(factor_name)
        if metric is None:
            continue
        if display_kind == "percent":
            value = _number(metric.value * 100, digits=2)
            unit = "%"
        elif display_kind == "days":
            value = _number(metric.value, digits=0)
            unit = "天"
        else:
            value = _number(metric.value, digits=3)
            unit = None
        limitations = (_return_basis_limitation(metric.source_dataset),)
        facts.append(
            ResearchFact(
                label=label,
                value=value,
                unit=unit,
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(
                    f"derived:{metric.source_dataset}:{factor_name}",
                ),
                limitations=limitations,
            )
        )
    return tuple(facts)


_OPEN_FUND_STANDARD_ORDER = (
    "1m",
    "3m",
    "6m",
    "ytd",
    "1y",
    "3y",
    "5y",
    "since_inception",
)


def _open_fund_standard_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    result = execution.open_fund_standard_metrics
    if result is None:
        return ()
    by_key = {
        str(item.parameters.get("window_key")): item
        for item in result.values
    }
    facts = []
    for key in _OPEN_FUND_STANDARD_ORDER:
        metric = by_key.get(key)
        if metric is None:
            continue
        label = str(metric.parameters.get("window_label") or key)
        tolerance = metric.parameters.get("start_tolerance_days")
        if tolerance is None:
            window_limit = (
                "实际输入区间为"
                f"{metric.input_start_date}至{metric.input_end_date}；"
                "成立以来起点采用首条正式净值。"
            )
        else:
            window_limit = (
                "实际输入区间为"
                f"{metric.input_start_date}至{metric.input_end_date}；"
                "非成立以来窗口的起点采用目标日期当日或之前"
                f"{tolerance}天内最近正式净值。"
            )
        limitations = [window_limit]
        if result.nav_basis == "adjusted_nav":
            limitations.append(_ADJUSTED_NAV_LIMITATION)
        elif result.nav_basis == "accumulated_nav":
            limitations.append(
                "按累计净值变化计算，不等同于现金分红再投资总回报。"
            )
        else:
            limitations.append(
                "累计净值不可用，按单位净值变化计算；"
                "不含现金分红再投资。"
            )
        facts.append(
            ResearchFact(
                label=f"{label}收益率",
                value=_number(metric.value * 100, digits=2),
                unit="%",
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(
                    f"derived:{metric.source_dataset}:"
                    f"{metric.factor_name}",
                ),
                limitations=tuple(limitations),
            )
        )
    return tuple(facts)


_OPEN_FUND_HISTORICAL_FACTS = (
    (
        "open_fund_net_share_flow",
        "报告期净份额流量",
        "shares",
    ),
    (
        "open_fund_net_share_flow_rate",
        "报告期净份额流量占期初份额",
        "percent",
    ),
    (
        "open_fund_scale_change",
        "可比规模记录变化",
        "upstream",
    ),
    (
        "open_fund_scale_change_rate",
        "可比规模记录变化率",
        "percent",
    ),
    (
        "open_fund_top10_concentration",
        "前十大持仓集中度",
        "percent",
    ),
    (
        "open_fund_hong_kong_exposure",
        "港股行业暴露",
        "percent",
    ),
    (
        "open_fund_top3_industry_concentration",
        "前三大行业集中度",
        "percent",
    ),
    (
        "open_fund_ongoing_fee_rate",
        "披露持续运作费率合计",
        "percent",
    ),
)


def _open_fund_historical_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    by_name = {
        item.factor_name: item
        for item in execution.factor_values
        if item.factor_name.startswith("open_fund_")
    }
    facts = []
    for factor_name, label, display_kind in _OPEN_FUND_HISTORICAL_FACTS:
        metric = by_name.get(factor_name)
        if metric is None:
            continue
        limitations = []
        if display_kind == "shares":
            value = _number(metric.value / 100_000_000, digits=2)
            unit = "亿份"
        elif display_kind == "percent":
            value = _number(metric.value * 100, digits=2)
            unit = "%"
        else:
            value = _number(metric.value, digits=2)
            unit = None
            limitations.append("沿用上游规模记录单位。")
        methodology = {
            "open_fund_net_share_flow": (
                "按官方定期报告的申购份额减赎回份额计算。"
            ),
            "open_fund_net_share_flow_rate": (
                "净份额流量除以报告期期初份额。"
            ),
            "open_fund_scale_change": (
                "按前后两条同口径规模记录计算差额。"
            ),
            "open_fund_scale_change_rate": (
                "可比规模记录差额除以前一期记录。"
            ),
            "open_fund_top10_concentration": (
                "按官方定期报告前十大股票占净资产比例求和。"
            ),
            "open_fund_hong_kong_exposure": (
                "按官方定期报告中港股行业占净资产比例求和。"
            ),
            "open_fund_top3_industry_concentration": (
                "按同一配置口径下前三大行业占比求和。"
            ),
            "open_fund_ongoing_fee_rate": (
                "按官方披露的管理费、托管费和销售服务费率求和。"
            ),
        }[factor_name]
        limitations.insert(0, methodology)
        if factor_name == "open_fund_ongoing_fee_rate":
            limitations.append(
                "静态披露费率仅作事实汇总，不作为已实现收益拖累。"
            )
        facts.append(
            ResearchFact(
                label=label,
                value=value,
                unit=unit,
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(
                    f"derived:{metric.source_dataset}:{factor_name}",
                ),
                limitations=tuple(limitations),
            )
        )
    return tuple(facts)


_BENCHMARK_FACTS = (
    ("benchmark_total_return", "区间基准累计收益率", "percent"),
    (
        "cumulative_tracking_difference",
        "累计跟踪差值",
        "percent",
    ),
    ("tracking_error", "年化跟踪误差", "percent"),
    ("information_ratio", "信息比率", "ratio"),
    ("beta", "Beta", "ratio"),
    ("alpha", "年化 Alpha", "percent"),
    ("common_observation_count", "共同观察日数", "days"),
)


def _benchmark_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    result = execution.benchmark_result
    if result is None:
        return ()
    by_name = {item.factor_name: item for item in result.metrics}
    facts = []
    definition = result.benchmark_definition
    source_refs = (
        tuple(
            sorted(
                {
                    component.source
                    for component in definition.components
                }
            )
        )
        if definition is not None
        else ("derived:benchmark",)
    )
    limitations = [
        (
            "仅使用基金净值与全部基准组件均有观察值的"
            "共同日期区间计算。"
        )
    ]
    if result.metrics:
        first_metric = result.metrics[0]
        limitations.append(
            "实际输入区间为"
            f"{first_metric.input_start_date}至"
            f"{first_metric.input_end_date}。"
        )
    if definition is not None:
        limitations.append(
            "复合基准按官方权重合成；任一组件不可用时不降级。"
        )
        for component in definition.components:
            if getattr(component.kind, "value", "") == "cash_rate":
                limitations.append(
                    "现金利率组件按"
                    f"{component.rate_effective_date}披露的固定年利率"
                    "延展，未重建逐次利率调整历史。"
                )
    for factor_name, label, display_kind in _BENCHMARK_FACTS:
        metric = by_name.get(factor_name)
        if metric is None:
            continue
        if display_kind == "percent":
            value = _number(metric.value * 100, digits=2)
            unit = "%"
        elif display_kind == "days":
            value = _number(metric.value, digits=0)
            unit = "个"
        else:
            value = _number(metric.value, digits=3)
            unit = None
        facts.append(
            ResearchFact(
                label=label,
                value=value,
                unit=unit,
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=source_refs,
                limitations=tuple(limitations),
            )
        )
    return tuple(facts)


_SECURITY_BAR_LIMITATION = (
    "按执行窗口内的日频未复权收盘价计算，不含分红；"
    "窗口内发生送转或拆分时，收益与回撤会失真。"
)
_TOTAL_RETURN_LIMITATION = (
    "按执行窗口内的复权总回报计算（交易所前收盘价链接，"
    "含分红再投资，已消除送转、拆分和份额折算的影响）。"
)
_ADJUSTED_NAV_LIMITATION = (
    "按执行窗口内的复权净值计算（单位净值与公布的日增长率链接，"
    "含分红再投资）；缺少日增长率的日期按单位净值变化计算。"
)


def _return_basis_limitation(source_dataset: str) -> str:
    if source_dataset in TOTAL_RETURN_BAR_DATASETS:
        return _TOTAL_RETURN_LIMITATION
    if source_dataset == DATASET_OPEN_FUND_NAV:
        return _ADJUSTED_NAV_LIMITATION
    return _SECURITY_BAR_LIMITATION


def _benchmark_basis_limitation(source_dataset: str) -> str:
    asset_dataset, _, benchmark_dataset = source_dataset.partition("|")
    if asset_dataset not in TOTAL_RETURN_BAR_DATASETS:
        return "按标的与基准共同交易日的未复权收盘价计算，不含分红；"
    if benchmark_dataset == DATASET_INDEX_DAILY_BARS:
        return (
            "标的按复权总回报计算，基准为价格指数、不含分红，"
            "超额收益偏高约一个区间股息率；"
        )
    return "标的与基准均按复权总回报计算；"

_METRIC_DISPLAY: dict[str, tuple[str, str]] = {
    "market_cap": ("总市值", "amount"),
    "enterprise_value": ("企业价值", "amount"),
    "pe_ttm": ("市盈率（TTM）", "multiple"),
    "pe_ttm_recurring": ("扣非市盈率（TTM）", "multiple"),
    "pb": ("市净率", "multiple"),
    "ps_ttm": ("市销率（TTM）", "multiple"),
    "pcf_ttm": ("市现率（TTM）", "multiple"),
    "ev_ebitda": ("EV/EBITDA", "multiple"),
    "ev_fcf": ("EV/自由现金流", "multiple"),
    "earnings_yield": ("盈利收益率", "percent"),
    "fcf_yield": ("自由现金流收益率", "percent"),
    "dividend_yield": ("股息率", "percent"),
    "roe": ("ROE", "percent"),
    "roe_recurring": ("扣非 ROE", "percent"),
    "non_recurring_share": ("非经常性损益占归母净利润", "percent"),
    "roa": ("ROA", "percent"),
    "roic": ("ROIC", "percent"),
    "gross_margin": ("毛利率", "percent"),
    "operating_margin": ("营业利润率", "percent"),
    "net_margin": ("净利率", "percent"),
    "revenue_growth": ("营业收入同比增速", "percent"),
    "net_profit_growth": ("净利润同比增速", "percent"),
    "recurring_profit_growth": ("扣非净利润同比增速", "percent"),
    "operating_cash_flow_growth": ("经营现金流同比增速", "percent"),
    "free_cash_flow_growth": ("自由现金流同比增速", "percent"),
    "debt_to_assets": ("资产负债率", "percent"),
    "net_debt": ("净负债", "amount"),
    "interest_coverage": ("利息保障倍数", "multiple"),
    "capital_adequacy": ("资本充足率", "percent"),
    "net_interest_margin": ("净息差", "percent"),
    "non_performing_loan_ratio": ("不良贷款率", "percent"),
    "annualized_return": ("区间年化收益率", "percent"),
    "annualized_volatility": ("区间年化波动率", "percent"),
    "max_drawdown": ("区间最大回撤", "percent"),
    "excess_total_return": ("相对基准超额收益", "percent"),
    "tracking_error": ("年化跟踪误差", "percent"),
    "premium_discount": ("折溢价率", "percent"),
    "expense_ratio": ("年综合费率", "percent"),
    "momentum_5": ("5日涨跌幅", "percent"),
    "return_1d": ("最近一日涨跌幅", "percent"),
    "commodity_sensitivity": ("商品价格敏感度", "number"),
    "rd_intensity": ("研发费用率", "percent"),
    "cash_burn": ("年度现金消耗（负值为净流入）", "amount"),
    "cash_burn_ratio": ("年度现金消耗占货币资金", "percent"),
    "cash_runway_years": ("现金跑道", "years"),
    "financing_dependence": ("筹资现金占现金消耗", "percent"),
}
_VALUATION_METRICS = (
    "market_cap",
    "enterprise_value",
    "pe_ttm",
    "pe_ttm_recurring",
    "pb",
    "ps_ttm",
    "pcf_ttm",
    "ev_ebitda",
    "earnings_yield",
    "fcf_yield",
    "dividend_yield",
)
_PROFITABILITY_METRICS = (
    "roe",
    "roe_recurring",
    "non_recurring_share",
    "roa",
    "roic",
    "gross_margin",
    "operating_margin",
    "net_margin",
)
_GROWTH_METRICS = (
    "revenue_growth",
    "net_profit_growth",
    "recurring_profit_growth",
    "operating_cash_flow_growth",
    "free_cash_flow_growth",
)
_BALANCE_METRICS = (
    "debt_to_assets",
    "net_debt",
    "interest_coverage",
)
_BANK_METRICS = (
    "pb",
    "roe",
    "capital_adequacy",
    "net_interest_margin",
    "non_performing_loan_ratio",
)
_CASH_METRICS = (
    "rd_intensity",
    "cash_burn",
    "cash_burn_ratio",
    "cash_runway_years",
    "financing_dependence",
)
_SPECIAL_FACTOR_LIMITATIONS = {
    "premium_discount": "按行情快照的最新价与 IOPV 计算，是单一时点值。",
    "expense_ratio": "按官方费率表汇总的年综合费率，不含交易佣金。",
    "commodity_sensitivity": (
        "按共同交易日收益回归得到的历史联动系数，"
        "不代表因果，也不预测商品价格。"
    ),
}
_SECURITY_BENCHMARK_FACTS = (
    ("asset_total_return", "区间累计收益率（标的）", "percent"),
    ("benchmark_total_return", "区间累计收益率（基准）", "percent"),
    ("excess_total_return", "相对基准超额收益", "percent"),
    ("tracking_error", "年化主动风险（跟踪误差）", "percent"),
    ("information_ratio", "信息比率", "number"),
    ("beta", "Beta", "number"),
    ("correlation", "与基准相关系数", "number"),
    ("common_observation_count", "共同交易日数", "count"),
)
_EVIDENCE_TYPE_LABELS = {
    "financial_statement": "财务报表",
    "disclosure": "公告",
    "business_model_evidence": "商业模式证据",
    "management_evidence": "管理层证据",
    "research_event": "研究事件",
    "research_report": "第三方研报",
}
_FLOW_BASIS_LABELS = {
    "ttm": "滚动十二个月（TTM）口径",
    "annual": "年度口径",
    "quarterly": "单季口径",
}
_OPERATOR_SYMBOLS = {
    ConditionOperator.GT: ">",
    ConditionOperator.GTE: "≥",
    ConditionOperator.LT: "<",
    ConditionOperator.LTE: "≤",
    ConditionOperator.EQ: "=",
}


def _metric_label(name: str) -> str:
    return _METRIC_DISPLAY.get(name, ("未命名指标", "number"))[0]


def _format_metric(name: str, value) -> str:
    return _format_value(
        _METRIC_DISPLAY.get(name, ("", "number"))[1],
        value,
    )


def _format_value(kind: str, value) -> str:
    number = float(value)
    if kind == "percent":
        return f"{_number(number * 100, digits=2)}%"
    if kind == "multiple":
        return f"{_number(number, digits=2)}倍"
    if kind == "amount":
        return f"{_number(number / 100_000_000, digits=2)}亿元"
    if kind == "count":
        return _number(number, digits=0)
    if kind == "years":
        return f"{_number(number, digits=1)}年"
    return _number(number, digits=3)


def _condition_leaves(node) -> tuple[MetricCondition, ...]:
    if isinstance(node, MetricCondition):
        return (node,)
    if isinstance(node, ConditionGroup):
        return tuple(
            leaf
            for child in node.children
            for leaf in _condition_leaves(child)
        )
    return ()


def _condition_text(condition: MetricCondition) -> str:
    label = _metric_label(condition.metric_name)
    lower = _format_metric(condition.metric_name, condition.threshold)
    if (
        condition.operator is ConditionOperator.BETWEEN
        and condition.upper_threshold is not None
    ):
        upper = _format_metric(
            condition.metric_name,
            condition.upper_threshold,
        )
        return f"{label}介于{lower}至{upper}"
    symbol = _OPERATOR_SYMBOLS.get(condition.operator, "=")
    return f"{label} {symbol} {lower}"


def _security_fact_sections(
    execution: ResearchExecutionResult,
    *,
    rule: _RuleView | None,
    benchmark_name: str | None,
    broad_fallback: bool,
) -> tuple[ResearchFactSection, ...]:
    sections: list[ResearchFactSection] = []

    def add(section_id: str, title: str, facts) -> None:
        if facts:
            sections.append(
                ResearchFactSection(
                    section_id=section_id,
                    title=title,
                    facts=tuple(facts),
                )
            )

    add("market_data", "行情与覆盖", _price_facts(execution))
    add("risk_metrics", "收益与风险指标", _risk_facts(execution))
    add(
        "benchmark_metrics",
        "相对基准表现",
        _security_benchmark_facts(
            execution,
            benchmark_name=benchmark_name,
            broad_fallback=broad_fallback,
        ),
    )
    add("industry_benchmark", "相对行业表现", _industry_benchmark_facts(execution))
    position = (
        execution.industry_context.valuation_position
        if execution.industry_context is not None
        else None
    )
    fundamental = execution.fundamental_result
    if fundamental is not None:
        snapshot = fundamental.snapshot
        basis = _FLOW_BASIS_LABELS.get(snapshot.flow_basis, "报告期口径")
        price_facts = _price_facts(execution)
        price_date = (
            price_facts[0].as_of_date
            if price_facts
            else snapshot.as_of_date
        )
        add(
            "valuation",
            "估值",
            _fundamental_facts(
                fundamental.metrics,
                _VALUATION_METRICS,
                (
                    f"基于{snapshot.report_period}报告期财报与"
                    f"截至{price_date}的最新收盘价，流量项为{basis}。",
                    "历史与同行分位见“估值位置”一节。"
                    if position is not None
                    else "仅当前一个时点，尚无历史估值分位；"
                    "未与同行比较，需要时在探索阶段查询同行。",
                ),
            ),
        )
        add(
            "profitability",
            "盈利能力",
            _fundamental_facts(
                fundamental.metrics,
                _PROFITABILITY_METRICS,
                (
                    f"基于{snapshot.report_period}报告期财报，"
                    f"流量项为{basis}。",
                    "多期趋势见“成长趋势”一节。"
                    if execution.growth_trend
                    else "仅当前一期，未形成多期趋势。",
                ),
            ),
        )
        add(
            "growth",
            "成长",
            [
                *_fundamental_facts(
                    fundamental.metrics,
                    _GROWTH_METRICS,
                    (
                        f"{snapshot.report_period}报告期相对上年同期计算，"
                        f"流量项为{basis}。",
                    ),
                ),
                *_ytd_growth_facts(snapshot),
            ],
        )
        add(
            "balance_sheet",
            "资产负债",
            _fundamental_facts(
                fundamental.metrics,
                _BALANCE_METRICS,
                (f"基于{snapshot.report_period}报告期末资产负债表。",),
            ),
        )
        add(
            "cash_and_rd",
            "研发与现金消耗" if execution.task.profile_id == PROFILE_HIGH_GROWTH_UNPROFITABLE else "研发投入",
            _fundamental_facts(
                fundamental.metrics,
                _CASH_METRICS if execution.task.profile_id == PROFILE_HIGH_GROWTH_UNPROFITABLE else ("rd_intensity",),
                (
                    f"基于{snapshot.report_period}报告期，流量项为{basis}。",
                    "现金消耗 = −（经营现金流 − 购建长期资产支出）；现金跑道 = 货币资金 ÷ 现金消耗。"
                    "货币资金不含理财类金融资产（偏保守），也没有扣除有息负债的到期偿还（偏乐观），借款多的公司要结合负债期限看。"
                    if execution.task.profile_id == PROFILE_HIGH_GROWTH_UNPROFITABLE
                    else "研发费用率 = 费用化研发支出 ÷ 营业收入，不含资本化部分。",
                ),
            ),
        )
    add("growth_trend", "成长趋势", _growth_trend_facts(execution))
    add("dividends", "分红", _dividend_facts(execution))
    add(
        "bank_metrics",
        "银行经营指标",
        _fundamental_facts(
            execution.profile_metrics,
            _BANK_METRICS,
            (
                f"基于{execution.profile_metrics[0].report_period}报告期披露数据；"
                "ROE 为年初至今的披露值按 12 ÷ 月数年化，四季度常低于前三季度的年化值。",
            )
            if execution.profile_metrics
            else (),
        ),
    )
    add("valuation_position", "估值位置", _valuation_position_facts(position))
    add("implied_expectations", "隐含预期与内在价值", _valuation_model_facts(execution.valuation_model))
    add("special_metrics", "专项指标", _special_factor_facts(execution))
    if rule is not None:
        add(
            "research_rule",
            "研究规则检查",
            _rule_facts(rule, _security_metric_values(execution)),
        )
    add("evidence_overview", "公告与研报", _evidence_facts(execution))
    return tuple(sections)


def _price_facts(
    execution: ResearchExecutionResult,
) -> list[ResearchFact]:
    response = execution.bar_response
    if response is None:
        return []
    cutoff = execution.task.as_of_date
    dated = []
    for record in response.records:
        values = dict(record)
        record_date = _record_date(values)
        if record_date is not None and record_date <= cutoff:
            dated.append((values, record_date))
    if not dated:
        return []
    dated.sort(key=lambda item: item[1])
    latest, latest_date = dated[-1]
    source = _record_source(latest, response.dataset)
    facts = []
    close = _first_value(latest, "close")
    if close is not None:
        facts.append(
            ResearchFact(
                label="最新收盘价",
                value=_number(close, digits=3),
                unit="元",
                as_of_date=latest_date,
                kind=ResearchFactKind.REPORTED,
                evidence_refs=(source,),
                limitations=("未复权价格。",),
            )
        )
    facts.append(
        ResearchFact(
            label="行情覆盖",
            value=(
                f"{len(dated)}个交易日，"
                f"{dated[0][1]}至{latest_date}"
            ),
            as_of_date=latest_date,
            kind=ResearchFactKind.REPORTED,
            evidence_refs=(source,),
        )
    )
    return facts


def _security_benchmark_facts(
    execution: ResearchExecutionResult,
    *,
    benchmark_name: str | None,
    broad_fallback: bool,
) -> list[ResearchFact]:
    result = execution.benchmark_result
    if result is None or not result.metrics:
        return []
    by_name = {item.factor_name: item for item in result.metrics}
    first = result.metrics[0]
    limitations = (
        _benchmark_basis_limitation(first.source_dataset)
        + f"实际区间为{first.input_start_date}至"
        f"{first.input_end_date}。",
    )
    if broad_fallback:
        limitations += (
            "该画像缺少专用行业或风格基准，暂以沪深300"
            "作为宽基参照；超额收益不代表行业内相对强弱。",
        )
    facts = []
    if benchmark_name:
        facts.append(
            ResearchFact(
                label="比较基准",
                value=benchmark_name,
                as_of_date=first.as_of_date,
                kind=ResearchFactKind.REPORTED,
                evidence_refs=("master-data:benchmark",),
            )
        )
    for factor_name, label, kind in _SECURITY_BENCHMARK_FACTS:
        metric = by_name.get(factor_name)
        if metric is None:
            continue
        facts.append(
            ResearchFact(
                label=label,
                value=_format_value(kind, metric.value),
                unit="个" if kind == "count" else None,
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(
                    f"derived:{metric.source_dataset}:{factor_name}",
                ),
                limitations=limitations,
            )
        )
    return facts


def _industry_benchmark_facts(
    execution: ResearchExecutionResult,
) -> list[ResearchFact]:
    context = execution.industry_context
    result = context.industry_benchmark if context is not None else None
    if result is None or not result.metrics:
        return []
    by_name = {item.factor_name: item for item in result.metrics}
    first = result.metrics[0]
    limitations = (
        f"申万行业指数为价格指数，不含分红；实际区间为{first.input_start_date}至"
        f"{first.input_end_date}。行业按申万当前成分归属。",
    )
    facts = [
        ResearchFact(
            label="行业基准",
            value=context.industry_benchmark_name or "申万一级行业指数",
            as_of_date=first.as_of_date,
            kind=ResearchFactKind.REPORTED,
            evidence_refs=("shenwan_2021:industry_index",),
        )
    ]
    for factor_name, label in (
        ("benchmark_total_return", "区间累计收益率（行业指数）"),
        ("excess_total_return", "相对行业超额收益"),
        ("correlation", "与行业指数相关系数"),
    ):
        metric = by_name.get(factor_name)
        if metric is None:
            continue
        facts.append(
            ResearchFact(
                label=label,
                value=_format_value(
                    "number" if factor_name == "correlation" else "percent",
                    metric.value,
                ),
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(f"derived:{metric.source_dataset}:industry:{factor_name}",),
                limitations=limitations,
            )
        )
    return facts


_POSITION_METRICS = (("pe_ttm", "市盈率(TTM)", "倍"), ("pb", "市净率", "倍"), ("ps", "市销率", "倍"))


def _valuation_position_facts(position: dict | None) -> list[ResearchFact]:
    if position is None:
        return []
    facts = []
    limitation = (position.get("note") or "") + "。"
    peers = position.get("peers") or {}
    for metric, label, unit in _POSITION_METRICS:
        own = position["own_history"].get(metric) or {}
        if own.get("percentile") is not None:
            facts.append(
                ResearchFact(
                    label=f"{label}自身历史分位",
                    value=(
                        f"{own['percentile']:.0%}（当前{own['value']:.2f}{unit}；"
                        f"{own['window_start']}至{own['window_end']} 共{own['sample_count']}个样本，"
                        f"中位数{own['median']:.2f}{unit}）"
                    ),
                    as_of_date=own.get("current_date") or position["as_of_date"],
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=("akshare:stock_value_em",),
                    limitations=(limitation,),
                )
            )
        elif own.get("current") is not None and float(own["current"]) <= 0:
            facts.append(
                ResearchFact(
                    label=f"{label}自身历史分位",
                    value="当前为负（亏损），不给分位",
                    as_of_date=own.get("current_date") or position["as_of_date"],
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=("akshare:stock_value_em",),
                    limitations=(limitation,),
                )
            )
        peer = (peers.get("metrics") or {}).get(metric) or {}
        if peer.get("percentile") is not None:
            industry = peers.get("industry") or {}
            facts.append(
                ResearchFact(
                    label=f"{label}同行分位",
                    value=(
                        f"{peer['percentile']:.0%}（申万{industry.get('name', '')} "
                        f"{peer['peer_count']}家有效样本，同行中位数{peer['median']:.2f}{unit}）"
                    ),
                    as_of_date=position["as_of_date"],
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=("akshare:stock_value_em", "shenwan_2021:members"),
                    limitations=(limitation,),
                )
            )
    return facts


def _valuation_model_facts(model: dict | None) -> list[ResearchFact]:
    if model is None:
        return []
    limitations = tuple(model["notes"]) + tuple(model["gaps"])
    return [
        ResearchFact(
            label=name,
            value=text,
            as_of_date=model["as_of_date"],
            kind=ResearchFactKind.DERIVED,
            evidence_refs=(f"valuation_model:{model['method']}:{model['report_period']}", "macro:cgb_10y"),
            limitations=limitations,
        )
        for name, text in valuation_model_lines(model)
    ]


def _ytd_growth_facts(snapshot) -> list[ResearchFact]:
    """Year-to-date change, unsmoothed: TTM growth lags turning points by up to three quarters."""

    if snapshot.flow_basis != "ttm":
        return []
    period = snapshot.report_period
    prior = f"{int(period[:4]) - 1}{period[4:]}"
    parts = []
    for field, label in (("revenue", "营业收入"), ("earnings", "归母净利润"), ("recurring_earnings", "扣非归母净利润")):
        items = snapshot.evidence.get(field, ())
        current = next((item.value for item in items if item.report_period == period), None)
        base = next((item.value for item in items if item.report_period == prior), None)
        if current is not None and base:
            change = current / base - 1 if base > 0 else None
            parts.append(
                f"{label} {change:+.2%}" if change is not None else f"{label} 上年同期为负，不计算同比"
            )
    if not parts:
        return []
    return [
        ResearchFact(
            label=f"年初至{period}累计同比（不滚动）",
            value="；".join(parts),
            as_of_date=snapshot.as_of_date,
            kind=ResearchFactKind.DERIVED,
            evidence_refs=(f"derived:ytd_growth:{snapshot.asset_id}:{period}",),
            limitations=("TTM 增速反映过去四个季度，拐点会滞后；两者方向不同时，本行更接近最新趋势。",),
        )
    ]


def _dividend_facts(execution: ResearchExecutionResult) -> list[ResearchFact]:
    data = execution.dividends
    if not data:
        return []
    as_of = execution.task.as_of_date
    ref = (f"derived:dividend_history:{execution.task.asset_id}:{as_of}",)
    events = "；".join(
        f"除息日 {item['ex_date']} 每股 {float(item['cash_per_share']):.3f} 元" for item in data["ttm_events"]
    ) or "近 12 个月没有除息"
    ttm = f"近 12 个月每股现金分红 {float(data['ttm_per_share']):.3f} 元（{events}）"
    if data["ttm_yield"] is not None:
        ttm += f"，按现价 {float(data['price']):.2f} 元股息率 {float(data['ttm_yield']):.2%}"
    facts = [
        ResearchFact(
            label="近 12 个月分红",
            value=ttm,
            as_of_date=as_of,
            kind=ResearchFactKind.DERIVED,
            evidence_refs=ref,
            limitations=(
                data["source"],
                "只算已实施的现金分红（含税），已公告未除息的方案不计入；未按送转股调整。",
            ),
        )
    ]
    if data["by_year"]:
        facts.append(
            ResearchFact(
                label="按除息年度的每股分红",
                value="；".join(f"{year} 年 {float(value):.3f} 元" for year, value in data["by_year"].items()),
                as_of_date=as_of,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=ref,
                limitations=("按除息日所在年度归属，不等于按会计年度的分配方案。",),
            )
        )
    return facts


def _growth_trend_facts(execution: ResearchExecutionResult) -> list[ResearchFact]:
    trend = execution.growth_trend
    if not trend or not trend["periods"]:
        return []
    facts = []
    for metric in trend["metrics"]:
        points = [
            f"{item['period_end']} {_format_metric(metric, item[metric])}"
            for item in trend["periods"]
            if item.get(metric) is not None
        ]
        if not points:
            continue
        facts.append(
            ResearchFact(
                label=f"{_metric_label(metric)}（按报告期）",
                value="；".join(points),
                as_of_date=execution.task.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=(f"derived:metric_history:{metric}",),
                limitations=(trend["note"],),
            )
        )
    return facts


def _fundamental_facts(
    metrics,
    names: tuple[str, ...],
    limitations: tuple[str, ...],
) -> list[ResearchFact]:
    by_name = {}
    for item in metrics:
        if (
            item.status is MetricStatus.AVAILABLE
            and item.value is not None
        ):
            by_name.setdefault(item.metric_name, item)
    facts = []
    for name in names:
        metric = by_name.get(name)
        if metric is None:
            continue
        refs = tuple(
            sorted(
                {
                    entry.source
                    for entry in metric.inputs
                    if entry.source
                }
            )
        ) or (f"derived:fundamental:{name}",)
        facts.append(
            ResearchFact(
                label=_metric_label(name),
                value=_format_metric(name, metric.value),
                as_of_date=metric.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=refs,
                limitations=limitations
                or (f"基于{metric.report_period}报告期披露数据。",),
            )
        )
    return facts


def _special_factor_facts(
    execution: ResearchExecutionResult,
) -> list[ResearchFact]:
    cutoff = execution.task.as_of_date
    latest = {}
    for item in execution.factor_values:
        if (
            item.factor_name not in _SPECIAL_FACTOR_LIMITATIONS
            or item.as_of_date > cutoff
        ):
            continue
        current = latest.get(item.factor_name)
        if current is None or item.as_of_date >= current.as_of_date:
            latest[item.factor_name] = item
    return [
        ResearchFact(
            label=_metric_label(name),
            value=_format_metric(name, latest[name].value),
            as_of_date=latest[name].as_of_date,
            kind=ResearchFactKind.DERIVED,
            evidence_refs=(
                f"derived:{latest[name].source_dataset}:{name}",
            ),
            limitations=(limitation,),
        )
        for name, limitation in _SPECIAL_FACTOR_LIMITATIONS.items()
        if name in latest
    ]


def _security_metric_values(
    execution: ResearchExecutionResult,
) -> dict[str, float]:
    cutoff = execution.task.as_of_date
    values: dict[str, float] = {}
    for item in sorted(
        execution.factor_values,
        key=lambda value: value.as_of_date,
    ):
        if item.as_of_date <= cutoff:
            values[item.factor_name] = float(item.value)
    for result in (execution.risk_result, execution.benchmark_result):
        if result is None:
            continue
        for item in result.metrics:
            values[item.factor_name] = float(item.value)
    fundamentals = (
        *(
            execution.fundamental_result.metrics
            if execution.fundamental_result is not None
            else ()
        ),
        *execution.profile_metrics,
    )
    for item in fundamentals:
        if (
            item.status is MetricStatus.AVAILABLE
            and item.value is not None
        ):
            values[item.metric_name] = float(item.value)
    return values


def _rule_facts(
    rule: _RuleView,
    values: dict[str, float],
) -> list[ResearchFact]:
    result = rule.result
    reference = (rule.reference,)
    details = [rule.name]
    if result.score is not None and rule.spec is not None:
        details.append(
            f"得分{_number(result.score, digits=2)}，"
            f"通过线{_number(rule.spec.passing_score, digits=2)}"
        )
    summary = f"{rule.status_label}（{'；'.join(details)}）"
    facts = [
        ResearchFact(
            label="规则结论",
            value=summary,
            as_of_date=result.as_of_date,
            kind=ResearchFactKind.DERIVED,
            evidence_refs=reference,
            limitations=(
                *result.limitations,
                f"规则结果有效期至{result.valid_until}。",
            ),
        )
    ]
    spec = rule.spec
    if spec is not None:
        matched = set(result.matched_conditions)
        failed = set(result.failed_conditions)
        vetoed = set(result.vetoed_conditions)
        insufficient = (
            result.status is StrategyRunStatus.INSUFFICIENT_DATA
        )

        def line(condition, *, veto, weight=None) -> str:
            current = values.get(condition.metric_name)
            condition_id = condition.condition_id
            if condition_id in vetoed:
                state = "已触发否决"
            elif condition_id in matched:
                state = "满足"
            elif condition_id in failed:
                state = "未满足"
            elif current is None:
                state = "缺少数据"
            elif veto and not insufficient:
                state = "未触发"
            else:
                state = "未评估"
            current_text = (
                _format_metric(condition.metric_name, current)
                if current is not None
                else "缺失"
            )
            weight_text = (
                f"，权重{_number(weight * 100, digits=0)}%"
                if weight is not None
                else ""
            )
            return (
                f"{_condition_text(condition)}"
                f"（当前{current_text}，{state}{weight_text}）"
            )

        groups = (
            (
                "硬性门槛",
                [
                    line(leaf, veto=False)
                    for node in spec.hard_filters
                    for leaf in _condition_leaves(node)
                ],
            ),
            (
                "否决条件",
                [
                    line(leaf, veto=True)
                    for node in spec.vetoes
                    for leaf in _condition_leaves(node)
                ],
            ),
            (
                "评分条件",
                [
                    line(leaf, veto=False, weight=item.weight)
                    for item in spec.score_rules
                    for leaf in _condition_leaves(item.condition)
                ],
            ),
        )
        for label, lines in groups:
            if not lines:
                continue
            facts.append(
                ResearchFact(
                    label=label,
                    value="；".join(lines),
                    as_of_date=result.as_of_date,
                    kind=ResearchFactKind.DERIVED,
                    evidence_refs=reference,
                    limitations=(
                        "阈值为研究假设，未经回测或同行标准化。",
                    ),
                )
            )
    missing = [
        *(_metric_label(name) for name in result.missing_metrics),
        *(
            _EVIDENCE_TYPE_LABELS.get(name, "其他证据")
            for name in result.missing_evidence_types
        ),
    ]
    if missing:
        facts.append(
            ResearchFact(
                label="缺失输入",
                value="、".join(dict.fromkeys(missing)),
                as_of_date=result.as_of_date,
                kind=ResearchFactKind.DERIVED,
                evidence_refs=reference,
                limitations=(
                    "缺失输入不按零值处理；输入不完整时规则无法给出"
                    "完整结论。",
                ),
            )
        )
    return facts


def _evidence_facts(
    execution: ResearchExecutionResult,
) -> list[ResearchFact]:
    bundle = execution.evidence_bundle
    if bundle is None:
        return []
    cutoff = execution.task.as_of_date
    items = []
    for item in bundle.evidence:
        event_date = str(item.event_date)[:10]
        try:
            date.fromisoformat(event_date)
        except ValueError:
            continue
        if event_date <= cutoff:
            items.append((event_date, item))
    items.sort(key=lambda entry: entry[0], reverse=True)
    official = [
        entry
        for entry in items
        if entry[1].tier is EvidenceTier.OFFICIAL
        and entry[1].evidence_type == "disclosure"
    ]
    reports = [
        entry
        for entry in items
        if entry[1].tier is EvidenceTier.THIRD_PARTY
    ]
    facts = [
        ResearchFact(
            label="证据覆盖",
            value=(
                f"官方公告{len(official)}条，第三方研报{len(reports)}条，"
                f"结构化事件{len(bundle.events)}个"
                f"（{bundle.start_date}至{bundle.end_date}）"
            ),
            as_of_date=cutoff,
            kind=ResearchFactKind.REPORTED,
            evidence_refs=tuple(bundle.sources) or ("research-evidence",),
        )
    ]
    for event_date, item in official[:5]:
        url = str(item.source_url or "")
        facts.append(
            ResearchFact(
                label="公告",
                value=item.title,
                as_of_date=event_date,
                kind=(
                    ResearchFactKind.OFFICIAL
                    if url.lower().startswith(("http://", "https://"))
                    else ResearchFactKind.REPORTED
                ),
                evidence_refs=(url or item.evidence_id,),
            )
        )
    for event_date, item in reports[:3]:
        details = [
            str(item.metadata.get(key)).strip()
            for key in ("institution", "rating")
            if item.metadata.get(key) not in (None, "")
        ]
        facts.append(
            ResearchFact(
                label="研报",
                value=(
                    f"{item.title}（{'，'.join(details)}）"
                    if details
                    else item.title
                ),
                as_of_date=event_date,
                kind=ResearchFactKind.REPORTED,
                evidence_refs=(item.source_url or item.evidence_id,),
                limitations=("第三方观点，不代表官方披露。",),
            )
        )
    return facts


def _fund_record_facts(
    execution: ResearchExecutionResult,
) -> tuple[ResearchFact, ...]:
    bundle = execution.fund_bundle
    assert bundle is not None
    facts = []
    status = _latest_record(
        bundle.status_records,
        execution.task.as_of_date,
    )
    if status is not None:
        record, record_date = status
        values = [
            str(record[key])
            for key in ("purchase_status", "redemption_status")
            if record.get(key) not in (None, "")
        ]
        if values:
            facts.append(
                ResearchFact(
                    label="申购赎回状态",
                    value="；".join(values),
                    as_of_date=record_date,
                    kind=ResearchFactKind.REPORTED,
                    evidence_refs=(
                        _record_source(record, "fund_status"),
                    ),
                )
            )
    scale = _latest_record(
        bundle.scale_records,
        execution.task.as_of_date,
    )
    if scale is not None:
        record, record_date = scale
        scale_value = _first_value(record, "item_value", "scale")
        if scale_value is not None:
            item_name = str(record.get("item_name") or "").strip()
            is_share_count = (
                record.get("report_type") == "fund_scale_open_sina"
            )
            value = _number(
                (
                    float(scale_value) / 100_000_000
                    if is_share_count
                    else scale_value
                ),
                digits=2,
            )
            if item_name:
                value = f"{item_name}：{value}"
            facts.append(
                ResearchFact(
                    label=(
                        "最新份额记录"
                        if is_share_count
                        else "最新规模记录"
                    ),
                    value=value,
                    unit="亿份" if is_share_count else None,
                    as_of_date=record_date,
                    kind=ResearchFactKind.REPORTED,
                    evidence_refs=(
                        _record_source(record, "fund_scale"),
                    ),
                    limitations=(
                        ("上游字段为最近总份额，按1亿份换算。",)
                        if is_share_count
                        else ("沿用上游记录单位",)
                    ),
                )
            )
    allocation = _allocation_fact(
        bundle.allocation_records,
        execution.task.as_of_date,
    )
    if allocation is not None:
        facts.append(allocation)
    return tuple(facts)


def _allocation_fact(
    records: tuple[dict[str, Any], ...],
    fallback_date: str,
) -> ResearchFact | None:
    if not records:
        return None
    latest_date = max(
        (_record_date(record) or fallback_date)
        for record in records
    )
    latest = [
        record
        for record in records
        if (_record_date(record) or fallback_date) == latest_date
    ]
    items = []
    for record in latest:
        name = str(record.get("item_name") or "").strip()
        ratio = _first_value(record, "ratio")
        if name and ratio is not None:
            items.append((float(ratio), f"{name} {_number(ratio, 2)}%"))
    if not items:
        stock_ratio = _first_value(latest[0], "stock_ratio")
        if stock_ratio is not None:
            value = float(stock_ratio)
            if abs(value) <= 1:
                value *= 100
            items.append((value, f"股票 {_number(value, 2)}%"))
    if items:
        items.sort(key=lambda item: item[0], reverse=True)
        value = "；".join(item[1] for item in items[:5])
    else:
        value = f"{len(latest)}条配置记录"
    return ResearchFact(
        label="最新配置摘要",
        value=value,
        as_of_date=latest_date,
        kind=ResearchFactKind.REPORTED,
        evidence_refs=tuple(
            sorted(
                {
                    _record_source(record, "fund_allocation")
                    for record in latest
                }
            )
        ),
    )


def _latest_record(
    records: tuple[dict[str, Any], ...],
    fallback_date: str,
) -> tuple[dict[str, Any], str] | None:
    if not records:
        return None
    return max(
        (
            (record, _record_date(record) or fallback_date)
            for record in records
        ),
        key=lambda item: item[1],
    )


def _record_date(record: dict[str, Any]) -> str | None:
    for key in (
        "nav_date",
        "trade_date",
        "report_date",
        "observed_at",
        "date",
        "update_date",
    ):
        value = record.get(key)
        if value in (None, ""):
            continue
        candidate = str(value)[:10]
        try:
            date.fromisoformat(candidate)
        except ValueError:
            continue
        return candidate
    return None


def _record_source(record: dict[str, Any], fallback: str) -> str:
    value = str(record.get("source") or fallback).strip()
    return value or fallback


def _first_value(record: dict[str, Any], *keys: str):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _number(value: float, digits: int) -> str:
    formatted = f"{float(value):,.{digits}f}"
    if digits:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted
