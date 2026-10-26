import re
import unittest
from dataclasses import replace
from pathlib import Path

from src.research_routing import (
    QuestionRouter,
    RecipeRunResult,
    RecipeRunStatus,
    RoutingStatus,
    StepOutcome,
    StepOutcomeStatus,
    VisionAcceptanceRunner,
    render_coverage_matrix,
    vision_question_scenarios,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
VISION = PROJECT_ROOT / "docs" / "long_term" / "长期愿景.md"
SYSTEM_MAP = PROJECT_ROOT / "docs" / "long_term" / "系统模块总图.md"


class VisionAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.router = QuestionRouter()
        self.runner = VisionAcceptanceRunner()
        self.scenarios = vision_question_scenarios()

    def test_all_frozen_scenarios_replay(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                outcome = self.runner.evaluate(
                    scenario,
                    self.router.route(scenario.request),
                )
                self.assertTrue(outcome.passed, outcome.findings)

    def test_scenarios_quote_the_ten_vision_questions(self):
        headings = dict(
            re.findall(
                r"^#### (Q\d+)：(.+)$",
                VISION.read_text(encoding="utf-8"),
                flags=re.M,
            )
        )

        self.assertEqual(
            {item.question_id: item.question_text for item in self.scenarios},
            headings,
        )

    def test_capability_drift_is_reported(self):
        scenario = self.scenarios[2]
        routing = self.router.route(scenario.request)
        upgraded = replace(
            routing,
            status=RoutingStatus.EXECUTABLE,
            coverage=tuple(
                replace(item, status=scenario.element_status[0][1])
                for item in routing.coverage
            ),
        )

        outcome = self.runner.evaluate(scenario, upgraded)

        self.assertFalse(outcome.passed)
        self.assertIn("status_changed:executable", outcome.findings)
        self.assertIn(
            "element_status_changed:priced_expectations",
            outcome.findings,
        )

    def test_run_outputs_cannot_overstate_execution(self):
        scenario = self.scenarios[4]
        routing = self.router.route(scenario.request)
        leaked = RecipeRunResult(
            routing=routing,
            status=RecipeRunStatus.EXECUTED,
            step_outcomes=(
                StepOutcome(
                    "adjustment_plan",
                    StepOutcomeStatus.COMPLETED,
                    "伪造的调整方案",
                ),
            ),
        )

        outcome = self.runner.evaluate(scenario, routing, leaked)

        self.assertIn(
            "planned_step_reported_complete:adjustment_plan",
            outcome.findings,
        )
        self.assertIn("blocked_routing_had_side_effects", outcome.findings)

    def test_coverage_gap_modules_match_system_module_map(self):
        expected = _system_map_gap_modules()
        rendered = []
        for scenario in self.scenarios:
            outcome = self.runner.evaluate(
                scenario,
                self.router.route(scenario.request),
            )
            rendered.extend(outcome.coverage)
            with self.subTest(question=scenario.question_id):
                self.assertEqual(
                    {
                        module
                        for row in outcome.coverage
                        for module in row.system_modules
                    },
                    expected[scenario.question_id],
                )
        matrix = render_coverage_matrix(tuple(rendered))
        self.assertEqual(matrix.count("\n") - 1, len(rendered))


def _system_map_gap_modules() -> dict[str, set[str]]:
    text = SYSTEM_MAP.read_text(encoding="utf-8")
    section = text.split("## 五、", 1)[1].split("## 六、", 1)[0]
    result = {}
    for line in section.splitlines():
        cells = [item.strip() for item in line.split("|")]
        match = re.match(r"(Q\d+) ", cells[1]) if len(cells) > 3 else None
        if match:
            result[match.group(1)] = set(cells[3].split("、"))
    return result


if __name__ == "__main__":
    unittest.main()
