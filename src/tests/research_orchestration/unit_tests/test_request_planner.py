import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.data_src.common import DataSourceError
from src.data_src.models import StockIndustryInfo
from src.research_orchestration import (
    ResearchPlanningRequest,
    ResearchPlanningStatus,
    ResearchRequestPlanner,
)
from src.research_orchestration.cli import main
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import (
    ResearchProfileAssignmentService,
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
        self.task_factory = ResearchTaskFactory(
            self.master_data,
            benchmark_resolver=_IndexBenchmark(
                self._register(
                    asset_id="index:000300",
                    symbol="000300",
                    asset_type=ASSET_TYPE_INDEX,
                    name="沪深300",
                    market="cn",
                    exchange="sh",
                ).asset_id
            ),
        )
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

    def test_growth_rule_reroutes_ordinary_company_unless_manually_assigned(self):
        calls = []

        def detector(asset, as_of_date):
            calls.append(asset.asset_id)
            return "financial_rule:ttm_loss_revenue_growth"

        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
            growth_detector=detector,
        )
        request = ResearchPlanningRequest(symbol="600519", as_of_date="2026-08-23")
        routed = planner.plan(request)
        self.assertEqual(routed.profile_id, PROFILE_HIGH_GROWTH_UNPROFITABLE)
        self.assertIn("financial_rule:ttm_loss_revenue_growth", routed.routing_notes)

        self.assertEqual(
            planner.plan(
                ResearchPlanningRequest(asset_id=self.financial.asset_id, as_of_date="2026-08-23")
            ).profile_id,
            PROFILE_FINANCIAL_INSTITUTION,
        )
        self.assertEqual(calls, [self.stock.asset_id])

        self.assignment.assign_manual(
            asset_id=self.stock.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            industry_name="白酒",
            effective_date="2026-01-01",
            reason="unit_test",
        )
        self.assertEqual(planner.plan(request).profile_id, PROFILE_ORDINARY_COMPANY)
        self.assertEqual(len(calls), 1)

        def failing(asset, as_of_date):
            raise OSError("offline")

        fallback = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            growth_detector=failing,
        ).plan(request)
        self.assertEqual(fallback.profile_id, PROFILE_ORDINARY_COMPANY)

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

    def test_missing_identity_uses_public_bootstrap(self):
        bootstrap = _IdentityBootstrap(self.master_data)
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
            identity_bootstrap=bootstrap,
        )

        result = planner.plan(
            ResearchPlanningRequest(
                symbol="163417",
                asset_type=ASSET_TYPE_OPEN_FUND,
                as_of_date="2026-08-23",
            )
        )

        self.assertIs(result.status, ResearchPlanningStatus.READY)
        self.assertEqual(bootstrap.calls, [("163417", ASSET_TYPE_OPEN_FUND)])
        self.assertEqual(result.profile_id, PROFILE_OPEN_FUND)

    def test_bare_code_shared_by_bootstrapped_products_asks_which(self):
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            identity_bootstrap=_IdentityBootstrap(
                self.master_data,
                types=(ASSET_TYPE_STOCK, ASSET_TYPE_OPEN_FUND),
            ),
        )

        result = planner.plan(
            ResearchPlanningRequest(symbol="300001", as_of_date="2026-08-23")
        )

        self.assertIs(
            result.status,
            ResearchPlanningStatus.NEEDS_CLARIFICATION,
        )
        self.assertEqual(
            {item.asset_type for item in result.candidates},
            {ASSET_TYPE_STOCK, ASSET_TYPE_OPEN_FUND},
        )

    def test_identity_source_failure_is_not_reported_as_missing(self):
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            identity_bootstrap=_IdentityBootstrap(
                self.master_data,
                fail=True,
            ),
        )

        result = planner.plan(
            ResearchPlanningRequest(symbol="300001", as_of_date="2026-08-23")
        )

        self.assertIs(result.status, ResearchPlanningStatus.UNRESOLVED)
        self.assertEqual(result.issues, ("asset_identity_lookup_failed",))

    def test_new_stock_profile_follows_industry_classification(self):
        bank = self._register(
            asset_id="stock:600036",
            symbol="600036",
            asset_type=ASSET_TYPE_STOCK,
            name="招商银行",
            market="cn",
            exchange="sh",
        )
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
            industry_source=_Industries({"600036": "银行Ⅱ"}),
        )

        result = planner.plan(
            ResearchPlanningRequest(
                asset_id=bank.asset_id,
                as_of_date="2026-08-23",
            )
        )

        self.assertEqual(result.profile_id, PROFILE_FINANCIAL_INSTITUTION)
        self.assertIn("industry_profile_assignment", result.routing_notes)
        unavailable = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=self.task_factory,
            profile_assignment=self.assignment,
            industry_source=_Industries({}, fail=True),
        ).plan(
            ResearchPlanningRequest(
                asset_id=self.stock.asset_id,
                as_of_date="2026-08-23",
            )
        )
        self.assertEqual(unavailable.profile_id, PROFILE_ORDINARY_COMPANY)
        self.assertIn(
            "industry_unavailable_default_profile",
            unavailable.routing_notes,
        )

    def test_missing_required_benchmark_blocks_instead_of_ready(self):
        planner = ResearchRequestPlanner(
            master_data=self.master_data,
            task_factory=ResearchTaskFactory(self.master_data),
            profile_assignment=self.assignment,
        )

        stock = planner.plan(
            ResearchPlanningRequest(symbol="600519", as_of_date="2026-08-23")
        )
        fund = planner.plan(
            ResearchPlanningRequest(
                symbol="002236",
                asset_type=ASSET_TYPE_OPEN_FUND,
                as_of_date="2026-08-23",
            )
        )

        self.assertIs(stock.status, ResearchPlanningStatus.BLOCKED)
        self.assertEqual(stock.issues, ("benchmark_unavailable",))
        self.assertEqual(stock.identity.asset_id, self.stock.asset_id)
        self.assertIsNone(stock.task)
        self.assertIs(fund.status, ResearchPlanningStatus.READY)

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


