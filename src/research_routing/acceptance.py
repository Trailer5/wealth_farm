"""vision_q_v1: the ten representative questions frozen as replayable scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.research_contracts import (
    Personalization,
    ResearchDepth,
    ResearchHorizon,
    ResearchIntent,
    ResearchRequest,
    SubjectRef,
    SubjectType,
    to_payload,
)

from .executor import RecipeRunResult, StepOutcomeStatus
from .models import RoutingResult, RoutingStatus, StepStatus

VISION_SCENARIO_VERSION = "vision_q_v1"
SCENARIO_AS_OF = "2026-10-23"
A, P, X = StepStatus.AVAILABLE, StepStatus.PARTIAL, StepStatus.PLANNED


@dataclass(frozen=True)
class VisionQuestionScenario:
    """Expected statuses describe current capability; update them as stages land."""

    question_id: str
    question_text: str
    request: ResearchRequest
    recipe_id: str
    status: RoutingStatus
    element_status: tuple[tuple[str, StepStatus], ...]
    structural_checks: tuple[str, ...]

    @property
    def scenario_id(self) -> str:
        return f"{VISION_SCENARIO_VERSION}:{self.question_id}"


@dataclass(frozen=True)
class CoverageRow:
    question_id: str
    element_id: str
    element_text: str
    status: StepStatus
    system_modules: tuple[str, ...]
    roadmap_stages: tuple[int, ...]


@dataclass(frozen=True)
class VisionScenarioOutcome:
    scenario_id: str
    passed: bool
    findings: tuple[str, ...]
    coverage: tuple[CoverageRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)


def _request(question: str, **fields: Any) -> ResearchRequest:
    return ResearchRequest(
        request_id=question.lower(),
        as_of_date=SCENARIO_AS_OF,
        **fields,
    )


def vision_question_scenarios() -> tuple[VisionQuestionScenario, ...]:
    return (
        VisionQuestionScenario(
            "Q1",
            "整体市场行情怎么样？",
            _request(
                "Q1",
                subject_type=SubjectType.MARKET,
                subject_refs=(SubjectRef("A股"),),
                intents=(ResearchIntent.UNDERSTAND,),
                personalization=Personalization.OPTIONAL,
                depth=ResearchDepth.QUICK,
            ),
            "market_brief",
            RoutingStatus.PARTIAL,
            (("state", P), ("recent_change", P), ("implications", P), ("confidence", P)),
            ("no_cutoff",),
        ),
        VisionQuestionScenario(
            "Q2",
            "某个细分领域现状如何？",
            _request(
                "Q2",
                subject_type=SubjectType.SECTOR,
                subject_refs=(SubjectRef("半导体设备"),),
                intents=(ResearchIntent.UNDERSTAND,),
                personalization=Personalization.NONE,
            ),
            "sector_brief",
            RoutingStatus.PARTIAL,
            (
                ("scope", X),
                ("fundamentals", X),
                ("valuation", X),
                ("flows_policy_cycle", X),
                ("representatives", P),
            ),
            (),
        ),
        VisionQuestionScenario(
            "Q3",
            "这个标的怎么样？",
            _request(
                "Q3",
                subject_type=SubjectType.ASSET,
                subject_refs=(SubjectRef("600519", "asset:600519"),),
                intents=(ResearchIntent.EVALUATE, ResearchIntent.DECIDE),
                personalization=Personalization.OPTIONAL,
                depth=ResearchDepth.DEEP,
                horizon=ResearchHorizon.MEDIUM_LONG,
            ),
            "asset_decision",
            RoutingStatus.PARTIAL,
            (
                ("decision_lanes", A),
                ("scenarios", A),
                ("priced_expectations", P),
                ("counter_and_review", A),
            ),
            ("no_conclusion", "fake_position", "baseline_as_conclusion"),
        ),
        VisionQuestionScenario(
            "Q4",
            "这个策略好不好？",
            _request(
                "Q4",
                subject_type=SubjectType.STRATEGY,
                subject_refs=(SubjectRef("低估值高股息"),),
                intents=(ResearchIntent.EVALUATE,),
                personalization=Personalization.NONE,
            ),
            "strategy_evaluation",
            RoutingStatus.PARTIAL,
            (
                ("mechanism", A),
                ("samples", X),
                ("costs", X),
                ("significance", X),
                ("benchmark", X),
                ("conditions", P),
            ),
            (),
        ),
        VisionQuestionScenario(
            "Q5",
            "我想把现有持仓变得更激进一些，如何调整？",
            _request(
                "Q5",
                subject_type=SubjectType.PORTFOLIO,
                subject_refs=(),
                intents=(ResearchIntent.EVALUATE, ResearchIntent.PLAN),
                personalization=Personalization.REQUIRED,
                user_id="user:a",
                personal_refs=("mandate:a",),
            ),
            "portfolio_rebalance",
            RoutingStatus.NEEDS_USER_CONTEXT,
            (
                ("meaning", P),
                ("diagnosis", P),
                ("risk_budget", X),
                ("adjustment", X),
                ("migration", X),
                ("stress", X),
            ),
            ("unconfirmed_meaning",),
        ),
        VisionQuestionScenario(
            "Q6",
            "帮我找一些这个领域值得投资的标的。",
            _request(
                "Q6",
                subject_type=SubjectType.SECTOR,
                subject_refs=(SubjectRef("新能源"),),
                intents=(ResearchIntent.DISCOVER,),
                personalization=Personalization.OPTIONAL,
            ),
            "sector_discovery",
            RoutingStatus.PARTIAL,
            (("scope", P), ("rules", X), ("candidates", P), ("priority", P)),
            ("list_only", "candidate_as_buy"),
        ),
        VisionQuestionScenario(
            "Q7",
            "我觉得快要经济危机了，你的观点是什么？证据和支撑？",
            _request(
                "Q7",
                subject_type=SubjectType.MACRO,
                subject_refs=(),
                intents=(ResearchIntent.TEST_VIEW,),
                personalization=Personalization.OPTIONAL,
                user_id="user:a",
                user_view="我觉得快要经济危机了",
            ),
            "view_test",
            RoutingStatus.PARTIAL,
            (
                ("proposition", A),
                ("indicators", P),
                ("evidence", P),
                ("system_view", P),
                ("portfolio_impact", P),
                ("signals", P),
            ),
            ("confirmation_bias", "exact_timing"),
        ),
        VisionQuestionScenario(
            "Q8",
            "我买的 A 已经亏了不少，接下来怎么办？",
            _request(
                "Q8",
                subject_type=SubjectType.ASSET,
                subject_refs=(SubjectRef("A", "asset:a"),),
                intents=(ResearchIntent.DECIDE,),
                personalization=Personalization.REQUIRED,
                user_id="user:a",
            ),
            "holding_review",
            RoutingStatus.PARTIAL,
            (
                ("original_thesis", A),
                ("rebuy_test", A),
                ("opportunity_cost", A),
                ("risk_share", A),
                ("behavioral_bias", X),
                ("action", P),
            ),
            (),
        ),
        VisionQuestionScenario(
            "Q9",
            "我觉得快消行业不错，请分析一下并推荐。",
            _request(
                "Q9",
                subject_type=SubjectType.SECTOR,
                subject_refs=(SubjectRef("快消"),),
                intents=(ResearchIntent.TEST_VIEW, ResearchIntent.DISCOVER),
                personalization=Personalization.REQUIRED,
                user_id="user:a",
                user_view="我觉得快消行业不错",
            ),
            "sector_view_recommendation",
            RoutingStatus.PARTIAL,
            (("view_test", P), ("sector", X), ("candidates", P), ("fit", P)),
            ("support_only",),
        ),
        VisionQuestionScenario(
            "Q10",
            "根据我提供的信息，用 HTML 拉一份时间表。",
            _request(
                "Q10",
                subject_type=SubjectType.PORTFOLIO,
                subject_refs=(),
                intents=(ResearchIntent.PLAN,),
                personalization=Personalization.REQUIRED,
                user_id="user:a",
            ),
            "plan_schedule",
            RoutingStatus.PARTIAL,
            (
                ("nodes", P),
                ("node_actions", P),
                ("targets", X),
                ("review", P),
                ("conflicts", X),
                ("html", X),
            ),
            ("exact_targets",),
        ),
    )


class VisionAcceptanceRunner:
    """Check the public routing and run outputs passed in; never writes narrative."""

    def evaluate(
        self,
        scenario: VisionQuestionScenario,
        routing: RoutingResult,
        run: RecipeRunResult | None = None,
    ) -> VisionScenarioOutcome:
        findings = []
        if routing.request != scenario.request:
            findings.append("request_mismatch")
        if routing.recipe_ids[:1] != (scenario.recipe_id,):
            findings.append("recipe_mismatch")
        if routing.status is not scenario.status:
            findings.append(f"status_changed:{routing.status.value}")
        actual = {item.element_id: item.status for item in routing.coverage}
        for element_id, status in scenario.element_status:
            if actual.get(element_id) is not status:
                findings.append(f"element_status_changed:{element_id}")
        for gap in routing.gaps:
            if not gap.system_modules or not gap.roadmap_stages:
                findings.append(f"gap_without_module_or_stage:{gap.element_id}")
        structural = {
            item.check_id for item in routing.avoid_checks if item.structural
        }
        for check_id in scenario.structural_checks:
            if check_id not in structural:
                findings.append(f"avoid_check_not_structural:{check_id}")
        if run is not None:
            findings.extend(_run_findings(routing, run))
        return VisionScenarioOutcome(
            scenario_id=scenario.scenario_id,
            passed=not findings,
            findings=tuple(findings),
            coverage=coverage_rows(scenario.question_id, routing),
        )


def coverage_rows(question_id: str, routing: RoutingResult) -> tuple[CoverageRow, ...]:
    gaps = {item.element_id: item for item in routing.gaps}
    return tuple(
        CoverageRow(
            question_id=question_id,
            element_id=item.element_id,
            element_text=item.text,
            status=item.status,
            system_modules=(
                gaps[item.element_id].system_modules
                if item.element_id in gaps
                else ()
            ),
            roadmap_stages=(
                gaps[item.element_id].roadmap_stages
                if item.element_id in gaps
                else ()
            ),
        )
        for item in routing.coverage
    )


def render_coverage_matrix(rows: tuple[CoverageRow, ...]) -> str:
    labels = {A: "支持", P: "部分", X: "缺口"}
    lines = [
        "| 问题 | 必须包含 | 状态 | 缺失模块 | 补齐阶段 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.question_id} | {row.element_text} | {labels[row.status]} "
            f"| {'、'.join(row.system_modules) or '—'} "
            f"| {'、'.join(str(item) for item in row.roadmap_stages) or '—'} |"
        )
    return "\n".join(lines)


def _run_findings(routing: RoutingResult, run: RecipeRunResult) -> list[str]:
    findings = []
    if run.routing != routing:
        findings.append("run_routing_mismatch")
    planned = {
        item.step.step_id
        for item in routing.steps
        if item.status is StepStatus.PLANNED
    }
    for outcome in run.step_outcomes:
        if (
            outcome.step_id in planned
            and outcome.status is StepOutcomeStatus.COMPLETED
        ):
            findings.append(f"planned_step_reported_complete:{outcome.step_id}")
    if (
        routing.status
        in (
            RoutingStatus.UNRESOLVED,
            RoutingStatus.UNRESOLVED_SUBJECT,
            RoutingStatus.NEEDS_USER_CONTEXT,
        )
        and (run.step_outcomes or run.ledger_entry_ids)
    ):
        findings.append("blocked_routing_had_side_effects")
    return findings
