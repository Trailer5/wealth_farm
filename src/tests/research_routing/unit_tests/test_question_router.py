import re
import unittest
from dataclasses import replace
from pathlib import Path

from src.research_contracts import (
    Personalization,
    ResearchDepth,
    ResearchHorizon,
    ResearchIntent,
    ResearchRequest,
    SubjectRef,
    SubjectType,
)
from src.research_orchestration import default_capability_registry
from src.research_orchestration.routing_adapters import RegistryLaneSupport
from src.research_routing import (
    PROFILE_LANE_GAP_STAGE,
    PREFERENCE_RECORDED,
    PREFERENCE_WITH_MEANING,
    RESOLVED,
    USER_CONTEXT_QUESTIONS,
    VIEW_HORIZON_QUESTION,
    PORTFOLIO_CASH_QUESTION,
    OutputElement,
    QuestionRouter,
    RecipeDefinition,
    RecipeMatch,
    RecipeStep,
    RoutingStatus,
    StepStatus,
    SubjectResolution,
    UserContextNeed,
    default_recipes,
)
from src.tests.research_contracts.unit_tests.test_research_requests import (
    representative_requests,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
VISION = PROJECT_ROOT / "docs" / "long_term" / "长期愿景.md"
SYSTEM_MAP = PROJECT_ROOT / "docs" / "long_term" / "系统模块总图.md"

EXPECTED = {
    "Q1": ("market_brief", RoutingStatus.PARTIAL),
    "Q2": ("sector_brief", RoutingStatus.PARTIAL),
    "Q3": ("asset_decision", RoutingStatus.EXECUTABLE),
    "Q4": ("strategy_evaluation", RoutingStatus.PARTIAL),
    "Q5": ("portfolio_rebalance", RoutingStatus.NEEDS_USER_CONTEXT),
    "Q6": ("sector_discovery", RoutingStatus.PARTIAL),
    "Q7": ("view_test", RoutingStatus.PARTIAL),
    "Q8": ("holding_review", RoutingStatus.PARTIAL),
    "Q9": ("sector_view_recommendation", RoutingStatus.PARTIAL),
    "Q10": ("plan_schedule", RoutingStatus.EXECUTABLE),
}


class QuestionRouterTest(unittest.TestCase):
    def setUp(self):
        self.router = QuestionRouter()
        self.requests = representative_requests()

    def test_representative_questions_reach_their_recipes(self):
        for question, (recipe_id, status) in EXPECTED.items():
            with self.subTest(question=question):
                result = self.router.route(self.requests[question])

                self.assertEqual(result.recipe_ids[0], recipe_id)
                self.assertEqual(result.status, status)
                for gap in result.gaps:
                    self.assertTrue(gap.system_modules)
                    self.assertTrue(
                        all(stage >= 4 for stage in gap.roadmap_stages)
                    )
                result.to_dict()

    def test_aggressive_rebalance_asks_what_aggressive_means(self):
        result = self.router.route(self.requests["Q5"])

        self.assertTrue(
            any("更激进" in item for item in result.user_questions)
        )
        confirmed = self.router.route(
            _replace(
                self.requests["Q5"],
                personal_refs=("mandate:a", "preference:aggressiveness:equity"),
            )
        )
        self.assertEqual(confirmed.status, RoutingStatus.EXECUTABLE)

    def test_aggressive_rebalance_needs_a_recorded_preference_with_meanings(self):
        probe = _Holdings({("user:a", None)})
        probe.preferences = {"preference:plain": PREFERENCE_RECORDED, "preference:bold": PREFERENCE_WITH_MEANING}
        router = QuestionRouter(context_probe=probe)

        statuses = {
            ref: router.route(_replace(self.requests["Q5"], personal_refs=(ref,))).status
            for ref in ("preference:unknown", "preference:plain", "preference:bold")
        }

        self.assertEqual(statuses["preference:unknown"], RoutingStatus.NEEDS_USER_CONTEXT)
        self.assertEqual(statuses["preference:plain"], RoutingStatus.NEEDS_USER_CONTEXT)
        self.assertEqual(statuses["preference:bold"], RoutingStatus.EXECUTABLE)

    def test_user_without_a_preference_is_asked_for_the_baseline_and_cash(self):
        probe = _Holdings({("user:a", None)})
        probe.preferences = {"preference:plain": PREFERENCE_RECORDED}
        router = QuestionRouter(context_probe=probe)

        fresh = router.route(_replace(self.requests["Q5"], personal_refs=()))
        recorded = router.route(_replace(self.requests["Q5"], personal_refs=("preference:plain",)))

        baseline = USER_CONTEXT_QUESTIONS[UserContextNeed.INVESTMENT_PREFERENCE]
        self.assertEqual(fresh.user_questions[0], baseline)
        self.assertIn(USER_CONTEXT_QUESTIONS[UserContextNeed.AGGRESSIVENESS_MEANING], fresh.user_questions)
        self.assertNotIn(baseline, recorded.user_questions)
        self.assertEqual(fresh.user_questions[-1], PORTFOLIO_CASH_QUESTION)

    def test_holding_review_needs_positions_recorded_for_the_asset(self):
        probe = _Holdings(set())
        router = QuestionRouter(context_probe=probe)

        missing = router.route(self.requests["Q8"])
        probe.held.add(("user:a", "asset:a"))
        recorded = router.route(self.requests["Q8"])

        self.assertEqual(missing.status, RoutingStatus.NEEDS_USER_CONTEXT)
        self.assertIn(
            USER_CONTEXT_QUESTIONS[UserContextNeed.POSITIONS],
            missing.user_questions,
        )
        self.assertEqual(recorded.status, RoutingStatus.PARTIAL)

    def test_view_without_time_window_asks_for_it_when_routed(self):
        without = self.router.route(self.requests["Q7"])
        with_window = self.router.route(
            _replace(self.requests["Q7"], horizon=ResearchHorizon.SHORT)
        )

        self.assertEqual(without.status, RoutingStatus.PARTIAL)
        self.assertEqual(without.user_questions[0], VIEW_HORIZON_QUESTION)
        self.assertNotIn(VIEW_HORIZON_QUESTION, with_window.user_questions)

    def test_portfolio_steps_only_apply_when_user_is_known(self):
        anonymous = self.router.route(self.requests["Q1"])
        personal = self.router.route(
            _replace(self.requests["Q1"], user_id="user:a")
        )

        self.assertNotIn(
            "portfolio_impact",
            {item.element_id for item in anonymous.coverage},
        )
        self.assertEqual(personal.status, RoutingStatus.PARTIAL)

    def test_unmatched_combination_is_unresolved_with_suggestions(self):
        result = self.router.route(
            _replace(
                self.requests["Q4"],
                intents=(ResearchIntent.DISCOVER,),
            )
        )

        self.assertEqual(result.status, RoutingStatus.UNRESOLVED)
        self.assertEqual(result.recipe_ids, ())
        self.assertIn("strategy_evaluation", result.suggestions)
        self.assertIn("sector_discovery", result.suggestions)

    def test_ambiguous_asset_stops_before_steps_are_trusted(self):
        router = QuestionRouter(
            subject_resolver=_Resolver(
                SubjectResolution(
                    "A",
                    "needs_clarification",
                    candidates=("stock:a", "fund:a"),
                )
            )
        )

        result = router.route(
            _replace(self.requests["Q3"], subject_refs=(SubjectRef("A"),))
        )

        self.assertEqual(result.status, RoutingStatus.UNRESOLVED_SUBJECT)
        self.assertEqual(result.subjects[0].candidates, ("stock:a", "fund:a"))

    def test_open_fund_timing_and_position_are_available(self):
        router = QuestionRouter(
            subject_resolver=_Resolver(
                SubjectResolution(
                    "163417",
                    RESOLVED,
                    asset_id="fund:163417",
                    profile_id="open_fund",
                )
            ),
            lane_support=RegistryLaneSupport(default_capability_registry()),
        )

        result = router.route(self.requests["Q3"])

        steps = {item.step.step_id: item for item in result.steps}
        for step_id in ("suitability", "timing", "position"):
            self.assertEqual(steps[step_id].status, StepStatus.AVAILABLE)
            self.assertNotEqual(
                steps[step_id].roadmap_stage,
                PROFILE_LANE_GAP_STAGE,
            )
        if result.request.personalization.value != "none":
            self.assertIn("持有多久", result.user_questions[0])
        self.assertFalse(
            [item for item in result.gaps if item.element_id == "decision_lanes"]
        )

    def test_view_test_recipe_requires_counter_evidence(self):
        with self.assertRaisesRegex(ValueError, "counter-evidence"):
            RecipeDefinition(
                recipe_id="one_sided",
                question_ids=(),
                title="只找支持证据",
                handles=(
                    RecipeMatch(
                        SubjectType.MACRO,
                        (ResearchIntent.TEST_VIEW,),
                        tuple(Personalization),
                    ),
                ),
                steps=(
                    RecipeStep(
                        "support",
                        "支持证据",
                        "O3",
                        StepStatus.AVAILABLE,
                        produces=("support",),
                        entry="agent",
                    ),
                ),
                output_elements=(OutputElement("support", "支持证据"),),
                avoid_checks=(),
                supported_depths=(ResearchDepth.STANDARD,),
            )


class RecipeDocumentAlignmentTest(unittest.TestCase):
    def test_recipes_carry_the_vision_must_include_and_avoid_lists(self):
        requirements = _vision_requirements()
        questions = set()
        for recipe in default_recipes():
            for question in recipe.question_ids:
                questions.add(question)
                includes, avoids = requirements[question]
                with self.subTest(question=question):
                    self.assertEqual(
                        [item.text for item in recipe.output_elements],
                        includes,
                    )
                    self.assertEqual(
                        [item.text for item in recipe.avoid_checks],
                        avoids,
                    )
        self.assertEqual(questions, set(requirements))

    def test_step_status_matches_system_module_map(self):
        modules = _system_module_status()
        for recipe in default_recipes():
            for step in recipe.steps:
                with self.subTest(recipe=recipe.recipe_id, step=step.step_id):
                    self.assertIn(step.system_module, modules)
                    planned_module = modules[step.system_module].startswith(
                        "规划"
                    )
                    if planned_module:
                        self.assertIs(step.status, StepStatus.PLANNED)


class _Resolver:
    def __init__(self, resolution):
        self.resolution = resolution

    def resolve(self, ref, as_of_date):
        return self.resolution


class _Holdings:
    def __init__(self, held):
        self.held = held
        self.preferences = {}

    def has_positions(self, user_id, asset_id):
        return (user_id, asset_id) in self.held

    def preference_status(self, user_id, ref):
        return self.preferences.get(ref)


def _replace(request: ResearchRequest, **changes) -> ResearchRequest:
    return replace(request, **changes)


def _vision_requirements() -> dict[str, tuple[list[str], list[str]]]:
    text = VISION.read_text(encoding="utf-8")
    result = {}
    for block in re.split(r"^#### ", text, flags=re.M)[1:]:
        question = block.split("：", 1)[0]
        if not re.fullmatch(r"Q\d+", question):
            continue
        body = block.split("必须包含：", 1)[1].split("必须避免", 1)[0]
        includes = re.findall(r"^\d+\. (.+)$", body, flags=re.M)
        avoid = re.search(r"^必须避免：(.+)$", block, flags=re.M).group(1)
        avoids = [
            item.strip().rstrip("。") for item in avoid.split("；")
        ]
        result[question] = (includes, avoids)
    return result


def _system_module_status() -> dict[str, str]:
    text = SYSTEM_MAP.read_text(encoding="utf-8")
    index = text.split("## 三、模块索引", 1)[1].split("## 四、", 1)[0]
    result = {}
    for line in index.splitlines():
        cells = [item.strip() for item in line.split("|")]
        if len(cells) >= 7 and re.fullmatch(r"[A-Z]\d", cells[1]):
            result[cells[1]] = cells[5]
    return result


if __name__ == "__main__":
    unittest.main()
