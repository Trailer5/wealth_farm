import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_acceptance import (
    ExpectedFailureSemantics,
    NewConversationAcceptanceService,
    NewConversationObservation,
    NewConversationScenario,
    representative_new_conversation_scenarios,
)
from src.research_orchestration import (
    NewConversationAcceptanceRunner,
    ResearchRequestPlanner,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_FINANCIAL_INSTITUTION,
)
from src.research_workflow import (
    ResearchProfileAssignmentService,
    ResearchTaskFactory,
    SQLiteResearchProfileAssignmentRepository,
)


class NewConversationAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.scenarios = {
            item.scenario_id: item
            for item in representative_new_conversation_scenarios()
        }
        self.service = NewConversationAcceptanceService()

    def test_frozen_catalog_covers_profiles_and_failure_semantics(self):
        self.assertEqual(len(self.scenarios), 9)
        self.assertEqual(
            {
                item.expected_profile_id
                for item in self.scenarios.values()
                if item.expected_profile_id is not None
            },
            {
                "ordinary_company",
                "open_fund",
                "exchange_fund",
                "financial_institution",
                "cyclical_resource",
            },
        )
        self.assertEqual(
            {
                item.expected_failure
                for item in self.scenarios.values()
            },
            set(ExpectedFailureSemantics),
        )
        for scenario in self.scenarios.values():
            restored = NewConversationScenario.from_dict(
                scenario.to_dict()
            )
            self.assertEqual(restored, scenario)

    def test_every_frozen_scenario_accepts_its_expected_observation(self):
        for scenario in self.scenarios.values():
            with self.subTest(scenario=scenario.scenario_id):
                result = self.service.evaluate(
                    scenario,
                    _passing_observation(scenario),
                )
                self.assertTrue(
                    result.passed,
                    [item.code for item in result.findings],
                )

    def test_ready_final_requires_support_and_counterevidence_rounds(self):
        scenario = self.scenarios["open_fund_002236_long_term"]
        observation = replace(
            _passing_observation(scenario),
            counterevidence_round_count=0,
        )

        result = self.service.evaluate(scenario, observation)

        self.assertFalse(result.passed)
        self.assertIn(
            "counterevidence_round_missing",
            {item.code for item in result.findings},
        )

    def test_missing_position_context_cannot_be_replaced_by_fixed_range(self):
        scenario = self.scenarios["open_fund_002236_long_term"]
        observation = replace(
            _passing_observation(scenario),
            position_status="available",
            position_has_range=True,
        )

        result = self.service.evaluate(scenario, observation)

        self.assertFalse(result.passed)
        self.assertIn(
            "unsolicited_position_range",
            {item.code for item in result.findings},
        )

    def test_ambiguity_stops_before_baseline_execution(self):
        scenario = self.scenarios["ambiguous_asset_type"]
        observation = replace(
            _passing_observation(scenario),
            baseline_status="complete",
            workflow_created=True,
        )

        result = self.service.evaluate(scenario, observation)

        self.assertFalse(result.passed)
        self.assertIn(
            "execution_started_before_identity",
            {item.code for item in result.findings},
        )

    def test_remote_failure_cannot_be_reported_as_business_empty(self):
        scenario = self.scenarios["provider_remote_failure"]
        observation = replace(
            _passing_observation(scenario),
            query_outcomes=("business_empty",),
        )

        result = self.service.evaluate(scenario, observation)

        self.assertFalse(result.passed)
        self.assertIn(
            "query_outcome_missing",
            {item.code for item in result.findings},
        )

    def test_blocked_baseline_cannot_create_handoff_or_final_report(self):
        scenario = self.scenarios["baseline_execution_blocked"]
        observation = replace(
            _passing_observation(scenario),
            handoff_created=True,
            final_report_published=True,
            final_report_path="report/final.md",
        )

        result = self.service.evaluate(scenario, observation)

        codes = {item.code for item in result.findings}
        self.assertIn("blocked_baseline_has_handoff", codes)
        self.assertIn("blocked_baseline_downstream_started", codes)


class NewConversationPlanningRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "slice12.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        assets = {
            "maotai": _register(
                self.master,
                "stock:600519",
                ASSET_TYPE_STOCK,
                "600519",
                "贵州茅台",
            ),
            "open_fund": _register(
                self.master,
                "open-fund:002236",
                ASSET_TYPE_OPEN_FUND,
                "002236",
                "大成360互联网+大数据100A",
                market="cn_fund",
                exchange=None,
            ),
            "gold_etf": _register(
                self.master,
                "etf:518880",
                ASSET_TYPE_EXCHANGE_FUND,
                "518880",
                "黄金ETF",
            ),
            "pingan": _register(
                self.master,
                "stock:000001",
                ASSET_TYPE_STOCK,
                "000001",
                "平安银行",
                exchange="sz",
            ),
            "zijin": _register(
                self.master,
                "stock:601899",
                ASSET_TYPE_STOCK,
                "601899",
                "紫金矿业",
            ),
        }
        _register(
            self.master,
            "index:000001",
            ASSET_TYPE_INDEX,
            "000001",
            "上证指数",
        )
        assignments = ResearchProfileAssignmentService(
            master_data=self.master,
            repository=SQLiteResearchProfileAssignmentRepository(
                db_path
            ),
        )
        assignments.assign_manual(
            asset_id=assets["pingan"].asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            industry_name="银行",
            effective_date="2026-01-01",
            reason="Slice 12 representative financial profile",
        )
        assignments.assign_manual(
            asset_id=assets["zijin"].asset_id,
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            industry_name="工业金属",
            effective_date="2026-01-01",
            reason="Slice 12 representative cyclical profile",
        )
        factory = ResearchTaskFactory(
            self.master,
            benchmark_resolver=_RecordedBenchmark(
                _register(
                    self.master,
                    "index:000300",
                    ASSET_TYPE_INDEX,
                    "000300",
                    "沪深300",
                ).asset_id
            ),
        )
        self.runner = NewConversationAcceptanceRunner(
            planner=ResearchRequestPlanner(
                master_data=self.master,
                task_factory=factory,
                profile_assignment=assignments,
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_public_planner_matches_every_frozen_scenario(self):
        for scenario in representative_new_conversation_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                planning = self.runner.plan(scenario)
                self.assertEqual(
                    planning.status.value,
                    scenario.expected_planning_status,
                )
                self.assertEqual(
                    planning.profile_id,
                    scenario.expected_profile_id,
                )
                if scenario.expected_baseline_question is not None:
                    self.assertEqual(
                        planning.task.question_type,
                        scenario.expected_baseline_question,
                    )
                else:
                    self.assertIsNone(planning.task)


def _passing_observation(
    scenario: NewConversationScenario,
) -> NewConversationObservation:
    if scenario.expected_planning_status != "ready":
        return NewConversationObservation(
            scenario_id=scenario.scenario_id,
            planning_status=scenario.expected_planning_status,
            candidate_count=2,
            asked_user_questions=scenario.required_user_questions,
        )
    blocked = (
        scenario.expected_failure
        is ExpectedFailureSemantics.BASELINE_BLOCKED
    )
    query_failure = scenario.expected_failure in (
        ExpectedFailureSemantics.REMOTE_FAILURE,
        ExpectedFailureSemantics.BUSINESS_EMPTY,
    )
    final = scenario.final_report_required
    position_available = scenario.position_required
    return NewConversationObservation(
        scenario_id=scenario.scenario_id,
        planning_status="ready",
        resolved_asset_id=f"asset:{scenario.symbol}",
        profile_id=scenario.expected_profile_id,
        baseline_question=(
            scenario.expected_baseline_question.value
            if scenario.expected_baseline_question is not None
            else None
        ),
        baseline_status=("blocked" if blocked else "complete"),
        workflow_created=True,
        handoff_created=not blocked,
        baseline_report_path=(
            None
            if blocked
            else f"report/{scenario.scenario_id}-baseline.md"
        ),
        exploration_status=(
            "blocked"
            if query_failure
            else (
                "ready_for_report"
                if scenario.exploration_required
                else None
            )
        ),
        supporting_round_count=(
            1 if scenario.exploration_required else 0
        ),
        counterevidence_round_count=(
            1 if scenario.exploration_required else 0
        ),
        query_outcomes=scenario.expected_query_outcomes,
        asked_user_questions=scenario.required_user_questions,
        gap_codes=(
            ("daily_bars_unavailable",)
            if blocked
            else (
                (f"{scenario.expected_failure.value}_observed",)
                if query_failure
                else ()
            )
        ),
        suitability_action=(
            "consider" if scenario.exploration_required else None
        ),
        entry_status=(
            "wait" if scenario.timing_required else "not_applicable"
        ),
        position_status=(
            "available" if position_available else "unsupported"
        ),
        position_has_range=position_available,
        final_review_passed=final,
        final_report_published=final,
        final_report_path=(
            f"report/{scenario.scenario_id}-final.md"
            if final
            else None
        ),
        final_decision_lane_count=3 if final else 0,
    )


def _register(
    master,
    asset_id,
    asset_type,
    symbol,
    name,
    *,
    market="cn_a",
    exchange="sh",
):
    return master.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=symbol,
            display_name=name,
            market=market,
            exchange=exchange,
        ),
        source=f"slice12-unit-test:{asset_type}",
        raw_symbol=symbol,
    )


class _RecordedBenchmark:
    """Benchmarks the frozen scenarios assume were reviewed beforehand."""

    def __init__(self, index_asset_id):
        self.index_asset_id = index_asset_id

    def resolve(self, asset, profile, as_of_date):
        return (
            self.index_asset_id if profile.benchmark_required else None
        )


if __name__ == "__main__":
    unittest.main()
