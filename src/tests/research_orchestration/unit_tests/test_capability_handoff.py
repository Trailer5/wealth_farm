import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_acceptance import ResearchGap
from src.research_orchestration import (
    DEFAULT_EXECUTION_PROFILES,
    HandoffGapSource,
    ResearchQueryRoute,
    ResearchRunRequest,
    ResearchRunStatus,
    ResearchWorkflowCoordinator,
    create_default_container,
    default_capability_registry,
)
from src.research_orchestration.review import ReviewFinding
from src.research_orchestration.sqlite_repository import (
    SQLiteResearchWorkflowRepository,
)
from src.research_workflow import (
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionContextRequirement,
    DecisionLane,
    ResearchExecutionGap,
    ResearchHorizon,
    ResearchPhaseStatus,
    ResearchQuestionType,
    ResearchTaskSpec,
)


class ResearchCapabilityRegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = default_capability_registry()

    def test_five_execution_profiles_expose_decision_capabilities(self):
        self.assertEqual(
            set(self.registry.list_profile_ids()),
            set(DEFAULT_EXECUTION_PROFILES),
        )

        sizing = self.registry.resolve(
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=DecisionLane.POSITION_SIZING,
        )

        self.assertTrue(sizing.question_supported)
        self.assertIn(
            DecisionContextRequirement.INVESTMENT_MANDATE,
            sizing.required_user_context,
        )
        self.assertIn(
            DecisionContextRequirement.PORTFOLIO_CONSTRAINT_SNAPSHOT,
            sizing.required_user_context,
        )
        self.assertIn(
            "personalized_position_plan",
            sizing.automated_capabilities,
        )
        self.assertNotIn(
            "personalized_position_plan",
            sizing.unsupported_capabilities,
        )

    def test_open_fund_reports_unsupported_baseline_question(self):
        capability = self.registry.resolve(
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=DecisionLane.SUITABILITY,
        )

        self.assertFalse(capability.question_supported)
        self.assertIn(
            "baseline_question:investment",
            capability.unsupported_capabilities,
        )
        sizing = self.registry.resolve(
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=DecisionLane.POSITION_SIZING,
        )
        self.assertNotIn(
            "personalized_position_plan",
            sizing.automated_capabilities,
        )
        self.assertIn(
            "open_fund_position_sizing",
            sizing.unsupported_capabilities,
        )

    def test_suitability_assessment_and_final_review_are_declared(self):
        capability = self.registry.resolve(
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=DecisionLane.SUITABILITY,
        )

        self.assertIn(
            "structured_suitability_assessment",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "structured_suitability_assessment",
            capability.unsupported_capabilities,
        )
        self.assertIn(
            "programmatic_final_decision_review",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "programmatic_final_decision_review",
            capability.unsupported_capabilities,
        )

    def test_entry_plan_and_final_review_are_declared(self):
        capability = self.registry.resolve(
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.TIMING,
            decision_lane=DecisionLane.TIMING,
        )

        self.assertIn(
            "structured_entry_plan",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "structured_entry_plan",
            capability.unsupported_capabilities,
        )
        self.assertIn(
            "programmatic_final_decision_review",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "programmatic_final_decision_review",
            capability.unsupported_capabilities,
        )

    def test_position_plan_and_final_review_are_declared(self):
        capability = self.registry.resolve(
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            decision_lane=DecisionLane.POSITION_SIZING,
        )

        self.assertIn(
            "personalized_position_plan",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "personalized_position_plan",
            capability.unsupported_capabilities,
        )
        self.assertIn(
            "programmatic_final_decision_review",
            capability.automated_capabilities,
        )
        self.assertNotIn(
            "programmatic_final_decision_review",
            capability.unsupported_capabilities,
        )

    def test_every_profile_question_lane_combination_is_queryable(self):
        lanes = (None, *tuple(DecisionLane))

        for profile_id in DEFAULT_EXECUTION_PROFILES:
            for question_type in ResearchQuestionType:
                for decision_lane in lanes:
                    with self.subTest(
                        profile_id=profile_id,
                        question_type=question_type,
                        decision_lane=decision_lane,
                    ):
                        capability = self.registry.resolve(
                            profile_id=profile_id,
                            question_type=question_type,
                            decision_lane=decision_lane,
                        )
                        self.assertEqual(
                            capability.profile_id,
                            profile_id,
                        )

    def test_declared_python_entrypoints_are_importable(self):
        entrypoints = {
            entrypoint
            for profile_id in self.registry.list_profile_ids()
            for entrypoint in self.registry.resolve(
                profile_id=profile_id,
                question_type=ResearchQuestionType.UNDERSTANDING,
                decision_lane=None,
            ).public_entrypoints
        }

        missing = []
        for entrypoint in sorted(entrypoints):
            module_name, attribute = entrypoint.rsplit(".", 1)
            module = importlib.import_module(module_name)
            if not hasattr(module, attribute):
                missing.append(entrypoint)
        self.assertEqual(missing, [])

    def test_default_container_registers_all_public_query_routes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            container = create_default_container(
                db_path=root / "query-routes.sqlite3",
                report_root=root / "report",
            )

            self.assertEqual(
                set(container.queries.router.handlers),
                set(ResearchQueryRoute),
            )
            self.assertIs(
                container.new_conversation_acceptance.planner,
                container.request_planner,
            )
            self.assertIsNotNone(
                container.suitability.resolve_policy(
                    PROFILE_ORDINARY_COMPANY
                )
            )
            self.assertIsNotNone(
                container.entry.resolve_policy(
                    PROFILE_ORDINARY_COMPANY
                )
            )
            self.assertIsNotNone(
                container.position.resolve_policy(
                    PROFILE_ORDINARY_COMPANY
                )
            )


class ResearchWorkflowCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "workflow.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master_data.register_asset(
            Asset(
                asset_id="stock:600519",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn",
                exchange="sh",
            ),
            source="unit_test",
            raw_symbol="600519",
        )
        self.repository = SQLiteResearchWorkflowRepository(
            self.db_path
        )
        self.coordinator = ResearchWorkflowCoordinator(
            master_data=self.master_data,
            capability_registry=default_capability_registry(),
            repository=self.repository,
        )
        self.task = ResearchTaskSpec(
            task_id="research:test:workflow",
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-23",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_and_partial_runs_create_persisted_handoffs(self):
        complete_run, complete_handoff = (
            self.coordinator.record_result(
                ResearchRunRequest(
                    task=self.task,
                    workflow_run_id="workflow:complete",
                    decision_brief_id="decision-brief:test",
                    decision_lanes=(DecisionLane.SUITABILITY,),
                ),
                self._result(ResearchRunStatus.COMPLETE),
            )
        )
        partial_run, partial_handoff = (
            self.coordinator.record_result(
                ResearchRunRequest(
                    task=self.task,
                    workflow_run_id="workflow:partial",
                    decision_lanes=(DecisionLane.SUITABILITY,),
                ),
                self._result(
                    ResearchRunStatus.PARTIAL,
                    execution_gaps=(
                        ResearchExecutionGap(
                            module="data_access",
                            code="peer_data_partial",
                            message="Peer coverage is incomplete.",
                            blocking=False,
                        ),
                    ),
                    report_gaps=(
                        ResearchGap(
                            module="research_acceptance",
                            code="alternative_missing",
                            message="Alternative is not yet compared.",
                            blocking=False,
                        ),
                    ),
                ),
            )
        )

        self.assertIsNotNone(complete_handoff)
        self.assertIsNotNone(partial_handoff)
        self.assertEqual(
            complete_run.decision_brief_id,
            "decision-brief:test",
        )
        assert partial_handoff is not None
        self.assertEqual(len(partial_handoff.partial_gaps), 2)
        self.assertEqual(
            {
                gap.source for gap in partial_handoff.partial_gaps
            },
            {
                HandoffGapSource.EXECUTION,
                HandoffGapSource.ACCEPTANCE,
            },
        )
        self.assertTrue(partial_handoff.capability_gaps)
        self.assertEqual(
            partial_handoff.phase_statuses[0].status,
            "success",
        )
        self.assertTrue(partial_handoff.candidate_questions)
        self.assertTrue(partial_handoff.candidate_hypotheses)

        restarted = ResearchWorkflowCoordinator(
            master_data=self.master_data,
            capability_registry=default_capability_registry(),
            repository=SQLiteResearchWorkflowRepository(
                self.db_path
            ),
        )
        self.assertEqual(
            restarted.get_workflow_run(
                partial_run.workflow_run_id
            ),
            partial_run,
        )
        self.assertEqual(
            restarted.get_handoff_for_run(
                partial_run.workflow_run_id
            ),
            partial_handoff,
        )

    def test_blocked_run_persists_state_without_handoff_or_session(self):
        workflow_run, handoff = self.coordinator.record_result(
            ResearchRunRequest(
                task=self.task,
                workflow_run_id="workflow:blocked",
                decision_lanes=(DecisionLane.SUITABILITY,),
            ),
            self._result(
                ResearchRunStatus.BLOCKED,
                execution_gaps=(
                    ResearchExecutionGap(
                        module="data_access",
                        code="bars_missing",
                        message="Required bars are unavailable.",
                        blocking=True,
                    ),
                ),
                review_findings=(
                    ReviewFinding(
                        code="blocking_gap",
                        message="Execution is blocked.",
                    ),
                ),
            ),
        )

        self.assertIsNone(handoff)
        self.assertIsNone(workflow_run.handoff_id)
        self.assertIsNone(workflow_run.exploration_session_id)
        self.assertEqual(
            set(workflow_run.blocking_gap_codes),
            {"bars_missing", "blocking_gap"},
        )
        self.assertEqual(len(workflow_run.blocking_gaps), 2)
        self.assertIsNone(
            self.coordinator.get_handoff_for_run(
                workflow_run.workflow_run_id
            )
        )

    def _result(
        self,
        status,
        *,
        execution_gaps=(),
        report_gaps=(),
        review_findings=(),
    ):
        report = (
            None
            if status is ResearchRunStatus.BLOCKED
            else SimpleNamespace(
                fact_sections=(
                    SimpleNamespace(section_id="identity"),
                ),
                gaps=report_gaps,
            )
        )
        return SimpleNamespace(
            task=self.task,
            status=status,
            report_path=Path("report") / "baseline.md",
            execution=SimpleNamespace(
                gaps=execution_gaps,
                phases=(
                    SimpleNamespace(
                        phase_id="identity",
                        status=ResearchPhaseStatus.SUCCESS,
                        summary="Identity resolved.",
                    ),
                ),
            ),
            report=report,
            review=SimpleNamespace(findings=review_findings),
        )


if __name__ == "__main__":
    unittest.main()
