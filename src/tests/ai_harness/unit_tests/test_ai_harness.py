import tempfile
import unittest
from pathlib import Path

from src.ai_harness import (
    CLAIM_FACT,
    CLAIM_SUGGESTION,
    TASK_COMPLETED,
    TASK_FAILED,
    AIHarnessService,
    AnalysisClaim,
    EvidenceReference,
    ModelOutput,
    ResearchContextAssembler,
    SQLiteAIHarnessRepository,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from src.data_src.models import DisclosureDocument
from src.event_knowledge import EventKnowledgeService, SQLiteEventRepository
from src.factor_engineering import FactorService, SQLiteFactorRepository
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.research_state import (
    ResearchStateService,
    SQLiteResearchStateRepository,
)
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class CitingAdapter:
    name = "test-citing"

    def generate(self, task, evidence):
        return ModelOutput(
            content="结构化分析完成",
            data={"evidence_count": len(evidence)},
            citations=(evidence[0].evidence_id,),
        )


class InvalidCitationAdapter:
    name = "test-invalid"

    def generate(self, task, evidence):
        return ModelOutput(
            content="无效引用",
            citations=("evidence:missing",),
        )


class ToolAwareAdapter:
    name = "test-tool-aware"

    def generate(self, task, evidence):
        tool_evidence = next(
            item
            for item in evidence
            if item.evidence_type == "tool"
        )
        return ModelOutput(
            content="工具与结构化结论完成",
            citations=(tool_evidence.evidence_id,),
            claims=(
                AnalysisClaim(
                    claim_type=CLAIM_FACT,
                    text="最新价格已由工具返回。",
                    evidence_ids=(tool_evidence.evidence_id,),
                ),
                AnalysisClaim(
                    claim_type=CLAIM_SUGGESTION,
                    text="继续观察。",
                ),
            ),
        )


class PriceTool:
    name = "latest_price"

    def run(self, task, arguments):
        price = float(arguments["price"])
        return ToolResult(
            output={"price": price},
            evidence=(
                EvidenceReference(
                    evidence_id=f"tool-price:{task.asset_id}",
                    evidence_type="tool",
                    source_id=task.asset_id,
                    title="最新价格",
                    content=str(price),
                ),
            ),
        )


class FailingTool:
    name = "failing"

    def run(self, task, arguments):
        raise RuntimeError("tool failed")


class UncitedFactAdapter:
    name = "test-uncited-fact"

    def generate(self, task, evidence):
        return ModelOutput(
            content="缺少证据",
            claims=(
                AnalysisClaim(
                    claim_type=CLAIM_FACT,
                    text="无证据事实",
                ),
            ),
        )


class AIHarnessServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ai-harness.sqlite3"
        self.users = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        self.context = self.users.default_context()
        self.master = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master.resolve_identifier(
            source="unit-test",
            raw_symbol="518880",
            name="黄金ETF",
            asset_type_hint="ETF",
        ).asset
        self.factors = FactorService(
            SQLiteFactorRepository(self.db_path),
            self.master,
        )
        self.factors.compute_and_store(
            asset_id=self.asset.asset_id,
            records=[
                {
                    "trade_date": f"2026-07-{day:02d}",
                    "close": close,
                }
                for day, close in zip(
                    range(19, 25),
                    [8.0, 8.1, 8.2, 8.3, 8.4, 8.385],
                )
            ],
            source_dataset="fund_exchange_daily_bars",
        )
        self.events = EventKnowledgeService(
            SQLiteEventRepository(self.db_path),
            self.master,
        )
        self.events.ingest_disclosure(
            self.asset.asset_id,
            DisclosureDocument(
                document_id="event-doc",
                symbol="518880",
                title="黄金ETF2025年年度报告",
                announcement_date="2026-04-17",
                category="annual",
                source="cninfo",
                file_url="https://example.com/report.pdf",
            ),
        )
        self.research = ResearchStateService(
            SQLiteResearchStateRepository(self.db_path),
            self.users,
            self.master,
        )
        self.research.upsert_holding(
            self.context,
            asset_id=self.asset.asset_id,
            quantity=100,
            average_cost=8.1,
        )
        self.research.save_report(
            self.context,
            asset_id=self.asset.asset_id,
            title="黄金ETF观察",
            summary="",
            content_markdown="# 观察\n维持关注。",
        )
        assembler = ResearchContextAssembler(
            self.master,
            self.factors,
            self.events,
            self.research,
        )
        self.service = AIHarnessService(
            SQLiteAIHarnessRepository(self.db_path),
            self.users,
            assembler,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_context_contains_traceable_evidence(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="黄金ETF当前有哪些值得关注的因素？",
        )
        evidence = self.service.list_evidence(
            self.context,
            task.task_id,
        )

        evidence_types = {item.evidence_type for item in evidence}
        self.assertEqual(
            evidence_types,
            {"asset", "factor", "event", "report", "holding"},
        )
        self.assertEqual(len(evidence), 9)

    def test_adapter_result_and_citations_are_persisted(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="总结当前研究证据。",
        )

        completed = self.service.execute(
            self.context,
            task.task_id,
            CitingAdapter(),
        )
        reloaded = self.service.get_task(
            self.context,
            task.task_id,
        )

        self.assertEqual(completed.status, TASK_COMPLETED)
        self.assertEqual(completed.model_name, "test-citing")
        self.assertEqual(len(completed.citations), 1)
        self.assertEqual(reloaded, completed)

    def test_invalid_citation_fails_task(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="制造无效引用。",
        )

        with self.assertRaises(ValueError):
            self.service.execute(
                self.context,
                task.task_id,
                InvalidCitationAdapter(),
            )

        failed = self.service.get_task(
            self.context,
            task.task_id,
        )
        self.assertEqual(failed.status, TASK_FAILED)
        self.assertIn("unknown evidence", failed.error)

    def test_other_user_cannot_query_task_normally(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="用户归属测试。",
        )
        other = self.users.create_user("Other")
        other_context = self.users.context_for_user(other.user_id)

        with self.assertRaises(KeyError):
            self.service.get_task(other_context, task.task_id)
        self.assertEqual(self.service.list_tasks(other_context), [])

    def test_explicit_tool_adds_evidence_and_claims(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="结合最新价格总结。",
        )

        completed = self.service.execute(
            self.context,
            task.task_id,
            ToolAwareAdapter(),
            tool_calls=(
                ToolCall(
                    tool_name="latest_price",
                    arguments={"price": 8.385},
                ),
            ),
            tool_registry=ToolRegistry((PriceTool(),)),
        )
        executions = self.service.list_tool_executions(
            self.context,
            task.task_id,
        )

        self.assertEqual(completed.status, TASK_COMPLETED)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, TASK_COMPLETED)
        self.assertEqual(
            completed.result["claims"][0]["claim_type"],
            CLAIM_FACT,
        )
        self.assertEqual(
            len(self.service.list_evidence(self.context, task.task_id)),
            10,
        )

    def test_tool_failure_is_recorded_and_fails_task(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="工具失败测试。",
        )

        with self.assertRaises(RuntimeError):
            self.service.execute(
                self.context,
                task.task_id,
                CitingAdapter(),
                tool_calls=(ToolCall(tool_name="failing"),),
                tool_registry=ToolRegistry((FailingTool(),)),
            )

        execution = self.service.list_tool_executions(
            self.context,
            task.task_id,
        )[0]
        failed = self.service.get_task(self.context, task.task_id)
        self.assertEqual(execution.status, TASK_FAILED)
        self.assertEqual(failed.status, TASK_FAILED)

    def test_fact_claim_without_evidence_is_rejected(self):
        task = self.service.create_asset_analysis(
            self.context,
            asset_id=self.asset.asset_id,
            question="事实证据测试。",
        )

        with self.assertRaises(ValueError):
            self.service.execute(
                self.context,
                task.task_id,
                UncitedFactAdapter(),
            )


if __name__ == "__main__":
    unittest.main()
