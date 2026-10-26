import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

from src.event_name_discovery import (
    EventDirection,
    EventNameDiscoverySpec,
    EventPhrase,
    ExternalEventAnchor,
)
from src.research_acceptance import (
    AcceptanceStatus,
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    InvalidationCondition,
    REQUIRED_ACCEPTANCE_STEPS,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchFact,
    ResearchFactKind,
    ResearchFactSection,
    ResearchGap,
    ResearchScenario,
    ScenarioKind,
)
from src.research_orchestration import (
    DiscoveryOrchestrationPipeline,
    DiscoveryRunRequest,
    EvidenceTier,
    ExplorationQuestion,
    ExplorationStatus,
    HypothesisImpact,
    HypothesisImpactRecord,
    HypothesisRole,
    HypothesisStatus,
    ResearchExplorationRound,
    ResearchExplorationService,
    ResearchFinding,
    ResearchFindingKind,
    ResearchHypothesis,
    ResearchExecutionDispatcher,
    ResearchOrchestrationPipeline,
    ResearchReportRenderer,
    ResearchReportReviewer,
    ResearchRunRequest,
    ResearchRunStatus,
)
from src.research_orchestration.cli import build_parser, main
from src.research_orchestration.workflow import _baseline_chart_manifest
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from src.research_workflow import (
    CycleResearchExecutionOptions,
    ResearchExecutionGap,
    ResearchExecutionOptions,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchWindows,
)
from src.research_visualization import (
    ChartArtifact,
    ChartType,
    ReportArtifactService,
)