class _IdentityBootstrap:
    """Registers one identity per requested type for codes starting with 1 or 3."""

    def __init__(self, master_data, *, types=None, fail=False):
        self.master_data = master_data
        self.types = types
        self.fail = fail
        self.calls = []

    def fetch_and_store_identities(self, symbol, *, asset_type=None):
        self.calls.append((symbol, asset_type))
        if self.fail:
            raise DataSourceError("provider timeout")
        if not symbol.startswith(("1", "3")):
            return SimpleNamespace(stored_count=0)
        for item in self.types or (asset_type,):
            self.master_data.register_asset(
                Asset(
                    asset_id=f"{item}:{symbol}",
                    asset_type=item,
                    standard_symbol=symbol,
                    display_name=f"{item} {symbol}",
                    market="cn",
                    exchange="sz" if item == ASSET_TYPE_STOCK else None,
                ),
                source=f"unit_test:identity_bootstrap:{item}",
                raw_symbol=symbol,
            )
        return SimpleNamespace(stored_count=1)


class _IndexBenchmark:
    def __init__(self, index_asset_id):
        self.index_asset_id = index_asset_id

    def resolve(self, asset, profile, as_of_date):
        return (
            self.index_asset_id
            if profile.default_benchmark_role != "fund_category"
            else None
        )


class _Industries:
    def __init__(self, industries, *, fail=False):
        self.industries = industries
        self.fail = fail

    def fetch_stock_industries(self, symbols):
        if self.fail:
            raise OSError("industry provider unavailable")
        return [
            StockIndustryInfo(
                symbol=symbol,
                name=symbol,
                industry_name=self.industries[symbol],
                source="unit_test:industry",
            )
            for symbol in symbols
            if symbol in self.industries
        ]


if __name__ == "__main__":
    unittest.main()
