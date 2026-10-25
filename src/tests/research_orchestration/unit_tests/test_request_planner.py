import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_orchestration import (
    ResearchPlanningRequest,
    ResearchPlanningStatus,
    ResearchRequestPlanner,
)
from src.research_orchestration.cli import main
from src.research_workflow import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchProfileAssignmentService,
    ResearchQuestionType,
    ResearchTaskFactory,
    SQLiteResearchProfileAssignmentRepository,
)


class ResearchRequestPlannerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "planner.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.stock = self._register(
            asset_id="stock:600519",
            symbol="600519",
            asset_type=ASSET_TYPE_STOCK,
            name="贵州茅台",
            market="cn",
            exchange="sh",
        )
        self.financial = self._register(
            asset_id="stock:000001",
            symbol="000001",
            asset_type=ASSET_TYPE_STOCK,
            name="平安银行",
            market="cn",
            exchange="sz",
        )
        self.open_fund = self._register(
            asset_id="open-fund:002236",
            symbol="002236",
            asset_type=ASSET_TYPE_OPEN_FUND,
            name="大成360互联网+大数据100A",
            market="cn",
            exchange=None,
        )
        self.ambiguous_stock = self._register(
            asset_id="stock:999999",
            symbol="999999",
            asset_type=ASSET_TYPE_STOCK,
            name="同码股票",
            market="cn",
            exchange="sh",
        )
        self.ambiguous_etf = self._register(
            asset_id="etf:999999",
            symbol="999999",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            name="同码ETF",
            market="cn",
            exchange="sh",
        )
        self.task_factory = ResearchTaskFactory(self.master_data)
        self.assignment = ResearchProfileAssignmentService(
            master_data=self.master_data,
            repository=SQLiteResearchProfileAssignmentRepository(
                self.db_path
            ),
            profile_registry=self.task_factory.profile_registry,
        )
        self.assignment.assign_manual(
            asset_id=self.financial.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            industry_name="银行",
            effective_date="2026-01-01",
            reason="unit_test",
        )
        self.planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unique_symbol_produces_profile_and_task_without_manual_profile(self):
        result = self.planner.plan(
            ResearchPlanningRequest(
                symbol="600519",
                as_of_date="2026-08-23",
            )
        )

        self.assertIs(result.status, ResearchPlanningStatus.READY)
        self.assertEqual(
            result.identity.asset_id,
            self.stock.asset_id,
        )
        self.assertEqual(
            result.profile_id,
            PROFILE_ORDINARY_COMPANY,
        )
        self.assertEqual(
            result.task.question_type,
            ResearchQuestionType.UNDERSTANDING,
        )

    def test_ambiguous_symbol_returns_choices_without_guessing(self):
        result = self.planner.plan(
            ResearchPlanningRequest(
                symbol="999999",
                as_of_date="2026-08-23",
            )
        )

        self.assertIs(
            result.status,
            ResearchPlanningStatus.NEEDS_CLARIFICATION,
        )
        self.assertIsNone(result.task)
        self.assertEqual(
            {item.asset_id for item in result.candidates},
            {
                self.ambiguous_stock.asset_id,
                self.ambiguous_etf.asset_id,
            },
        )

        selected = self.planner.plan(
            ResearchPlanningRequest(
                symbol="999999",
                asset_type=ASSET_TYPE_EXCHANGE_FUND,
                as_of_date="2026-08-23",
            )
        )
        self.assertIs(
            selected.status,
            ResearchPlanningStatus.READY,
        )
        self.assertEqual(
            selected.profile_id,
            PROFILE_EXCHANGE_FUND,
        )

    def test_profile_assignment_overrides_stock_default(self):
        result = self.planner.plan(
            ResearchPlanningRequest(
                asset_id=self.financial.asset_id,
                as_of_date="2026-08-23",
            )
        )

        self.assertEqual(
            result.profile_id,
            PROFILE_FINANCIAL_INSTITUTION,
        )
        self.assertIn("profile_assignment", result.routing_notes)

    def test_open_fund_keeps_purchase_goal_but_uses_understanding_task(self):
        result = self.planner.plan(
            ResearchPlanningRequest(
                symbol="002236",
                asset_type=ASSET_TYPE_OPEN_FUND,
                as_of_date="2026-08-23",
                decision_intent=DecisionIntent.EVALUATE_PURCHASE,
                decision_lanes=(DecisionLane.SUITABILITY,),
                horizon=ResearchHorizon.MEDIUM_LONG,
            )
        )

        self.assertIs(result.status, ResearchPlanningStatus.READY)
        self.assertEqual(result.profile_id, PROFILE_OPEN_FUND)
        self.assertEqual(
            result.task.question_type,
            ResearchQuestionType.UNDERSTANDING,
        )
        self.assertIs(
            result.request.decision_intent,
            DecisionIntent.EVALUATE_PURCHASE,
        )
        self.assertIn(
            "user_decision_objective_preserved_for_exploration",
            result.routing_notes,
        )

    def test_missing_open_fund_identity_uses_public_bootstrap(self):
        bootstrap = _FundBootstrap(self.master_data)
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
            fund_identity_bootstrap=bootstrap,
        )

        result = planner.plan(
            ResearchPlanningRequest(
                symbol="163417",
                asset_type=ASSET_TYPE_OPEN_FUND,
                as_of_date="2026-08-23",
            )
        )

        self.assertIs(result.status, ResearchPlanningStatus.READY)
        self.assertEqual(bootstrap.calls, ["163417"])
        self.assertEqual(result.profile_id, PROFILE_OPEN_FUND)

    def test_decision_brief_uses_same_public_planner(self):
        brief = ResearchDecisionBrief(
            brief_id="decision-brief:test",
            user_id="user:test",
            asset_id=self.stock.asset_id,
            decision_intent=DecisionIntent.EVALUATE_PURCHASE,
            decision_lanes=(DecisionLane.SUITABILITY,),
            position_state=PositionState.NOT_HELD,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-23",
            portfolio_role="核心配置",
            mandate_id=None,
            portfolio_constraint_snapshot_id=None,
            constraints=(),
            assumptions=(),
            missing_user_context=(),
            created_at="2026-08-23T13:00:00+00:00",
            updated_at="2026-08-23T13:00:00+00:00",
        )

        result = self.planner.plan_decision_brief(brief)

        self.assertEqual(
            result.task.question_type,
            ResearchQuestionType.INVESTMENT,
        )
        self.assertEqual(result.task.asset_id, brief.asset_id)

    def test_plan_single_cli_uses_planner_and_returns_json(self):
        output = io.StringIO()
        factory = _PlannerContainerFactory(self.planner)

        return_code = main(
            [
                "plan-single",
                "--symbol",
                "600519",
                "--as-of-date",
                "2026-08-23",
            ],
            container_factory=factory,
            stdout=output,
        )

        self.assertEqual(return_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["profile_id"],
            PROFILE_ORDINARY_COMPANY,
        )
        self.assertEqual(
            payload["task"]["question_type"],
            ResearchQuestionType.UNDERSTANDING.value,
        )
        self.assertEqual(len(factory.calls), 1)

    def _register(
        self,
        *,
        asset_id,
        symbol,
        asset_type,
        name,
        market,
        exchange,
    ):
        return self.master_data.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=name,
                market=market,
                exchange=exchange,
            ),
            source=f"unit_test:{asset_type}",
            raw_symbol=symbol,
        )


class _PlannerContainerFactory:
    def __init__(self, planner):
        self.planner = planner
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(request_planner=self.planner)


class _FundBootstrap:
    def __init__(self, master_data):
        self.master_data = master_data
        self.calls = []

    def fetch_and_store_fund_basic_info(self, *, symbol):
        self.calls.append(symbol)
        self.master_data.register_asset(
            Asset(
                asset_id=f"open-fund:{symbol}",
                asset_type=ASSET_TYPE_OPEN_FUND,
                standard_symbol=symbol,
                display_name="兴全合宜混合(LOF)A",
                market="cn",
                exchange=None,
            ),
            source="unit_test:open_fund_bootstrap",
            raw_symbol=symbol,
        )
        return SimpleNamespace(stored_count=1)


if __name__ == "__main__":
    unittest.main()
