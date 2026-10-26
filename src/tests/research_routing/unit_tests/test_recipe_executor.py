import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from src.research_contracts import (
    ActionTrigger,
    ActionType,
    DecisionIntent,
    PositionState,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
)
from src.research_ledger import (
    LedgerScope,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
)
from src.research_orchestration import (
    ResearchPlanningStatus,
    ResearchRunStatus,
)
from src.research_orchestration.routing_adapters import (
    SingleAssetRecipeRunner,
)
from src.research_routing import (
    QuestionRouter,
    RecipeExecutor,
    RecipeRunStatus,
    StepOutcome,
    StepOutcomeStatus,
)
from src.tests.research_contracts.unit_tests.test_research_requests import (
    AS_OF,
    representative_requests,
)


class RecipeExecutorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = ResearchLedgerService(
            SQLiteResearchLedgerRepository(
                Path(self.temp_dir.name) / "ledger.sqlite3"
            )
        )
        self.planner = _Planner(ResearchPlanningStatus.READY)
        self.pipeline = _Pipeline(ResearchRunStatus.COMPLETE)
        self.discovery = _Discovery()
        self.requests = representative_requests()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _executor(self, ledger=None, portfolio=None):
        return RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=ledger or self.ledger,
            discovery=self.discovery,
            portfolio=portfolio,
        )

    def test_asset_recipe_runs_the_planned_baseline_task(self):
        run = self._executor().run(self.requests["Q3"])

        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        (planning,) = self.planner.requests
        (pipeline_request,) = self.pipeline.requests
        self.assertEqual(planning.asset_id, "asset:600519")
        self.assertEqual(
            planning.decision_intent,
            DecisionIntent.EVALUATE_PURCHASE,
        )
        self.assertIs(pipeline_request.task, self.planner.task)
        baseline = run.step_outcomes[-1]
        self.assertEqual(
            baseline.artifacts,
            {
                "workflow_run_id": "workflow:1",
                "handoff_id": "handoff:1",
                "report_path": str(Path("report/baseline.md")),
            },
        )
        self.assertTrue(baseline.next_actions)

    def test_holding_review_plans_with_review_intent(self):
        self._executor().run(self.requests["Q8"])

        self.assertEqual(
            self.planner.requests[0].decision_intent,
            DecisionIntent.REVIEW_HOLDING,
        )

    def test_asset_recipe_saves_and_links_decision_brief(self):
        briefs = _BriefStore()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
                decision_briefs=briefs,
            ),
            ledger=self.ledger,
            discovery=self.discovery,
        )

        q3 = executor.run(replace(self.requests["Q3"], user_id="user:a"))
        executor.run(self.requests["Q8"])

        evaluate, review = briefs.calls
        self.assertEqual(evaluate["user_id"], "user:a")
        self.assertEqual(evaluate["asset_id"], "asset:600519")
        self.assertEqual(
            evaluate["decision_intent"],
            DecisionIntent.EVALUATE_PURCHASE,
        )
        self.assertIsNone(evaluate["position_state"])
        self.assertEqual(review["position_state"], PositionState.HELD)
        self.assertEqual(
            self.pipeline.requests[0].decision_brief_id,
            "brief:1",
        )
        self.assertEqual(
            q3.step_outcomes[-1].artifacts["decision_brief_id"],
            "brief:1",
        )

    def test_unready_plan_and_blocked_baseline_stay_blocked(self):
        self.planner.status = ResearchPlanningStatus.NEEDS_CLARIFICATION
        unready = self._executor().run(self.requests["Q3"])
        self.assertEqual(unready.status, RecipeRunStatus.BLOCKED)
        self.assertEqual(self.pipeline.requests, [])

        self.planner.status = ResearchPlanningStatus.READY
        self.pipeline.status = ResearchRunStatus.BLOCKED
        blocked = self._executor().run(self.requests["Q3"])
        self.assertEqual(blocked.status, RecipeRunStatus.BLOCKED)
        self.assertEqual(
            blocked.step_outcomes[-1].status,
            StepOutcomeStatus.BLOCKED,
        )

    def test_execution_validation_error_is_a_blocked_step(self):
        self.pipeline.error = ValueError("Bank task requires benchmark_asset_id")

        run = self._executor().run(self.requests["Q3"])

        self.assertEqual(run.status, RecipeRunStatus.BLOCKED)
        self.assertEqual(run.step_outcomes[-1].step_id, "baseline")
        self.assertIn("benchmark_asset_id", run.step_outcomes[-1].summary)

    def test_portfolio_steps_read_recorded_holdings_or_report_a_gap(self):
        request = replace(
            self.requests["Q7"],
            horizon=ResearchHorizon.MEDIUM_SHORT,
        )

        empty = self._executor(portfolio=_Portfolio([])).run(request)
        held = self._executor(
            portfolio=_Portfolio([{"asset_id": "asset:510300", "quantity": 100.0}])
        ).run(request)

        empty_read = empty.step_outcomes[-1]
        held_read = held.step_outcomes[-1]
        self.assertEqual(empty_read.step_id, "portfolio_impact")
        self.assertEqual(empty_read.status, StepOutcomeStatus.GAP)
        self.assertEqual(held_read.status, StepOutcomeStatus.COMPLETED)
        self.assertEqual(
            held_read.artifacts["holdings"][0]["asset_id"],
            "asset:510300",
        )

    def test_view_test_records_the_user_view_once(self):
        request = replace(
            self.requests["Q7"],
            horizon=ResearchHorizon.MEDIUM_SHORT,
        )

        first = self._executor().run(request)
        second = self._executor().run(request)

        self.assertEqual(first.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(first.ledger_entry_ids, second.ledger_entry_ids)
        (entry,) = self.ledger.list_entries(user_id="user:a")
        self.assertEqual(entry.scope, LedgerScope.PERSONAL)
        self.assertEqual(entry.artifact.proposition, "我觉得快要经济危机了")
        self.assertEqual(self.ledger.list_entries(user_id="user:b"), ())

    def test_view_test_adds_macro_archive_and_base_rate(self):
        market = _Market()
        macro = _Market()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=self.ledger,
            market=market,
            macro=macro,
            analogs=_Analogs(),
        )

        run = executor.run(
            replace(self.requests["Q7"], horizon=ResearchHorizon.MEDIUM_SHORT)
        )

        recorded, indicators, base_rate = run.step_outcomes[:3]
        self.assertEqual(recorded.step_id, "record_user_view")
        self.assertIn("record-view", recorded.next_actions[0])
        self.assertEqual(indicators.step_id, "leading_indicators")
        self.assertEqual(indicators.status, StepOutcomeStatus.COMPLETED)
        self.assertEqual(base_rate.step_id, "base_rate")
        self.assertEqual(base_rate.artifacts, {"analogs": {"metrics": []}})
        self.assertEqual(macro.dates, [AS_OF])
        self.assertEqual(market.dates, [])

    def test_view_without_time_window_needs_input_and_writes_nothing(self):
        run = self._executor().run(self.requests["Q7"])

        self.assertEqual(run.status, RecipeRunStatus.NOT_EXECUTED)
        self.assertEqual(
            run.step_outcomes[0].status,
            StepOutcomeStatus.NEEDS_INPUT,
        )
        self.assertEqual(self.ledger.list_entries(user_id="user:a"), ())

    def test_blocked_routing_and_gap_only_recipes_have_no_side_effects(self):
        recording = _RecordingLedger()
        executor = self._executor(ledger=recording)

        for question in ("Q5",):
            with self.subTest(question=question):
                run = executor.run(self.requests[question])
                self.assertEqual(run.status, RecipeRunStatus.NOT_EXECUTED)
                self.assertEqual(run.step_outcomes, ())
        self.assertEqual(recording.calls, [])
        self.assertEqual(self.planner.requests, [])

    def test_market_brief_runs_market_and_macro_archives(self):
        market = _Market()
        macro = _Market()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=self.ledger,
            market=market,
            macro=macro,
        )

        run = executor.run(self.requests["Q1"])
        market.error = RuntimeError("all sources down")
        failed = executor.run(self.requests["Q1"])

        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        market_outcome, macro_outcome, analog_outcome = run.step_outcomes
        self.assertEqual(market_outcome.step_id, "market_state")
        self.assertEqual(macro_outcome.step_id, "macro_state")
        self.assertEqual(analog_outcome.status, StepOutcomeStatus.GAP)
        self.assertIn("估值中等", market_outcome.summary)
        self.assertEqual(run.ledger_entry_ids, ("ledger-market", "ledger-market"))
        self.assertEqual(market.dates, [AS_OF, AS_OF])
        self.assertEqual(macro.dates, [AS_OF, AS_OF])
        self.assertEqual(failed.status, RecipeRunStatus.BLOCKED)
        self.assertIn("all sources down", failed.step_outcomes[0].summary)
        self.assertEqual(failed.step_outcomes[1].status, StepOutcomeStatus.COMPLETED)

    def test_plan_summary_lists_active_actions_and_review_dates(self):
        self.ledger.record(
            (_action(),),
            scope=LedgerScope.PERSONAL,
            user_id="user:a",
            source_ref="final-report:test",
        )

        run = self._executor().run(self.requests["Q10"])

        (outcome,) = run.step_outcomes
        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(len(outcome.artifacts["actions"]), 1)
        self.assertIn("不是完整计划", outcome.summary)

    def test_discovery_runs_only_with_prepared_input(self):
        without = self._executor().run(self.requests["Q6"])
        with_input = self._executor().run(
            self.requests["Q6"],
            discovery_request="prepared-input",
        )

        self.assertEqual(without.status, RecipeRunStatus.NOT_EXECUTED)
        self.assertEqual(
            without.step_outcomes[0].status,
            StepOutcomeStatus.GAP,
        )
        self.assertEqual(with_input.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(self.discovery.requests, ["prepared-input"])


class _Planner:
    def __init__(self, status):
        self.status = status
        self.requests = []
        self.task = ResearchTaskSpec(
            task_id="task:test",
            asset_id="asset:600519",
            profile_id="ordinary_company",
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date=AS_OF,
        )

    def plan(self, request):
        self.requests.append(request)
        ready = self.status is ResearchPlanningStatus.READY
        return SimpleNamespace(
            status=self.status,
            task=self.task if ready else None,
            profile_id="ordinary_company" if ready else None,
            effective_decision_lanes=request.decision_lanes,
            issues=() if ready else ("代码对应多个标的",),
            routing_notes=(),
        )


class _Pipeline:
    def __init__(self, status):
        self.status = status
        self.error = None
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status=self.status,
            workflow_run=SimpleNamespace(workflow_run_id="workflow:1"),
            handoff=(
                None
                if self.status is ResearchRunStatus.BLOCKED
                else SimpleNamespace(handoff_id="handoff:1")
            ),
            report_path=Path("report/baseline.md"),
        )


