import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from src.benchmark_analysis import (
    BenchmarkDefinitionIncompleteError,
    BenchmarkKind,
)
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.data_access import (
    DataAccessFetchError,
    DataAccessResponse,
)
from src.research_orchestration import (
    CAPABILITY_BENCHMARK_QUERY,
    CAPABILITY_DATASET_QUERY,
    CAPABILITY_EVENT_QUERY,
    CAPABILITY_EXTERNAL_OFFICIAL_WEB,
    CAPABILITY_OFFICIAL_DOCUMENT_QUERY,
    CAPABILITY_PEER_QUERY,
    CAPABILITY_PORTFOLIO_QUERY,
    EvidenceTier,
    ExplorationGapKind,
    ExplorationQuestion,
    ExplorationLedgerEntryKind,
    ExplorationStatus,
    ExplorationTrack,
    HypothesisImpact,
    HypothesisImpactRecord,
    HypothesisRole,
    HypothesisStatus,
    PersistentResearchExplorationService,
    QuestionStatus,
    ResearchExplorationRound,
    ResearchFinding,
    ResearchFindingKind,
    ResearchExplorationQueryService,
    ResearchQueryOutcome,
    ResearchQueryPlan,
    ResearchQueryRoute,
    ResearchQueryRouter,
    ResearchRunRequest,
    ResearchRunStatus,
    ResearchWorkflowCoordinator,
    default_capability_registry,
)
from src.research_orchestration.query_routing import (
    DataAccessQueryHandler,
    BenchmarkQueryHandler,
    EventKnowledgeQueryHandler,
    ExternalOfficialWebQueryHandler,
    PortfolioRiskQueryHandler,
    ResearchEvidenceQueryHandler,
    ResearchQueryHandlerOutput,
)
from src.research_orchestration.sqlite_repository import (
    SQLiteResearchWorkflowRepository,
)
from src.research_state import SQLiteResearchStateRepository
from src.research_workflow import (
    PROFILE_ORDINARY_COMPANY,
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    DecisionLane,
    ResearchExecutionGap,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from src.user_space import (
    SQLiteUserSpaceRepository,
    UserSpaceService,
)


class PersistentResearchExplorationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = (
            Path(self.temp_dir.name) / "exploration.sqlite3"
        )
        self.user_space = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        self.context = self.user_space.default_context()
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
        self.task = ResearchTaskSpec(
            task_id="research:test:exploration",
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-23",
        )
        self.workflow_coordinator = (
            self._new_workflow_coordinator()
        )
        self.exploration = self._new_exploration_service(
            self.workflow_coordinator,
            self.user_space,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_start_links_handoff_and_preserves_user_isolation(self):
        workflow_run = self._create_workflow(
            "workflow:exploration:start"
        )

        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
            session_id="exploration:test",
        )

        linked = self.workflow_coordinator.get_workflow_run(
            workflow_run.workflow_run_id
        )
        assert linked is not None
        self.assertEqual(
            linked.exploration_session_id,
            session.session_id,
        )
        self.assertEqual(
            session.workflow_run_id,
            workflow_run.workflow_run_id,
        )
        self.assertEqual(session.handoff_id, workflow_run.handoff_id)
        self.assertEqual(
            session.decision_lanes,
            (DecisionLane.SUITABILITY,),
        )
        self.assertTrue(
            any(question.blocking for question in session.questions)
        )
        self.assertTrue(
            any(
                hypothesis.role
                is HypothesisRole.COUNTERARGUMENT
                for hypothesis in session.hypotheses
            )
        )
        ledger = self.exploration.list_ledger(
            self.context,
            session_id=session.session_id,
        )
        self.assertEqual(
            [item.entry_kind for item in ledger],
            [ExplorationLedgerEntryKind.SESSION_STARTED.value],
        )

        other = self.user_space.context_for_user(
            self.user_space.create_user("Other").user_id
        )
        self.assertIsNone(
            self.exploration.get_session(
                other,
                session_id=session.session_id,
            )
        )
        self.assertEqual(
            self.exploration.list_ledger(
                other,
                session_id=session.session_id,
            ),
            (),
        )
        with self.assertRaisesRegex(
            ValueError,
            "cannot be overwritten",
        ):
            self.workflow_coordinator.record_result(
                ResearchRunRequest(
                    task=self.task,
                    workflow_run_id=workflow_run.workflow_run_id,
                ),
                self._result(ResearchRunStatus.COMPLETE),
            )

    def test_append_only_history_and_session_survive_restart(self):
        workflow_run = self._create_workflow(
            "workflow:exploration:restart"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
            session_id="exploration:restart",
        )
        for question in session.questions:
            if not question.blocking:
                continue
            session = self.exploration.answer_question(
                self.context,
                session_id=session.session_id,
                question_id=question.question_id,
                answer="已验证或正式判定当前不可用。",
                evidence_refs=("evidence:question",),
            )

        thesis = next(
            item
            for item in session.hypotheses
            if item.role is HypothesisRole.THESIS
        )
        for status in (
            HypothesisStatus.SUPPORTED,
            HypothesisStatus.WEAKENED,
            HypothesisStatus.REJECTED,
        ):
            thesis = replace(
                thesis,
                status=status,
                supporting_evidence_refs=(
                    "evidence:thesis:support",
                ),
                counterevidence_refs=(
                    "evidence:thesis:counter",
                ),
                falsification_conditions=(
                    "核心收益来源无法持续。",
                ),
            )
            session = self.exploration.update_hypothesis(
                self.context,
                session_id=session.session_id,
                hypothesis=thesis,
                reason=f"Moved to {status.value}.",
            )
        for other_thesis in (
            item
            for item in session.hypotheses
            if (
                item.role is HypothesisRole.THESIS
                and item.hypothesis_id != thesis.hypothesis_id
            )
        ):
            session = self.exploration.update_hypothesis(
                self.context,
                session_id=session.session_id,
                hypothesis=replace(
                    other_thesis,
                    status=HypothesisStatus.SUPPORTED,
                    supporting_evidence_refs=(
                        "evidence:other-thesis",
                    ),
                    falsification_conditions=(
                        "该补充假设的预期观察不再出现。",
                    ),
                ),
                reason="Supplementary thesis evaluated.",
            )

        counterargument = next(
            item
            for item in session.hypotheses
            if item.role is HypothesisRole.COUNTERARGUMENT
        )
        session = self.exploration.update_hypothesis(
            self.context,
            session_id=session.session_id,
            hypothesis=replace(
                counterargument,
                status=HypothesisStatus.WEAKENED,
                counterevidence_refs=(
                    "evidence:counterargument",
                ),
            ),
            reason="Independent evidence weakened the counterargument.",
        )
        impact = HypothesisImpactRecord(
            hypothesis_id=thesis.hypothesis_id,
            impact=HypothesisImpact.REJECTS,
            rationale="Counterevidence invalidates persistence.",
        )
        session = self.exploration.add_round(
            self.context,
            session_id=session.session_id,
            round_result=(
                self._round(
                    impact,
                    as_of_date=self.task.as_of_date,
                )
            ),
        )
        session = self.exploration.mark_ready(
            self.context,
            session_id=session.session_id,
        )
        self.assertIs(
            session.status,
            ExplorationStatus.READY_FOR_REPORT,
        )

        history = [
            item.new_status
            for item in session.hypothesis_history
            if item.hypothesis_id == thesis.hypothesis_id
        ]
        self.assertEqual(
            history,
            [
                HypothesisStatus.ACTIVE,
                HypothesisStatus.SUPPORTED,
                HypothesisStatus.WEAKENED,
                HypothesisStatus.REJECTED,
            ],
        )
        ledger = self.exploration.list_ledger(
            self.context,
            session_id=session.session_id,
        )
        self.assertEqual(
            [item.sequence_no for item in ledger],
            list(range(1, len(ledger) + 1)),
        )
        self.assertEqual(
            ledger[-1].entry_kind,
            ExplorationLedgerEntryKind.READY_FOR_REPORT.value,
        )

        restarted_users = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        restarted_context = restarted_users.default_context()
        restarted_coordinator = (
            self._new_workflow_coordinator()
        )
        restarted = self._new_exploration_service(
            restarted_coordinator,
            restarted_users,
        )
        recovered = restarted.resume_for_workflow(
            restarted_context,
            workflow_run_id=workflow_run.workflow_run_id,
        )

        self.assertEqual(recovered, session)
        self.assertEqual(
            restarted.start_from_handoff(
                restarted_context,
                workflow_run_id=workflow_run.workflow_run_id,
            ),
            session,
        )

    def test_ready_gate_requires_tracks_falsification_and_counterargument(self):
        workflow_run = self._create_workflow(
            "workflow:exploration:gates"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        with self.assertRaisesRegex(ValueError, "round"):
            self.exploration.mark_ready(
                self.context,
                session_id=session.session_id,
            )

        thesis = next(
            item
            for item in session.hypotheses
            if item.role is HypothesisRole.THESIS
        )
        session = self.exploration.add_round(
            self.context,
            session_id=session.session_id,
            round_result=self._round(
                HypothesisImpactRecord(
                    hypothesis_id=thesis.hypothesis_id,
                    impact=HypothesisImpact.SUPPORTS,
                    rationale="One finding supports the thesis.",
                ),
                as_of_date=self.task.as_of_date,
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "blocking questions",
        ):
            self.exploration.mark_ready(
                self.context,
                session_id=session.session_id,
            )
        for question in session.questions:
            if question.blocking:
                session = self.exploration.defer_question(
                    self.context,
                    session_id=session.session_id,
                    question_id=question.question_id,
                    reason="正式不可回答，保留缺口。",
                )
        with self.assertRaisesRegex(
            ValueError,
            "falsification conditions",
        ):
            self.exploration.mark_ready(
                self.context,
                session_id=session.session_id,
            )

    def test_blocked_workflow_cannot_start_exploration(self):
        workflow_run, handoff = (
            self.workflow_coordinator.record_result(
                ResearchRunRequest(
                    task=self.task,
                    workflow_run_id="workflow:exploration:blocked",
                ),
                self._result(
                    ResearchRunStatus.BLOCKED,
                    execution_gaps=(
                        ResearchExecutionGap(
                            module="data_access",
                            code="required_data_missing",
                            message="Required data are missing.",
                            blocking=True,
                        ),
                    ),
                ),
            )
        )
        self.assertIsNone(handoff)

        with self.assertRaisesRegex(ValueError, "no baseline handoff"):
            self.exploration.start_from_handoff(
                self.context,
                workflow_run_id=workflow_run.workflow_run_id,
            )

    def test_ledger_rows_cannot_be_updated(self):
        workflow_run = self._create_workflow(
            "workflow:exploration:append-only"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        ledger = self.exploration.list_ledger(
            self.context,
            session_id=session.session_id,
        )

        with sqlite3.connect(self.db_path) as conn:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "append-only",
            ):
                conn.execute(
                    """
                    UPDATE research_exploration_ledger_entries
                    SET entry_kind = 'rewritten'
                    WHERE entry_id = ?
                    """,
                    [ledger[0].entry_id],
                )

    def test_question_bound_query_updates_session_and_ledger(self):
        workflow_run = self._create_workflow(
            "workflow:query:success"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        question = next(
            item for item in session.questions if item.blocking
        )
        query_service = ResearchExplorationQueryService(
            capability_registry=default_capability_registry(),
            router=ResearchQueryRouter(
                {
                    ResearchQueryRoute.DATA_ACCESS: (
                        _StaticQueryHandler(
                            ResearchQueryHandlerOutput(
                                outcome=(
                                    ResearchQueryOutcome.SUCCESS
                                ),
                                summary="Expected records were found.",
                                evidence_refs=(
                                    "evidence:query:success",
                                ),
                                evidence_tier=EvidenceTier.DERIVED,
                            )
                        )
                    )
                }
            ),
            exploration=self.exploration,
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=question.question_id,
            query_id="query:success",
            capability_id=CAPABILITY_DATASET_QUERY,
            route=ResearchQueryRoute.DATA_ACCESS,
        )

        execution = query_service.execute(
            self.context,
            session_id=session.session_id,
            plan=plan,
        )

        updated_question = next(
            item
            for item in execution.session.questions
            if item.question_id == question.question_id
        )
        self.assertIs(updated_question.status, QuestionStatus.OPEN)
        self.assertEqual(
            updated_question.query_ids,
            (plan.query_id,),
        )
        self.assertEqual(
            updated_question.last_query_outcome,
            ResearchQueryOutcome.SUCCESS.value,
        )
        self.assertEqual(
            execution.result.findings[0].track,
            question.track,
        )
        ledger = self.exploration.list_ledger(
            self.context,
            session_id=session.session_id,
        )
        self.assertEqual(
            ledger[-1].entry_kind,
            ExplorationLedgerEntryKind.QUERY_EXECUTED.value,
        )
        self.assertEqual(
            ledger[-1].payload["plan"]["expected_evidence"],
            ["requested public records"],
        )
        with self.assertRaisesRegex(
            ValueError,
            "already recorded",
        ):
            query_service.execute(
                self.context,
                session_id=session.session_id,
                plan=plan,
            )
        answered = self.exploration.answer_question(
            self.context,
            session_id=session.session_id,
            question_id=question.question_id,
            answer=(
                "Agent interpreted the query evidence against "
                "the completion criteria."
            ),
            evidence_refs=execution.result.evidence_refs,
        )
        restarted_users = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        restarted = self._new_exploration_service(
            self._new_workflow_coordinator(),
            restarted_users,
        )
        self.assertEqual(
            restarted.resume_for_workflow(
                restarted_users.default_context(),
                workflow_run_id=workflow_run.workflow_run_id,
            ),
            answered,
        )
        with self.assertRaisesRegex(
            ValueError,
            "no longer open",
        ):
            query_service.execute(
                self.context,
                session_id=session.session_id,
                plan=replace(plan, query_id="query:after-answer"),
            )

    def test_data_query_distinguishes_business_empty_and_remote_failure(self):
        workflow_run = self._create_workflow(
            "workflow:query:classification"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        question = session.questions[0]
        plan = self._plan(
            session_id=session.session_id,
            question_id=question.question_id,
            query_id="query:classification",
            capability_id=CAPABILITY_DATASET_QUERY,
            route=ResearchQueryRoute.DATA_ACCESS,
            parameters={
                "dataset": "stock_daily_bars",
                "start_date": "2026-08-01",
                "end_date": "2026-12-31",
            },
        )
        empty_service = _FakeDataAccess(
            response=DataAccessResponse(
                asset=self.asset,
                dataset="stock_daily_bars",
                records=(),
                complete=True,
                metadata={"empty_sources": ["source-a"]},
            )
        )
        empty_handler = DataAccessQueryHandler(empty_service)
        remote_handler = DataAccessQueryHandler(
            _FakeDataAccess(
                error=DataAccessFetchError(
                    "all providers failed",
                    {"source-a": "timeout"},
                )
            )
        )

        business_empty = empty_handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )
        remote_failure = remote_handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            business_empty.outcome,
            ResearchQueryOutcome.BUSINESS_EMPTY,
        )
        self.assertIs(
            business_empty.gap_kind,
            ExplorationGapKind.BUSINESS_EMPTY,
        )
        self.assertEqual(
            empty_service.request.end_date,
            self.task.as_of_date,
        )
        self.assertIs(
            remote_failure.outcome,
            ResearchQueryOutcome.REMOTE_FAILURE,
        )
        self.assertIs(
            remote_failure.gap_kind,
            ExplorationGapKind.REMOTE_FAILURE,
        )

    def test_question_required_capability_is_enforced_before_routing(self):
        workflow_run = self._create_workflow(
            "workflow:query:required-capability"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        session = self.exploration.add_question(
            self.context,
            session_id=session.session_id,
            question=ExplorationQuestion(
                question_id="question:event-only",
                text="哪些正式事件会改变当前判断？",
                reason="Test explicit capability binding.",
                blocking=False,
                track=ExplorationTrack.GENERAL,
                required_capability=CAPABILITY_EVENT_QUERY,
            ),
        )
        handler = _CountingQueryHandler()
        query_service = ResearchExplorationQueryService(
            capability_registry=default_capability_registry(),
            router=ResearchQueryRouter(
                {
                    ResearchQueryRoute.DATA_ACCESS: handler,
                }
            ),
            exploration=self.exploration,
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id="question:event-only",
            query_id="query:wrong-capability",
            capability_id=CAPABILITY_DATASET_QUERY,
            route=ResearchQueryRoute.DATA_ACCESS,
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match question",
        ):
            query_service.execute(
                self.context,
                session_id=session.session_id,
                plan=plan,
            )
        self.assertEqual(handler.call_count, 0)

    def test_blocking_question_cannot_close_without_evidence(self):
        workflow_run = self._create_workflow(
            "workflow:question:evidence-gate"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        question = next(
            item for item in session.questions if item.blocking
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires evidence_refs",
        ):
            self.exploration.answer_question(
                self.context,
                session_id=session.session_id,
                question_id=question.question_id,
                answer="Unsupported assertion without evidence.",
            )

    def test_unsupported_route_creates_capability_gap(self):
        self.task = replace(
            self.task,
            profile_id=PROFILE_EXCHANGE_FUND,
        )
        workflow_run = self._create_workflow(
            "workflow:query:unsupported"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        question = session.questions[0]
        query_service = ResearchExplorationQueryService(
            capability_registry=default_capability_registry(),
            router=ResearchQueryRouter({}),
            exploration=self.exploration,
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=question.question_id,
            query_id="query:unsupported",
            capability_id=CAPABILITY_PEER_QUERY,
            route=ResearchQueryRoute.PEER_ANALYSIS,
        )

        execution = query_service.execute(
            self.context,
            session_id=session.session_id,
            plan=plan,
        )

        self.assertIs(
            execution.result.outcome,
            ResearchQueryOutcome.UNSUPPORTED,
        )
        self.assertIsNotNone(execution.result.gap)
        assert execution.result.gap is not None
        self.assertIs(
            execution.result.gap.kind,
            ExplorationGapKind.CAPABILITY,
        )
        updated_question = next(
            item
            for item in execution.session.questions
            if item.question_id == question.question_id
        )
        self.assertIs(
            updated_question.status,
            QuestionStatus.DEFERRED,
        )
        self.assertEqual(
            execution.session.query_gaps,
            (execution.result.gap,),
        )

    def test_external_official_source_is_registered_without_fetching(self):
        workflow_run = self._create_workflow(
            "workflow:query:external"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        question = session.questions[0]
        query_service = ResearchExplorationQueryService(
            capability_registry=default_capability_registry(),
            router=ResearchQueryRouter(
                {
                    ResearchQueryRoute.EXTERNAL_OFFICIAL_WEB: (
                        ExternalOfficialWebQueryHandler()
                    )
                }
            ),
            exploration=self.exploration,
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=question.question_id,
            query_id="query:external",
            capability_id=CAPABILITY_EXTERNAL_OFFICIAL_WEB,
            route=ResearchQueryRoute.EXTERNAL_OFFICIAL_WEB,
            parameters={
                "url": "https://example.gov.cn/disclosure/1",
                "title": "官方披露",
                "source_name": "示例监管机构",
                "published_at": "2026-08-20",
                "content_excerpt": "经 Agent 阅读的官方原文摘录。",
            },
        )

        execution = query_service.execute(
            self.context,
            session_id=session.session_id,
            plan=plan,
        )

        self.assertIs(
            execution.result.outcome,
            ResearchQueryOutcome.SUCCESS,
        )
        self.assertTrue(
            execution.result.evidence_refs[0].startswith(
                "evidence:external:"
            )
        )
        self.assertTrue(
            execution.result.metadata["registration_only"]
        )

    def test_event_query_clamps_cutoff_and_uses_derived_tier(self):
        workflow_run = self._create_workflow(
            "workflow:query:event-cutoff"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        service = _FakeEventService()
        handler = EventKnowledgeQueryHandler(service)
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:event-cutoff",
            capability_id=CAPABILITY_EVENT_QUERY,
            route=ResearchQueryRoute.EVENT_KNOWLEDGE,
            parameters={
                "end_date": "2026-12-31",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertEqual(
            service.end_date,
            self.task.as_of_date,
        )
        self.assertIs(
            output.evidence_tier,
            EvidenceTier.DERIVED,
        )

    def test_benchmark_data_access_failure_is_remote_failure(self):
        workflow_run = self._create_workflow(
            "workflow:query:benchmark-remote"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        handler = BenchmarkQueryHandler(
            _FailingBenchmarkService()
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:benchmark-remote",
            capability_id=CAPABILITY_BENCHMARK_QUERY,
            route=ResearchQueryRoute.BENCHMARK_ANALYSIS,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-08-23",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.outcome,
            ResearchQueryOutcome.REMOTE_FAILURE,
        )

    def test_open_fund_benchmark_uses_composite_public_route(self):
        workflow_run = self._create_workflow(
            "workflow:query:open-fund-benchmark"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        session = replace(
            session,
            task=replace(
                session.task,
                profile_id=PROFILE_OPEN_FUND,
            ),
        )
        benchmark_service = _OpenFundBenchmarkService()
        handler = BenchmarkQueryHandler(
            benchmark_service,
            open_fund_data_access=_OpenFundDataAccess(),
            open_fund_research=_OpenFundResearchService(),
            open_fund_resolver=_OpenFundResolver(),
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:open-fund-benchmark",
            capability_id=CAPABILITY_BENCHMARK_QUERY,
            route=ResearchQueryRoute.BENCHMARK_ANALYSIS,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-08-23",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.outcome,
            ResearchQueryOutcome.SUCCESS,
        )
        self.assertEqual(
            benchmark_service.composite_call_count,
            1,
        )

    def test_open_fund_malformed_definition_is_classified(self):
        workflow_run = self._create_workflow(
            "workflow:query:open-fund-definition"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        session = replace(
            session,
            task=replace(
                session.task,
                profile_id=PROFILE_OPEN_FUND,
            ),
        )
        handler = BenchmarkQueryHandler(
            _OpenFundBenchmarkService(),
            open_fund_data_access=_OpenFundDataAccess(),
            open_fund_research=_OpenFundResearchService(),
            open_fund_resolver=_FailingOpenFundResolver(),
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:open-fund-definition",
            capability_id=CAPABILITY_BENCHMARK_QUERY,
            route=ResearchQueryRoute.BENCHMARK_ANALYSIS,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-08-23",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.outcome,
            ResearchQueryOutcome.FAILED,
        )
        self.assertEqual(
            output.gap_code,
            "benchmark_definition_incomplete",
        )

    def test_open_fund_nav_remote_failure_is_not_business_empty(self):
        workflow_run = self._create_workflow(
            "workflow:query:open-fund-nav-remote"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        session = replace(
            session,
            task=replace(
                session.task,
                profile_id=PROFILE_OPEN_FUND,
            ),
        )
        handler = BenchmarkQueryHandler(
            _OpenFundBenchmarkService(),
            open_fund_data_access=_OpenFundDataAccess(
                _FakeNavResponse(
                    records=(),
                    provider_errors={"fund-source": "timeout"},
                )
            ),
            open_fund_research=_OpenFundResearchService(),
            open_fund_resolver=_OpenFundResolver(),
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:open-fund-nav-remote",
            capability_id=CAPABILITY_BENCHMARK_QUERY,
            route=ResearchQueryRoute.BENCHMARK_ANALYSIS,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-08-23",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.outcome,
            ResearchQueryOutcome.REMOTE_FAILURE,
        )

    def test_open_fund_unresolved_definition_is_classified(self):
        workflow_run = self._create_workflow(
            "workflow:query:open-fund-unresolved"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        session = replace(
            session,
            task=replace(
                session.task,
                profile_id=PROFILE_OPEN_FUND,
            ),
        )
        handler = BenchmarkQueryHandler(
            _IncompleteOpenFundBenchmarkService(),
            open_fund_data_access=_OpenFundDataAccess(),
            open_fund_research=_OpenFundResearchService(),
            open_fund_resolver=_OpenFundResolver(),
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:open-fund-unresolved",
            capability_id=CAPABILITY_BENCHMARK_QUERY,
            route=ResearchQueryRoute.BENCHMARK_ANALYSIS,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-08-23",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.outcome,
            ResearchQueryOutcome.FAILED,
        )
        self.assertEqual(
            output.gap_code,
            "benchmark_definition_incomplete",
        )

    def test_document_query_preserves_conservative_evidence_tier(self):
        workflow_run = self._create_workflow(
            "workflow:query:evidence-tier"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        service = _FakeEvidenceService("third_party")
        handler = ResearchEvidenceQueryHandler(service)
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:evidence-tier",
            capability_id=CAPABILITY_OFFICIAL_DOCUMENT_QUERY,
            route=ResearchQueryRoute.RESEARCH_EVIDENCE,
            parameters={
                "start_date": "2026-08-01",
                "end_date": "2026-12-31",
            },
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertIs(
            output.evidence_tier,
            EvidenceTier.SECONDARY,
        )
        self.assertEqual(
            service.parameters["end_date"],
            self.task.as_of_date,
        )

    def test_portfolio_query_excludes_post_cutoff_snapshots(self):
        workflow_run = self._create_workflow(
            "workflow:query:portfolio-cutoff"
        )
        session = self.exploration.start_from_handoff(
            self.context,
            workflow_run_id=workflow_run.workflow_run_id,
        )
        handler = PortfolioRiskQueryHandler(
            _FakePortfolioService()
        )
        plan = self._plan(
            session_id=session.session_id,
            question_id=session.questions[0].question_id,
            query_id="query:portfolio-cutoff",
            capability_id=CAPABILITY_PORTFOLIO_QUERY,
            route=ResearchQueryRoute.PORTFOLIO_RISK,
            parameters={"operation": "list_snapshots"},
        )

        output = handler.execute(
            plan=plan,
            session=session,
            context=self.context,
        )

        self.assertEqual(
            output.metadata["snapshot_ids"],
            ["snapshot:past"],
        )

    def _create_workflow(self, workflow_run_id):
        workflow_run, handoff = (
            self.workflow_coordinator.record_result(
                ResearchRunRequest(
                    task=self.task,
                    workflow_run_id=workflow_run_id,
                    decision_brief_id="decision-brief:test",
                    decision_lanes=(DecisionLane.SUITABILITY,),
                ),
                self._result(ResearchRunStatus.COMPLETE),
            )
        )
        self.assertIsNotNone(handoff)
        return workflow_run

    def _result(
        self,
        status,
        *,
        execution_gaps=(),
    ):
        report = (
            None
            if status is ResearchRunStatus.BLOCKED
            else SimpleNamespace(
                fact_sections=(
                    SimpleNamespace(section_id="identity"),
                ),
                gaps=(),
            )
        )
        return SimpleNamespace(
            task=self.task,
            status=status,
            report_path=Path("report") / "baseline.md",
            execution=SimpleNamespace(
                gaps=execution_gaps,
                phases=(),
            ),
            report=report,
            review=SimpleNamespace(findings=()),
        )

    def _round(self, impact, *, as_of_date):
        finding = ResearchFinding(
            finding_id=f"finding:{_impact_suffix(impact)}",
            text="结构化发现影响核心论文。",
            kind=ResearchFindingKind.FACT,
            as_of_date=as_of_date,
            track=ExplorationTrack.SUITABILITY,
            evidence_refs=("evidence:round",),
            source_tier=EvidenceTier.PRIMARY,
            hypothesis_impacts=(impact,),
        )
        return ResearchExplorationRound(
            round_id=f"round:{_impact_suffix(impact)}",
            objective="验证适配性论文",
            queries=("官方资料查询",),
            findings=(finding.text,),
            evidence_refs=finding.evidence_refs,
            structured_findings=(finding,),
            next_step="继续验证反方或准备报告门禁。",
        )

    def _plan(
        self,
        *,
        session_id,
        question_id,
        query_id,
        capability_id,
        route,
        parameters=None,
    ):
        return ResearchQueryPlan(
            query_id=query_id,
            session_id=session_id,
            question_id=question_id,
            capability_id=capability_id,
            route=route,
            expected_evidence=("requested public records",),
            completion_criteria=(
                "Evidence directly addresses the question."
            ),
            as_of_date=self.task.as_of_date,
            parameters=parameters or {},
        )

    def _new_workflow_coordinator(self):
        return ResearchWorkflowCoordinator(
            master_data=self.master_data,
            capability_registry=default_capability_registry(),
            repository=SQLiteResearchWorkflowRepository(
                self.db_path
            ),
            exploration_repository=SQLiteResearchStateRepository(
                self.db_path
            ),
        )

    def _new_exploration_service(
        self,
        coordinator,
        user_space,
    ):
        return PersistentResearchExplorationService(
            repository=SQLiteResearchStateRepository(
                self.db_path
            ),
            workflow_coordinator=coordinator,
            user_space=user_space,
        )


def _impact_suffix(impact):
    return f"{impact.hypothesis_id}:{impact.impact.value}"


class _StaticQueryHandler:
    def __init__(self, output):
        self.output = output

    def execute(self, **kwargs):
        del kwargs
        return self.output


class _CountingQueryHandler:
    def __init__(self):
        self.call_count = 0

    def execute(self, **kwargs):
        del kwargs
        self.call_count += 1
        raise AssertionError("handler must not be called")


class _FakeDataAccess:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def get(self, request):
        self.request = request
        if self.error is not None:
            raise self.error
        return self.response


class _FakeEventService:
    def __init__(self):
        self.end_date = None

    def list_events(
        self,
        asset_id,
        *,
        start_date=None,
        end_date=None,
        event_type=None,
    ):
        del asset_id, start_date, event_type
        self.end_date = end_date
        return [_FakeEvent("event:one")]


class _FakeEvent:
    def __init__(self, event_id):
        self.event_id = event_id


class _FailingBenchmarkService:
    def compare(self, **kwargs):
        del kwargs
        raise DataAccessFetchError("benchmark provider timeout")


class _OpenFundBenchmarkService:
    def __init__(self):
        self.composite_call_count = 0

    def compare_composite(self, **kwargs):
        del kwargs
        self.composite_call_count += 1
        return _FakeBenchmarkResult()


class _IncompleteOpenFundBenchmarkService:
    def compare_composite(self, **kwargs):
        del kwargs
        raise BenchmarkDefinitionIncompleteError(
            "benchmark definition has unresolved components"
        )


class _OpenFundDataAccess:
    def __init__(self, response=None):
        self.response = response or _FakeNavResponse()

    def get(self, request):
        del request
        return self.response


class _OpenFundResearchService:
    def get_open_fund_profile_snapshot(self, **kwargs):
        del kwargs
        return _FakeOpenFundProfile()


class _OpenFundResolver:
    def resolve(self, profile):
        del profile
        return object()


class _FailingOpenFundResolver:
    def resolve(self, profile):
        del profile
        raise ValueError("unparseable benchmark weights")


class _FakeNavResponse:
    def __init__(
        self,
        *,
        records=None,
        provider_errors=None,
    ):
        self.records = (
            records
            if records is not None
            else (
                {
                    "trade_date": "2026-08-20",
                    "unit_nav": 1.0,
                },
            )
        )
        self.sources = ("official:fund",)
        self.complete = not provider_errors
        self.stale = False
        self.error = None
        self.metadata = {
            "provider_errors": provider_errors or {},
        }


class _FakeOpenFundProfile:
    benchmark_text = "80%×指数 + 20%×现金"


class _FakeBenchmarkResult:
    benchmark_asset_id = "composite:fund"
    benchmark_kind = BenchmarkKind.COMPOSITE
    aligned = type(
        "_Aligned",
        (),
        {"dates": ("2026-08-20",)},
    )()
    metrics = ()


class _FakeEvidenceService:
    def __init__(self, tier):
        self.tier = tier
        self.parameters = None

    def get_bundle(self, **kwargs):
        self.parameters = kwargs
        return _FakeEvidenceBundle(self.tier)


class _FakeEvidenceBundle:
    def __init__(self, tier):
        self.evidence = (
            _FakeEvidenceItem("evidence:third-party", tier),
        )
        self.events = ()
        self.document_states = ()
        self.sources = ("source:test",)
        self.complete = True
        self.errors = {}


class _FakeEvidenceItem:
    def __init__(self, evidence_id, tier):
        self.evidence_id = evidence_id
        self.tier = _FakeTier(tier)


class _FakeTier:
    def __init__(self, value):
        self.value = value


class _FakePortfolioService:
    def list_snapshots(self, context):
        del context
        return [
            _FakeSnapshot("snapshot:past", "2026-08-20"),
            _FakeSnapshot("snapshot:future", "2026-08-25"),
        ]


class _FakeSnapshot:
    def __init__(self, snapshot_id, as_of_date):
        self.snapshot_id = snapshot_id
        self.as_of_date = as_of_date


if __name__ == "__main__":
    unittest.main()