class ResearchOrchestrationTest(unittest.TestCase):
    def test_dispatcher_selects_profile_without_agent_choice(self):
        first = _FakeExecutionService()
        second = _FakeExecutionService()
        task = _task("profile-b")
        dispatcher = ResearchExecutionDispatcher(
            {"profile-a": first, "profile-b": second}
        )
        options = object()

        result = dispatcher.execute(task, options)

        self.assertIs(result.task, task)
        self.assertEqual(first.calls, [])
        self.assertEqual(second.calls, [(task, options)])

        unknown = dispatcher.execute(_task("profile-missing"))
        self.assertIs(
            unknown.status,
            ResearchExecutionStatus.BLOCKED,
        )
        self.assertEqual(
            unknown.gaps[0].code,
            "unknown_research_profile",
        )

    def test_discovery_pipeline_runs_one_batch_then_funnel_when_ready(self):
        preparation = _FakePreparationJobs("completed")
        discovery = _FakeDiscoveryService()
        pipeline = DiscoveryOrchestrationPipeline(
            preparation_jobs=preparation,
            discovery_service=discovery,
        )
        spec = SimpleNamespace(universe_id="csi300")

        result = pipeline.run(
            DiscoveryRunRequest(
                job_id="csi300-current",
                spec=spec,
                profile_by_asset_id={"asset:a": "ordinary"},
                batch_size=10,
            )
        )

        self.assertTrue(result.preparation_complete)
        self.assertEqual(result.discovery_run, "discovery-result")
        self.assertEqual(len(preparation.create_calls), 1)
        self.assertEqual(len(preparation.batch_calls), 1)
        self.assertEqual(discovery.calls, [spec])

        pending_discovery = _FakeDiscoveryService()
        pending = DiscoveryOrchestrationPipeline(
            preparation_jobs=_FakePreparationJobs("running"),
            discovery_service=pending_discovery,
        ).run(
            DiscoveryRunRequest(
                job_id="csi300-current",
                spec=spec,
                profile_by_asset_id={},
            )
        )
        self.assertFalse(pending.preparation_complete)
        self.assertIsNone(pending.discovery_run)
        self.assertEqual(pending_discovery.calls, [])

    def test_dispatcher_blocks_when_selected_service_needs_options(self):
        dispatcher = ResearchExecutionDispatcher(
            {"cycle": _RequiredOptionsExecutionService()}
        )

        result = dispatcher.execute(_task("cycle"))

        self.assertIs(
            result.status,
            ResearchExecutionStatus.BLOCKED,
        )
        self.assertEqual(
            result.gaps[0].code,
            "execution_options_required",
        )

    def test_agent_exploration_requires_rounds_and_resolved_blockers(self):
        service = ResearchExplorationService()
        session = service.start(
            session_id="session:test",
            task=_task("ordinary"),
            baseline_path=Path("report") / "baseline.md",
            hypotheses=(
                ResearchHypothesis(
                    "growth",
                    "盈利增长能够持续。",
                    falsification_conditions=(
                        "销量与提价无法共同维持。",
                    ),
                ),
                ResearchHypothesis(
                    "counter",
                    "增长主要来自渠道压货。",
                    role=HypothesisRole.COUNTERARGUMENT,
                ),
            ),
        )
        session = service.add_question(
            session,
            ExplorationQuestion(
                "pricing",
                "提价是否来自真实需求？",
                "决定增长质量",
                True,
            ),
        )
        with self.assertRaisesRegex(ValueError, "round"):
            service.mark_ready(session)

        session = service.answer_question(
            session,
            question_id="pricing",
            answer="渠道数据支持真实需求。",
            evidence_refs=("evidence:channel",),
        )
        session = service.add_round(
            session,
            ResearchExplorationRound(
                round_id="round-1",
                objective="验证增长质量",
                queries=("渠道价格", "销量结构"),
                findings=("提价与销量增长同时存在",),
                evidence_refs=("evidence:channel",),
                structured_findings=(
                    ResearchFinding(
                        finding_id="finding:growth",
                        text="提价与销量增长同时存在。",
                        kind=ResearchFindingKind.FACT,
                        as_of_date="2026-08-09",
                        evidence_refs=("evidence:channel",),
                        source_tier=EvidenceTier.PRIMARY,
                        hypothesis_impacts=(
                            HypothesisImpactRecord(
                                hypothesis_id="growth",
                                impact=HypothesisImpact.SUPPORTS,
                                rationale="量价同时改善。",
                            ),
                        ),
                    ),
                ),
                next_step="评估最强反方。",
            ),
        )
        session = service.update_hypothesis(
            session,
            ResearchHypothesis(
                "growth",
                "盈利增长能够持续。",
                status=HypothesisStatus.SUPPORTED,
                supporting_evidence_refs=("evidence:channel",),
                falsification_conditions=(
                    "销量与提价无法共同维持。",
                ),
            ),
        )
        session = service.update_hypothesis(
            session,
            ResearchHypothesis(
                "counter",
                "增长主要来自渠道压货。",
                status=HypothesisStatus.WEAKENED,
                counterevidence_refs=("evidence:channel",),
                role=HypothesisRole.COUNTERARGUMENT,
            ),
        )
        ready = service.mark_ready(session)

        self.assertIs(
            ready.status,
            ExplorationStatus.READY_FOR_REPORT,
        )
        with self.assertRaisesRegex(ValueError, "no longer editable"):
            service.add_question(
                ready,
                ExplorationQuestion(
                    "late",
                    "迟到的问题",
                    "不应允许",
                    False,
                ),
            )

    def test_pipeline_executes_assembles_reviews_and_renders(self):
        task = _task("ordinary")
        service = _FakeExecutionService()
        report = _report(task)
        assembly = _FakeAssembly(report)
        pipeline = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {"ordinary": service}
            ),
            report_assembly=assembly,
        )
        peer = object()

        result = pipeline.run(
            ResearchRunRequest(task, peer_comparison=peer)
        )

        self.assertIs(result.status, ResearchRunStatus.COMPLETE)
        self.assertTrue(result.review.passed)
        self.assertIs(result.report, report)
        self.assertIn("示例资产 基础研究包", result.user_markdown)
        self.assertEqual(assembly.calls, [(result.execution, peer)])

    def test_pipeline_attaches_persisted_workflow_handoff(self):
        task = _task("ordinary")
        workflow_coordinator = _FakeWorkflowCoordinator()
        pipeline = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {"ordinary": _FakeExecutionService()}
            ),
            report_assembly=_FakeAssembly(_report(task)),
            workflow_coordinator=workflow_coordinator,
        )
        request = ResearchRunRequest(
            task,
            workflow_run_id="workflow:test",
        )

        result = pipeline.run(request)

        self.assertEqual(result.workflow_run, "workflow-run")
        self.assertEqual(result.handoff, "baseline-handoff")
        self.assertEqual(
            workflow_coordinator.calls,
            [(request, replace(
                result,
                workflow_run=None,
                handoff=None,
            ))],
        )

    def test_pipeline_publishes_user_markdown_to_report_root(self):
        task = _task("ordinary")
        report = _report(task)
        with tempfile.TemporaryDirectory() as temp_dir:
            report_root = Path(temp_dir) / "report"
            pipeline = ResearchOrchestrationPipeline(
                dispatcher=ResearchExecutionDispatcher(
                    {"ordinary": _FakeExecutionService()}
                ),
                report_assembly=_FakeAssembly(report),
                publisher=ReportArtifactService(report_root),
            )

            result = pipeline.run(ResearchRunRequest(task))

            self.assertIsNotNone(result.report_path)
            assert result.report_path is not None
            self.assertEqual(result.report_path.parent, report_root)
            self.assertEqual(
                result.report_path.read_text(encoding="utf-8"),
                result.user_markdown,
            )
            self.assertNotIn("## 关键步骤摘要", result.user_markdown)

    def test_pipeline_publishes_execution_charts_with_stable_paths(self):
        task = _task("ordinary")
        report = _report(task)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_root = temp_root / "source"
            source_root.mkdir()
            paths = {
                "png_path": source_root / "source.png",
                "svg_path": source_root / "source.svg",
                "data_path": source_root / "source.data.json",
                "metadata_path": source_root / "source.metadata.json",
            }
            for path in paths.values():
                path.write_text("chart artifact", encoding="utf-8")
            artifact = ChartArtifact(
                chart_type=ChartType.DRAWDOWN,
                input_digest="a" * 64,
                **paths,
            )
            execution = replace(
                _execution(task),
                chart_artifacts=(artifact,),
            )
            report_root = temp_root / "report"
            pipeline = ResearchOrchestrationPipeline(
                dispatcher=ResearchExecutionDispatcher(
                    {
                        "ordinary": _FakeExecutionService(
                            execution=execution
                        )
                    }
                ),
                report_assembly=_FakeAssembly(report),
                publisher=ReportArtifactService(report_root),
            )

            result = pipeline.run(ResearchRunRequest(task))

            chart_root = (
                report_root
                / "assets"
                / "示例资产-基础研究包-2026-08-09-drawdown"
                / "charts"
            )
            self.assertIn(
                (
                    "assets/示例资产-基础研究包-2026-08-09-"
                    "drawdown/charts/chart.png"
                ),
                result.user_markdown,
            )
            for filename in (
                "chart.png",
                "chart.svg",
                "data.json",
                "metadata.json",
            ):
                self.assertTrue((chart_root / filename).exists())
            self.assertNotIn(artifact.input_digest, result.user_markdown)
            self.assertNotIn(task.task_id, result.user_markdown)

    def test_baseline_manifest_accepts_parentheses_in_chart_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_root = Path(temp_dir)
            report_path = report_root / "基金(LOF)A-基础研究包.md"
            report_path.write_text("report", encoding="utf-8")
            relative = (
                "assets/基金(LOF)A-基础研究包-drawdown/"
                "charts/chart.png"
            )
            chart_root = report_root / Path(
                *relative.split("/")
            ).parent
            chart_root.mkdir(parents=True)
            for filename in ("chart.png", "metadata.json", "data.json"):
                (chart_root / filename).write_text(
                    filename,
                    encoding="utf-8",
                )

            manifest = _baseline_chart_manifest(
                SimpleNamespace(
                    report_path=report_path,
                    user_markdown=f"![回撤]({relative})",
                )
            )

            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0].markdown_path, relative)

    def test_blocked_execution_stops_before_report_assembly(self):
        task = _task("blocked-profile")
        service = _FakeExecutionService(blocked=True)
        assembly = _FakeAssembly(_report(task))
        pipeline = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {"blocked-profile": service}
            ),
            report_assembly=assembly,
        )

        result = pipeline.run(ResearchRunRequest(task))

        self.assertIs(result.status, ResearchRunStatus.BLOCKED)
        self.assertIsNone(result.report)
        self.assertEqual(assembly.calls, [])
        self.assertIn("未生成基础研究包", result.user_markdown)

    def test_review_finds_fact_without_evidence(self):
        task = _task("ordinary")
        report = _report(task)
        fact_without_evidence = SimpleNamespace(
            kind=ClaimKind.FACT,
            text="缺少证据的事实",
            evidence_refs=(),
        )
        report = replace(
            report,
            conclusions=(fact_without_evidence,),
        )

        review = ResearchReportReviewer().review(
            report,
            expected_task=task,
        )

        self.assertIn(
            "fact_evidence_missing",
            {item.code for item in review.findings},
        )

    def test_review_checks_official_sources_and_derived_methodology(self):
        task = _task("ordinary")
        report = replace(
            _report(task),
            fact_sections=(
                SimpleNamespace(
                    section_id="review-test",
                    facts=(
                        SimpleNamespace(
                            kind=ResearchFactKind.OFFICIAL,
                            as_of_date=task.as_of_date,
                            evidence_refs=("local:document",),
                            limitations=(),
                        ),
                        SimpleNamespace(
                            kind=ResearchFactKind.DERIVED,
                            as_of_date=task.as_of_date,
                            evidence_refs=("derived:test",),
                            limitations=(),
                        ),
                    ),
                ),
            ),
        )

        review = ResearchReportReviewer().review(
            report,
            expected_task=task,
        )

        self.assertTrue(
            {
                "official_fact_source_invalid",
                "derived_fact_methodology_missing",
            }.issubset({item.code for item in review.findings})
        )

    def test_review_requires_static_markdown_chart_citations(self):
        task = _task("ordinary")
        report = _report(task)
        steps = tuple(
            replace(
                step,
                artifacts=("cumulative_return:digest",),
            )
            if step.step_id == "charts"
            else step
            for step in report.steps
        )
        report = replace(report, asset_type="open_fund", steps=steps)

        missing = ResearchReportReviewer().review_markdown(report, "# 报告")
        invalid = ResearchReportReviewer().review_markdown(
            report,
            "![累计收益](C:/temp/chart.png)",
        )
        mismatch = ResearchReportReviewer().review_markdown(
            report,
            "![累计收益](assets/wrong/chart.png)",
            charts=(
                SimpleNamespace(
                    markdown_path="assets/expected/chart.png"
                ),
            ),
        )

        self.assertIn(
            "chart_citation_missing",
            {item.code for item in missing.findings},
        )
        self.assertIn(
            "chart_citation_invalid",
            {item.code for item in invalid.findings},
        )
        self.assertIn(
            "chart_citation_mismatch",
            {item.code for item in mismatch.findings},
        )

    def test_renderer_does_not_leak_internal_fields(self):
        task = _task("ordinary")
        report = _report(task)
        internal_text = " ".join(
            (
                report.report_id,
                report.acceptance_version,
                task.task_id,
                task.asset_id,
                report.structure_digest,
                "pass",
                "src/research_acceptance/report_service.py",
            )
        )
        report = replace(
            report,
            conclusions=(
                ResearchClaim(
                    ClaimKind.INFERENCE,
                    internal_text,
                ),
            ),
        )

        markdown = ResearchReportRenderer().render(report)

        for forbidden in (
            report.report_id,
            report.acceptance_version,
            task.task_id,
            task.asset_id,
            report.structure_digest,
            "src/research_acceptance/report_service.py",
        ):
            self.assertNotIn(forbidden, markdown)
        self.assertNotRegex(markdown, r"(?<![\w])pass(?![\w])")
        self.assertNotRegex(
            markdown,
            r"(?<![\w])assumption(?![\w])",
        )
        self.assertIn("## 初始三情景", markdown)
        self.assertIn("## 失效条件", markdown)

    def test_renderer_outputs_facts_and_omits_not_applicable_scenarios(
        self,
    ):
        task = _task("ordinary")
        not_applicable = ResearchClaim(
            ClaimKind.NOT_APPLICABLE,
            "理解型任务不适用。",
        )
        report = replace(
            _report(task),
            fact_sections=(
                ResearchFactSection(
                    section_id="fund_nav",
                    title="净值与数据覆盖",
                    facts=(
                        ResearchFact(
                            label="最新单位净值",
                            value="1.52",
                            unit="元",
                            as_of_date="2026-08-08",
                            kind=ResearchFactKind.REPORTED,
                            evidence_refs=("unit:nav",),
                        ),
                    ),
                ),
            ),
            scenarios=tuple(
                ResearchScenario(kind, (not_applicable,))
                for kind in ScenarioKind
            ),
            strongest_counterargument=not_applicable,
        )

        markdown = ResearchReportRenderer().render(report)

        self.assertIn("## 净值与数据覆盖", markdown)
        self.assertIn(
            "最新单位净值：1.52元（来源记录，截至2026-08-08）",
            markdown,
        )
        self.assertNotIn("## 初始三情景", markdown)
        self.assertNotIn("## 最强反方", markdown)

    def test_cli_help_lists_runnable_command(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("run-single", output.getvalue())

    def test_cli_run_single_parses_parameters_and_prints_only_path(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.COMPLETE,
            report_path=Path("report") / "用户报告.md",
        )
        output = io.StringIO()

        return_code = main(
            [
                "run-single",
                "--asset-id",
                "stock:test",
                "--profile",
                PROFILE_ORDINARY_COMPANY,
                "--question",
                "understanding",
                "--horizon",
                "long",
                "--as-of-date",
                "2026-08-09",
                "--benchmark-asset-id",
                "index:test",
                "--db-path",
                "data/database/test.sqlite3",
                "--report-root",
                "custom-report",
                "--provider-timeout",
                "12.5",
            ],
            container_factory=factory,
            stdout=output,
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(
            output.getvalue(),
            f"{Path('report') / '用户报告.md'}\n",
        )
        self.assertEqual(
            factory.calls,
            [
                {
                    "db_path": "data/database/test.sqlite3",
                    "report_root": "custom-report",
                }
            ],
        )
        task_arguments = factory.task_factory.calls[0]
        self.assertEqual(task_arguments["asset_id"], "stock:test")
        self.assertEqual(
            task_arguments["question_type"],
            ResearchQuestionType.UNDERSTANDING,
        )
        self.assertEqual(
            task_arguments["horizon"],
            ResearchHorizon.LONG,
        )
        self.assertEqual(
            task_arguments["benchmark_asset_id"],
            "index:test",
        )
        options = factory.pipeline.calls[0].execution_options
        self.assertIsInstance(options, ResearchExecutionOptions)
        self.assertNotIsInstance(
            options,
            CycleResearchExecutionOptions,
        )
        self.assertEqual(options.provider_timeout_seconds, 12.5)
        self.assertNotIn("stock:test", output.getvalue())
        self.assertNotIn("research:test", output.getvalue())

    def test_cli_runs_prepared_event_name_discovery_json(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.COMPLETE,
            report_path=Path("report") / "unused.md",
        )
        event = ExternalEventAnchor(
            event_id="event:test",
            title="重大事件",
            event_kind="test",
            occurred_at="2026-08-09T08:00:00+08:00",
            first_public_at="2026-08-09T08:00:00+08:00",
            source="unit-test",
            source_url="https://example.com/event",
            phrases=(
                EventPhrase(
                    phrase_id="phrase:test",
                    text="测试胜选",
                    direction=EventDirection.POSITIVE,
                    components=("测试", "胜选"),
                ),
            ),
            evidence_refs=("https://example.com/event",),
        )
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "event.json"
            input_path.write_text(
                json.dumps(
                    {
                        "event": event.to_dict(),
                        "spec": EventNameDiscoverySpec(
                            as_of_date="2026-08-09",
                            knowledge_cutoff_at=(
                                "2026-08-09T23:59:59+08:00"
                            ),
                        ).to_dict(),
                        "relation_assessments": [],
                        "reactions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            return_code = main(
                [
                    "run-event-name-discovery",
                    "--input-json",
                    str(input_path),
                ],
                container_factory=factory,
                stdout=output,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(
            output.getvalue(),
            "event-name-discovery:test\n",
        )
        self.assertEqual(len(factory.event_name_discovery.calls), 1)

    def test_cli_blocked_run_returns_two_without_report_path(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.BLOCKED,
            report_path=None,
        )
        output = io.StringIO()
        error = io.StringIO()

        return_code = main(
            _cli_arguments(PROFILE_ORDINARY_COMPANY),
            container_factory=factory,
            stdout=output,
            stderr=error,
        )

        self.assertEqual(return_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("blocked", error.getvalue())

    def test_cli_builds_cycle_options_from_commodity_weights(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.COMPLETE,
            report_path=Path("report") / "cycle.md",
        )

        return_code = main(
            [
                *_cli_arguments(PROFILE_CYCLICAL_RESOURCE),
                "--provider-timeout",
                "45",
                "--commodity-weight",
                "sina:CU0=0.7",
                "--commodity-weight",
                "sge:Au99.99=0.3",
            ],
            container_factory=factory,
            stdout=io.StringIO(),
        )

        self.assertEqual(return_code, 0)
        options = factory.pipeline.calls[0].execution_options
        self.assertIsInstance(options, CycleResearchExecutionOptions)
        self.assertEqual(options.provider_timeout_seconds, 45.0)
        self.assertEqual(
            options.commodity_weights,
            (("sina:CU0", 0.7), ("sge:Au99.99", 0.3)),
        )

    def test_cli_resolves_unique_symbol_without_agent_identity_steps(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.COMPLETE,
            report_path=Path("report") / "resolved.md",
        )
        arguments = _cli_arguments(PROFILE_ORDINARY_COMPANY)
        arguments[1:3] = [
            "--symbol",
            "600519",
            "--asset-type",
            "stock",
            "--exchange",
            "sh",
        ]

        return_code = main(
            arguments,
            container_factory=factory,
            stdout=io.StringIO(),
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(
            factory.task_factory.calls[0]["asset_id"],
            "asset:resolved",
        )

    def test_cli_creates_missing_fund_identity_from_provider_catalog(self):
        factory = _FakeContainerFactory(
            status=ResearchRunStatus.COMPLETE,
            report_path=Path("report") / "fund.md",
        )
        arguments = _cli_arguments(PROFILE_OPEN_FUND)
        arguments[1:3] = [
            "--symbol",
            "163417",
            "--asset-type",
            "open_fund",
            "--name",
            "兴全合宜混合(LOF)A",
        ]

        return_code = main(
            arguments,
            container_factory=factory,
            stdout=io.StringIO(),
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(
            factory.task_factory.calls[0]["asset_id"],
            "asset:created",
        )
        self.assertEqual(
            factory.data_fetch.calls,
            [("163417", "open_fund")],
        )


class _FakeContainerFactory:
    def __init__(self, *, status, report_path):
        self.calls = []
        self.task_factory = _FakeTaskFactory()
        self.pipeline = _FakePipeline(status, report_path)
        self.event_name_discovery = _FakeEventNamePipeline()
        self.master_data = _FakeMasterData()
        self.data_fetch = _FakeDataFetch(self.master_data)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            task_factory=self.task_factory,
            pipeline=self.pipeline,
            event_name_discovery=self.event_name_discovery,
            master_data=self.master_data,
            data_fetch=self.data_fetch,
        )


class _FakeTaskFactory:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _task(kwargs["profile_id"])


class _FakeMasterData:
    def __init__(self):
        self.fund_identity_created = False

    def find_assets(self, *, standard_symbol=None, asset_type=None):
        if standard_symbol == "600519" and asset_type == "stock":
            return [
                SimpleNamespace(
                    asset_id="asset:resolved",
                    exchange="sh",
                    display_name="贵州茅台",
                )
            ]
        if (
            standard_symbol == "163417"
            and asset_type == "open_fund"
            and self.fund_identity_created
        ):
            return [
                SimpleNamespace(
                    asset_id="asset:created",
                    exchange=None,
                    display_name="兴全合宜混合(LOF)A",
                )
            ]
        return []


class _FakeDataFetch:
    def __init__(self, master_data):
        self.master_data = master_data
        self.calls = []

    def fetch_and_store_identities(self, symbol, *, asset_type=None):
        self.calls.append((symbol, asset_type))
        self.master_data.fund_identity_created = True
        return SimpleNamespace(
            symbol=symbol,
            fetched_count=1,
            stored_count=1,
        )


class _FakePipeline:
    def __init__(self, status, report_path):
        self.status = status
        self.report_path = report_path
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        return SimpleNamespace(
            status=self.status,
            report_path=self.report_path,
        )


class _FakeEventNamePipeline:
    def __init__(self):
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        return SimpleNamespace(
            discovery_run=SimpleNamespace(
                run_id="event-name-discovery:test"
            )
        )


class _FakeExecutionService:
    def __init__(self, blocked=False, execution=None):
        self.blocked = blocked
        self.execution = execution
        self.calls = []

    def execute(self, task, *, options=None):
        self.calls.append((task, options))
        return self.execution or _execution(
            task,
            blocked=self.blocked,
        )


class _RequiredOptionsExecutionService:
    def execute(self, task, *, options):
        return _execution(task)


class _FakeAssembly:
    def __init__(self, report):
        self.report = report
        self.calls = []

    def assemble(self, execution, *, peer_comparison=None):
        self.calls.append((execution, peer_comparison))
        return self.report


class _FakeWorkflowCoordinator:
    def __init__(self):
        self.calls = []

    def record_result(self, request, result):
        self.calls.append((request, result))
        return "workflow-run", "baseline-handoff"


class _FakePreparationJobs:
    def __init__(self, status):
        self.status = status
        self.create_calls = []
        self.batch_calls = []

    def create_job(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(status="pending")

    def run_batch(self, **kwargs):
        self.batch_calls.append(kwargs)
        return SimpleNamespace(
            job=SimpleNamespace(status=self.status),
            counts={},
            last_batch_remote_attempted=0,
        )


class _FakeDiscoveryService:
    def __init__(self):
        self.calls = []

    def run(self, spec):
        self.calls.append(spec)
        return "discovery-result"


def _cli_arguments(profile_id):
    return [
        "run-single",
        "--asset-id",
        "stock:test",
        "--profile",
        profile_id,
        "--question",
        "understanding",
        "--horizon",
        "long",
        "--as-of-date",
        "2026-08-09",
    ]


def _task(profile_id):
    return ResearchTaskSpec(
        task_id="research:test:orchestration",
        asset_id="asset:test",
        profile_id=profile_id,
        question_type=ResearchQuestionType.UNDERSTANDING,
        horizon=ResearchHorizon.LONG,
        as_of_date="2026-08-09",
    )


def _execution(task, *, blocked=False):
    status = (
        ResearchExecutionStatus.BLOCKED
        if blocked
        else ResearchExecutionStatus.COMPLETE
    )
    phase_status = (
        ResearchPhaseStatus.FAILED
        if blocked
        else ResearchPhaseStatus.SUCCESS
    )
    gaps = (
        (
            ResearchExecutionGap(
                "data_access",
                "required_data_missing",
                "必需数据不可用。",
                True,
            ),
        )
        if blocked
        else ()
    )
    return ResearchExecutionResult(
        task=task,
        windows=ResearchWindows(
            bar_start="2025-08-09",
            bar_end=task.as_of_date,
            financial_start="2023-08-09",
            financial_end=task.as_of_date,
            evidence_start="2025-08-09",
            evidence_end=task.as_of_date,
            strategy_window_days=365,
        ),
        status=status,
        phases=(
            ResearchPhaseResult(
                "task",
                phase_status,
                "Research task checked.",
                sources=("unit-test",) if not blocked else (),
            ),
        ),
        gaps=gaps,
    )


def _report(task):
    steps = tuple(
        AcceptanceStepResult(
            step_id=step_id,
            status=AcceptanceStepStatus.PASS,
            summary=f"{step_id} summary",
            sources=(f"test:{step_id}",),
        )
        for step_id in REQUIRED_ACCEPTANCE_STEPS
    )
    scenarios = tuple(
        ResearchScenario(
            kind=kind,
            claims=(
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    f"{kind.value} scenario assumption",
                ),
            ),
        )
        for kind in ScenarioKind
    )
    return ResearchAcceptanceReport(
        report_id="acceptance:internal-report-id",
        acceptance_version="research_report_v1",
        task=task,
        asset_name="示例资产",
        asset_type="stock",
        steps=steps,
        scenarios=scenarios,
        strongest_counterargument=ResearchClaim(
            ClaimKind.INFERENCE,
            "最强反方观点。",
            ("evidence:counter",),
        ),
        invalidation_conditions=(
            InvalidationCondition(
                "核心前提不再成立。",
                "更新后的可靠证据。",
                "thesis_break",
            ),
        ),
        conclusions=(
            ResearchClaim(
                ClaimKind.FACT,
                "当前结论有事实支持。",
                ("evidence:conclusion",),
            ),
        ),
        gaps=(),
        overall_status=AcceptanceStatus.COMPLETE,
        structure_digest=(
            "0123456789abcdef0123456789abcdef"
            "0123456789abcdef0123456789abcdef"
        ),
        replay_verified=True,
    )


if __name__ == "__main__":
    unittest.main()