class _BriefStore:
    def __init__(self):
        self.calls = []

    def save_decision_brief(self, context, **kwargs):
        self.calls.append({"user_id": context.user_id, **kwargs})
        return SimpleNamespace(brief_id="brief:1")


class _Market:
    def __init__(self):
        self.dates = []
        self.error = None

    def run(self, as_of_date):
        self.dates.append(as_of_date)
        if self.error is not None:
            raise self.error
        return {
            "assessment": {"label": "估值中等；宽度低"},
            "ledger_entry_id": "ledger-market",
        }


class _Analogs:
    def market_defaults(self, as_of_date):
        return {"metrics": []}


class _Discovery:
    def __init__(self):
        self.requests = []

    def run(self, discovery_request):
        self.requests.append(discovery_request)
        return StepOutcome(
            "event_name_discovery",
            StepOutcomeStatus.COMPLETED,
            "完成",
        )


class _RecordingLedger:
    def __init__(self):
        self.calls = []

    def record_user_view(self, view, *, source_ref):
        self.calls.append(("record_user_view", view))

    def list_entries(self, **filters):
        self.calls.append(("list_entries", filters))
        return ()


class _Portfolio:
    def __init__(self, holdings):
        self.holdings = holdings

    def portfolio_summary(self, user_id):
        return {"holdings": self.holdings, "latest_valuation": None}


def _action():
    return SettleableAction(
        action_id="action:test",
        asset_id="asset:600519",
        action_type=ActionType.BUY,
        horizon=ResearchHorizon.MEDIUM_LONG,
        trigger=ActionTrigger(
            TriggerKind.CONDITIONAL,
            valid_from=AS_OF,
            valid_until="2027-01-23",
            observability_key="pe_ttm",
            condition="pe_ttm <= 20",
        ),
        quantity_rule=QuantityRule(
            QuantityRuleKind.UNAVAILABLE,
            description="缺少组合约束",
        ),
        settlement_rule=SettlementRule(
            SettlementMethod.PRICE_RELATIVE,
            "asset:600519",
        ),
    )


if __name__ == "__main__":
    unittest.main()
