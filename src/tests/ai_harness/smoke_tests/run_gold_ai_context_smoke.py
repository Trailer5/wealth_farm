"""Run a complete gold ETF evidence task with a deterministic adapter."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class EvidenceAdapter:
    name = "smoke-evidence-adapter"

    def generate(self, task, evidence):
        from src.ai_harness import (
            CLAIM_FACT,
            CLAIM_SUGGESTION,
            AnalysisClaim,
            ModelOutput,
        )

        tool_evidence = next(
            item for item in evidence if item.evidence_type == "tool"
        )
        return ModelOutput(
            content="黄金ETF研究上下文已装配。",
            data={"evidence_count": len(evidence)},
            citations=(tool_evidence.evidence_id,),
            claims=(
                AnalysisClaim(
                    claim_type=CLAIM_FACT,
                    text="最新价格工具已返回结果。",
                    evidence_ids=(tool_evidence.evidence_id,),
                ),
                AnalysisClaim(
                    claim_type=CLAIM_SUGGESTION,
                    text="结合因子和持仓继续观察。",
                ),
            ),
        )


class LatestPriceTool:
    name = "latest_price"

    def __init__(self, price):
        self.price = price

    def run(self, task, arguments):
        from src.ai_harness import EvidenceReference, ToolResult

        return ToolResult(
            output={"price": self.price},
            evidence=(
                EvidenceReference(
                    evidence_id=f"tool-price:{task.asset_id}",
                    evidence_type="tool",
                    source_id=task.asset_id,
                    title="最新价格",
                    content=str(self.price),
                ),
            ),
        )


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.ai_harness import (
        AIHarnessService,
        ResearchContextAssembler,
        SQLiteAIHarnessRepository,
        ToolCall,
        ToolRegistry,
    )
    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
        TradingCalendarService,
    )
    from src.data_src.models import DisclosureDocument
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.event_knowledge import EventKnowledgeService, SQLiteEventRepository
    from src.factor_engineering import FactorService, SQLiteFactorRepository
    from src.master_data import MasterDataService, SQLiteMasterDataRepository
    from src.research_state import (
        ResearchStateService,
        SQLiteResearchStateRepository,
    )
    from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "gold-ai-context.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(SQLiteMasterDataRepository(db_path))
        fetch = DataFetchService(store=store, master_data_service=master)
        access_repository = SQLiteDataAccessRepository(store)
        response = DataAccessService(
            master_data=master,
            repository=access_repository,
            fetch_service=fetch,
            trading_calendar=TradingCalendarService(
                repository=access_repository,
                fetch_service=fetch,
            ),
        ).get(
            DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="518880",
                    name="黄金ETF",
                    asset_type_hint="ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-24",
            )
        )
        factors = FactorService(SQLiteFactorRepository(db_path), master)
        factors.compute_from_response(response)
        events = EventKnowledgeService(
            SQLiteEventRepository(db_path),
            master,
        )
        events.ingest_disclosure(
            response.asset.asset_id,
            DisclosureDocument(
                document_id="gold-ai-event",
                symbol="518880",
                title="黄金ETF2025年年度报告",
                announcement_date="2026-04-17",
                category="annual",
                source="cninfo",
                file_url="https://example.com/gold-report.pdf",
            ),
        )
        users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        context = users.default_context()
        research = ResearchStateService(
            SQLiteResearchStateRepository(db_path),
            users,
            master,
        )
        research.upsert_holding(
            context,
            asset_id=response.asset.asset_id,
            quantity=100,
            average_cost=8.1,
        )
        research.save_report(
            context,
            asset_id=response.asset.asset_id,
            title="黄金ETF观察",
            summary="真实上下文 smoke",
            content_markdown="# 黄金ETF\n维持关注。",
        )

        harness = AIHarnessService(
            SQLiteAIHarnessRepository(db_path),
            users,
            ResearchContextAssembler(
                master,
                factors,
                events,
                research,
            ),
        )
        task = harness.create_asset_analysis(
            context,
            asset_id=response.asset.asset_id,
            question="总结黄金ETF当前研究证据。",
        )
        evidence = harness.list_evidence(context, task.task_id)
        completed = harness.execute(
            context,
            task.task_id,
            EvidenceAdapter(),
            tool_calls=(ToolCall(tool_name="latest_price"),),
            tool_registry=ToolRegistry(
                (LatestPriceTool(response.records[-1]["close"]),)
            ),
        )
        evidence = harness.list_evidence(context, task.task_id)
        tool_executions = harness.list_tool_executions(
            context,
            task.task_id,
        )
        result = {
            "task_id": task.task_id,
            "status": completed.status,
            "model_name": completed.model_name,
            "evidence_count": len(evidence),
            "evidence_types": sorted(
                {item.evidence_type for item in evidence}
            ),
            "citations": completed.citations,
            "claim_types": [
                claim["claim_type"]
                for claim in completed.result["claims"]
            ],
            "tool_execution_count": len(tool_executions),
            "result": completed.result,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
