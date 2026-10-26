"""Recipe and routing structures for question-level research."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.research_contracts import (
    DecisionLane,
    Personalization,
    ResearchDepth,
    ResearchIntent,
    ResearchRequest,
    SubjectType,
    to_payload,
)


class StepStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    PLANNED = "planned"


class RoutingStatus(str, Enum):
    EXECUTABLE = "executable"
    PARTIAL = "partial"
    GAP_ONLY = "gap_only"
    NEEDS_USER_CONTEXT = "needs_user_context"
    UNRESOLVED_SUBJECT = "unresolved_subject"
    UNRESOLVED = "unresolved"


class UserContextNeed(str, Enum):
    POSITIONS = "positions"
    INVESTMENT_MANDATE = "investment_mandate"
    CONSTRAINT_SNAPSHOT = "constraint_snapshot"
    AGGRESSIVENESS_MEANING = "aggressiveness_meaning"
    ORIGINAL_THESIS = "original_thesis"


# How a request proves a need is met: None means recorded holdings of the
# request user (only the user_id is checked when no context probe is wired).
USER_CONTEXT_REF_PREFIXES: dict[UserContextNeed, str | None] = {
    UserContextNeed.POSITIONS: None,
    UserContextNeed.INVESTMENT_MANDATE: "mandate:",
    UserContextNeed.CONSTRAINT_SNAPSHOT: "constraints:",
    UserContextNeed.AGGRESSIVENESS_MEANING: "preference:aggressiveness",
    UserContextNeed.ORIGINAL_THESIS: "thesis:",
}

USER_CONTEXT_QUESTIONS: dict[UserContextNeed, str] = {
    UserContextNeed.POSITIONS: "请提供当前持仓（标的、数量、成本），记录到用户空间后继续。",
    UserContextNeed.INVESTMENT_MANDATE: "请提供投资任务：风险预算、计划资金和投资期限。",
    UserContextNeed.CONSTRAINT_SNAPSHOT: "请确认组合约束：单标的上限、现金底线和流动性要求。",
    UserContextNeed.AGGRESSIVENESS_MEANING: (
        "“更激进”具体指更高权益比例、更集中、更高波动，还是更长期限？"
    ),
    UserContextNeed.ORIGINAL_THESIS: "当初买入的理由和预期是什么？",
}

VIEW_HORIZON_QUESTION = (
    "这个观点的时间窗口是多久：1 个月内、1–6 个月、6 个月–3 年，还是 3 年以上？"
)


@dataclass(frozen=True)
class OutputElement:
    """One “必须包含” item of the question in 长期愿景."""

    element_id: str
    text: str


@dataclass(frozen=True)
class AvoidCheck:
    """One “必须避免” item; structural ones are enforced by code."""

    check_id: str
    text: str
    enforced_by: str | None = None

    @property
    def structural(self) -> bool:
        return self.enforced_by is not None


@dataclass(frozen=True)
class RecipeStep:
    step_id: str
    description: str
    system_module: str
    status: StepStatus
    produces: tuple[str, ...] = ()
    roadmap_stage: int | None = None
    entry: str | None = None
    decision_lane: DecisionLane | None = None
    personal_only: bool = False
    counter_evidence: bool = False

    def __post_init__(self) -> None:
        if self.status is StepStatus.AVAILABLE:
            if self.entry is None or self.roadmap_stage is not None:
                raise ValueError(
                    f"{self.step_id}: available steps need an entry "
                    "and no roadmap stage"
                )
        elif self.status is StepStatus.PLANNED:
            if self.roadmap_stage is None or self.entry is not None:
                raise ValueError(
                    f"{self.step_id}: planned steps need a roadmap stage "
                    "and no entry"
                )
        elif self.roadmap_stage is None or self.entry is None:
            raise ValueError(
                f"{self.step_id}: partial steps need an entry and a stage"
            )


@dataclass(frozen=True)
class RecipeMatch:
    subject_type: SubjectType
    intents: tuple[ResearchIntent, ...]
    personalization: tuple[Personalization, ...]

    def matches(self, request: ResearchRequest) -> bool:
        return (
            request.subject_type is self.subject_type
            and set(request.intents) == set(self.intents)
            and request.personalization in self.personalization
        )


@dataclass(frozen=True)
class RecipeDefinition:
    recipe_id: str
    question_ids: tuple[str, ...]
    title: str
    handles: tuple[RecipeMatch, ...]
    steps: tuple[RecipeStep, ...]
    output_elements: tuple[OutputElement, ...]
    avoid_checks: tuple[AvoidCheck, ...]
    supported_depths: tuple[ResearchDepth, ...]
    required_user_context: tuple[UserContextNeed, ...] = ()
    optional_user_context: tuple[UserContextNeed, ...] = ()
    sub_recipes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        element_ids = [item.element_id for item in self.output_elements]
        step_ids = [item.step_id for item in self.steps]
        for name, values in (
            ("output_elements", element_ids),
            ("steps", step_ids),
            ("avoid_checks", [item.check_id for item in self.avoid_checks]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{self.recipe_id}: duplicate {name}")
        produced = {
            element for step in self.steps for element in step.produces
        }
        if not produced <= set(element_ids):
            raise ValueError(
                f"{self.recipe_id}: steps produce unknown elements "
                f"{sorted(produced - set(element_ids))}"
            )
        if set(element_ids) - produced:
            raise ValueError(
                f"{self.recipe_id}: elements without a step "
                f"{sorted(set(element_ids) - produced)}"
            )
        tests_view = any(
            ResearchIntent.TEST_VIEW in match.intents
            for match in self.handles
        )
        if tests_view and not any(
            step.counter_evidence for step in self.steps
        ):
            raise ValueError(
                f"{self.recipe_id}: view tests need a counter-evidence step"
            )


@dataclass(frozen=True)
class SubjectResolution:
    text: str
    status: str
    asset_id: str | None = None
    profile_id: str | None = None
    candidates: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    # Readable "name (type, exchange)" for each candidate, in the same order.
    candidate_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedStep:
    step: RecipeStep
    status: StepStatus
    roadmap_stage: int | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ElementCoverage:
    element_id: str
    text: str
    status: StepStatus


@dataclass(frozen=True)
class RoutingGap:
    element_id: str
    element_text: str
    status: StepStatus
    system_modules: tuple[str, ...]
    roadmap_stages: tuple[int, ...]


@dataclass(frozen=True)
class RoutingResult:
    request: ResearchRequest
    status: RoutingStatus
    recipe_ids: tuple[str, ...] = ()
    steps: tuple[RoutedStep, ...] = ()
    coverage: tuple[ElementCoverage, ...] = ()
    gaps: tuple[RoutingGap, ...] = ()
    avoid_checks: tuple[AvoidCheck, ...] = ()
    user_questions: tuple[str, ...] = ()
    subjects: tuple[SubjectResolution, ...] = ()
    suggestions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = to_payload(self)
        payload["avoid_checks"] = [
            {**item, "structural": check.structural}
            for item, check in zip(payload["avoid_checks"], self.avoid_checks)
        ]
        return payload
