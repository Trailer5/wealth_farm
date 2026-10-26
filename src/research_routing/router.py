"""Route a structured research request to recipes, steps and formal gaps."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.research_contracts import (
    DecisionLane,
    Personalization,
    ResearchIntent,
    ResearchRequest,
    SubjectRef,
    SubjectType,
)

from .models import (
    USER_CONTEXT_QUESTIONS,
    USER_CONTEXT_REF_PREFIXES,
    VIEW_HORIZON_QUESTION,
    ElementCoverage,
    RecipeDefinition,
    RecipeStep,
    RoutedStep,
    RoutingGap,
    RoutingResult,
    RoutingStatus,
    StepStatus,
    SubjectResolution,
    UserContextNeed,
)
from .recipes import RecipeRegistry, default_recipe_registry

# Profile-specific lane gaps (for example open-fund timing) close in 标的决策深化.
PROFILE_LANE_GAP_STAGE = 7
RESOLVED = "resolved"


class SubjectResolverProtocol(Protocol):
    def resolve(self, ref: SubjectRef, as_of_date: str) -> SubjectResolution: ...


class LaneSupportProtocol(Protocol):
    def lane_gaps(self, profile_id: str, lane: DecisionLane) -> tuple[str, ...]: ...


class UserContextProbeProtocol(Protocol):
    def has_positions(self, user_id: str, asset_id: str | None) -> bool: ...


class QuestionRouter:
    def __init__(
        self,
        *,
        recipes: RecipeRegistry | None = None,
        subject_resolver: SubjectResolverProtocol | None = None,
        lane_support: LaneSupportProtocol | None = None,
        context_probe: UserContextProbeProtocol | None = None,
    ) -> None:
        self.recipes = recipes or default_recipe_registry()
        self.subject_resolver = subject_resolver
        self.lane_support = lane_support
        self.context_probe = context_probe

    def route(self, request: ResearchRequest) -> RoutingResult:
        recipe = next(
            (
                item
                for item in self.recipes.list_recipes()
                if any(match.matches(request) for match in item.handles)
            ),
            None,
        )
        if recipe is None:
            return RoutingResult(
                request=request,
                status=RoutingStatus.UNRESOLVED,
                suggestions=self._suggestions(request),
            )
        subjects = self._resolve_subjects(request)
        profiles = {
            item.profile_id
            for item in subjects
            if item.status == RESOLVED and item.profile_id
        }
        steps = tuple(
            self._route_step(step, profiles)
            for step in recipe.steps
            if request.user_id is not None or not step.personal_only
        )
        coverage = _coverage(recipe, steps)
        positions = self._positions_recorded(request, subjects)
        missing = _missing_context(
            request, recipe.required_user_context, positions
        )
        optional = _missing_context(
            request, recipe.optional_user_context, positions
        )
        if request.personalization is Personalization.NONE:
            optional = ()
        if any(item.status != RESOLVED for item in subjects):
            status = RoutingStatus.UNRESOLVED_SUBJECT
        elif missing:
            status = RoutingStatus.NEEDS_USER_CONTEXT
        else:
            status = _step_status(steps)
        questions = tuple(
            USER_CONTEXT_QUESTIONS[item] for item in (*missing, *optional)
        )
        if ResearchIntent.TEST_VIEW in request.intents and request.horizon is None:
            questions = (VIEW_HORIZON_QUESTION, *questions)
        return RoutingResult(
            request=request,
            status=status,
            recipe_ids=(recipe.recipe_id, *recipe.sub_recipes),
            steps=steps,
            coverage=coverage,
            gaps=_gaps(coverage, steps),
            avoid_checks=recipe.avoid_checks,
            user_questions=questions,
            subjects=subjects,
        )

    def _positions_recorded(
        self,
        request: ResearchRequest,
        subjects: Sequence[SubjectResolution],
    ) -> bool:
        if request.user_id is None:
            return False
        if self.context_probe is None:
            return True
        asset_ids = [item.asset_id for item in subjects if item.asset_id]
        if not asset_ids:
            return self.context_probe.has_positions(request.user_id, None)
        return all(
            self.context_probe.has_positions(request.user_id, asset_id)
            for asset_id in asset_ids
        )

    def _resolve_subjects(
        self,
        request: ResearchRequest,
    ) -> tuple[SubjectResolution, ...]:
        if request.subject_type is not SubjectType.ASSET:
            return ()
        if self.subject_resolver is None:
            return tuple(
                SubjectResolution(
                    ref.text,
                    RESOLVED if ref.resolved_id else "not_checked",
                    asset_id=ref.resolved_id,
                )
                for ref in request.subject_refs
            )
        return tuple(
            self.subject_resolver.resolve(ref, request.as_of_date)
            for ref in request.subject_refs
        )

    def _route_step(
        self,
        step: RecipeStep,
        profiles: set[str],
    ) -> RoutedStep:
        if (
            step.decision_lane is None
            or self.lane_support is None
            or step.status is StepStatus.PLANNED
        ):
            return RoutedStep(step, step.status, step.roadmap_stage)
        blockers = tuple(
            dict.fromkeys(
                gap
                for profile in sorted(profiles)
                for gap in self.lane_support.lane_gaps(
                    profile,
                    step.decision_lane,
                )
            )
        )
        if not blockers:
            return RoutedStep(step, step.status, step.roadmap_stage)
        return RoutedStep(
            step,
            StepStatus.PLANNED,
            PROFILE_LANE_GAP_STAGE,
            notes=blockers,
        )

    def _suggestions(self, request: ResearchRequest) -> tuple[str, ...]:
        """Recipes sharing the subject type, then those sharing an intent."""

        recipes = self.recipes.list_recipes()
        same_subject = [
            item.recipe_id
            for item in recipes
            if any(
                match.subject_type is request.subject_type
                for match in item.handles
            )
        ]
        same_intent = [
            item.recipe_id
            for item in recipes
            if any(
                set(match.intents) & set(request.intents)
                for match in item.handles
            )
        ]
        return tuple(dict.fromkeys((*same_subject, *same_intent)))


def _coverage(
    recipe: RecipeDefinition,
    steps: Sequence[RoutedStep],
) -> tuple[ElementCoverage, ...]:
    result = []
    for element in recipe.output_elements:
        statuses = [
            item.status
            for item in steps
            if element.element_id in item.step.produces
        ]
        if not statuses:
            continue
        if all(item is StepStatus.AVAILABLE for item in statuses):
            status = StepStatus.AVAILABLE
        elif all(item is StepStatus.PLANNED for item in statuses):
            status = StepStatus.PLANNED
        else:
            status = StepStatus.PARTIAL
        result.append(ElementCoverage(element.element_id, element.text, status))
    return tuple(result)


def _gaps(
    coverage: Sequence[ElementCoverage],
    steps: Sequence[RoutedStep],
) -> tuple[RoutingGap, ...]:
    gaps = []
    for element in coverage:
        if element.status is StepStatus.AVAILABLE:
            continue
        lacking = [
            item
            for item in steps
            if element.element_id in item.step.produces
            and item.status is not StepStatus.AVAILABLE
        ]
        gaps.append(
            RoutingGap(
                element_id=element.element_id,
                element_text=element.text,
                status=element.status,
                system_modules=tuple(
                    dict.fromkeys(item.step.system_module for item in lacking)
                ),
                roadmap_stages=tuple(
                    sorted({item.roadmap_stage for item in lacking})
                ),
            )
        )
    return tuple(gaps)


def _missing_context(
    request: ResearchRequest,
    needs: Sequence[UserContextNeed],
    positions_recorded: bool,
) -> tuple[UserContextNeed, ...]:
    missing = []
    for need in needs:
        prefix = USER_CONTEXT_REF_PREFIXES[need]
        if prefix is None:
            satisfied = positions_recorded
        else:
            satisfied = any(
                ref.startswith(prefix) for ref in request.personal_refs
            )
        if not satisfied:
            missing.append(need)
    return tuple(missing)


def _step_status(steps: Sequence[RoutedStep]) -> RoutingStatus:
    statuses = {item.status for item in steps}
    if statuses == {StepStatus.AVAILABLE}:
        return RoutingStatus.EXECUTABLE
    if statuses == {StepStatus.PLANNED}:
        return RoutingStatus.GAP_ONLY
    return RoutingStatus.PARTIAL
