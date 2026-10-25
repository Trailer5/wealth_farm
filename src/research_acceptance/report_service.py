"""Map formal execution outputs into structured research reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from src.fund_research import DisclosureExtractionStatus
from src.master_data import ASSET_TYPE_OPEN_FUND, MasterDataService
from src.peer_analysis import (
    PeerComparisonBundle,
    PeerComparisonStatus,
)
from src.research_workflow.execution_models import (
    ResearchExecutionResult,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)
from src.research_workflow.models import ResearchQuestionType

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
    ) -> None:
        self.master_data = master_data
        self.assembler = (
            assembler or RepresentativeResearchAssembler()
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
                        message=(
                            "Execution produced no strategy result"
                        ),
                        blocking=True,
                    )
                )
        understanding_without_strategy = (
            strategy is None
            and execution.task.question_type
            is ResearchQuestionType.UNDERSTANDING
        )
        scenarios = _scenarios(
            strategy,
            not_applicable=understanding_without_strategy,
        )
        counterargument = _counterargument(
            strategy,
            not_applicable=understanding_without_strategy,
        )
        fact_sections = _fact_sections(execution)
        _append_disclosure_gaps(execution, gaps)
        _append_open_fund_fact_matrix_gaps(
            execution,
            fact_sections,
            gaps,
        )
        invalidation_condition = (
            "A hard filter or veto changes on refreshed inputs"
            if strategy
            else (
                "刷新后的净值、规模、配置或申赎状态"
                "发生重大变化"
            )
        )
        invalidation_evidence = (
            f"Rerun strategy {strategy.strategy_id}"
            if strategy
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
            conclusions=_conclusions(execution, strategy),
            gaps=tuple(gaps),
            fact_sections=fact_sections,
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
                message=f"Unavailable peer metrics: {unavailable}",
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


def _scenarios(strategy, *, not_applicable=False):
    if strategy is None:
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
            text="Scenario unavailable without strategy result.",
        )
        return tuple(
            ResearchScenario(kind=kind, claims=(claim,))
            for kind in ScenarioKind
        )
    reference = (
        f"strategy:{strategy.strategy_id}:{strategy.status.value}"
    )
    return (
        ResearchScenario(
            ScenarioKind.OPTIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    "Matched strategy conditions persist.",
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.BASE,
            (
                ResearchClaim(
                    ClaimKind.FACT,
                    f"Current strategy status is "
                    f"{strategy.status.value}.",
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.PESSIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    "Failed conditions, vetoes, or documented "
                    "limitations become material.",
                    (reference,),
                ),
            ),
        ),
    )


def _counterargument(strategy, *, not_applicable=False):
    if strategy is None:
        if not_applicable:
            return ResearchClaim(
                ClaimKind.NOT_APPLICABLE,
                "理解型基础研究不自动生成投资反方。",
            )
        return ResearchClaim(
            ClaimKind.UNKNOWN,
            "Strongest counterargument is unknown without strategy.",
        )
    evidence = (
        *strategy.counterevidence,
        *strategy.failed_conditions,
        *strategy.vetoed_conditions,
    )
    if evidence:
        text = f"Strategy counterevidence: {list(evidence)}"
    elif strategy.limitations:
        text = f"Key strategy limitation: {strategy.limitations[0]}"
    else:
        text = (
            "No machine counterevidence was observed; "
            "the result remains a research rule."
        )
    return ResearchClaim(
        ClaimKind.INFERENCE,
        text,
        (f"strategy:{strategy.strategy_id}",),
    )


def _conclusions(execution, strategy):
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
    return (
        ResearchClaim(
            kind=ClaimKind.FACT,
            text=(
                "Formal research execution emitted all "
                "required phase records."
            ),
            evidence_refs=(
                f"task:{execution.task.task_id}",
                "execution:phases",
            ),
        ),
        ResearchClaim(
            kind=ClaimKind.INFERENCE,
            text=(
                (
                    "The strategy result is a research rule "
                    "outcome, not an investment instruction."
                )
                if strategy
                else (
                    "This baseline describes available fund "
                    "data and is not an investment instruction."
                )
            ),
            evidence_refs=(
                (
                    f"strategy:{strategy.strategy_id}"
                    if strategy
                    else "execution:fund-data"
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
        limitations = (
            (
                "按执行窗口内的日频单位净值计算；"
                "若发生现金分红，除息会影响该序列，"
                "不能解释为分红再投资总回报。",
            )
            if metric.source_dataset == "open_fund_nav"
            else ("按执行窗口内的日频正式行情计算",)
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
        if result.nav_basis == "accumulated_nav":
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
