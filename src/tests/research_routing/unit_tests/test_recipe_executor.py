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
    ResearchIntent,
    ResearchQuestionType,
    ResearchTaskSpec,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    SubjectRef,
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
    DiscoveryFunnelRunner,
    SectorBriefRunner,
    SingleAssetRecipeRunner,
)
from src.research_routing import (
    QuestionRouter,
    RecipeExecutor,
    RecipeRunStatus,
    StepOutcome,
    StepOutcomeStatus,
)
from src.discovery_funnel import DiscoveryScope
from src.sector_data import SwIndustry
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

    def test_holding_review_checks_recorded_theses(self):
        calls = []

        class Theses:
            def __init__(self, theses):
                self.theses = theses

            def check(self, asset_id, *, as_of_date, user_id=None):
                calls.append((asset_id, as_of_date, user_id))
                return {"asset_id": asset_id, "theses": self.theses}

        def run(theses, request):
            executor = RecipeExecutor(
                router=QuestionRouter(),
                single_asset=SingleAssetRecipeRunner(planner=self.planner, pipeline=self.pipeline),
                ledger=self.ledger,
                thesis=Theses(theses),
            )
            return {item.step_id: item for item in executor.run(request).step_outcomes}

        missing = run([], self.requests["Q8"])["thesis_check"]
        self.assertEqual(missing.status, StepOutcomeStatus.GAP)
        self.assertIn("record-thesis", missing.summary)
        found = run(
            [{"summary": "提价驱动", "status_label": "偏离", "reconstructed": True}],
            self.requests["Q8"],
        )["thesis_check"]
        self.assertEqual(found.status, StepOutcomeStatus.COMPLETED)
        self.assertEqual(found.summary, "提价驱动：偏离（事后重建）")
        self.assertEqual(calls[0][0], self.requests["Q8"].subject_refs[0].resolved_id)
        self.assertNotIn("thesis_check", run([], self.requests["Q3"]))

    def test_holding_review_builds_and_records_the_exit_plan(self):
        class Plans:
            def run(self, *, user_id, asset_id, as_of_date):
                return {
                    "plan": {"behavioral_hints": [{"title": "以成本价为锚", "evidence": "浮亏 20%"}]},
                    "lines": ["估值透支｜已设定｜分批减持：乐观情景每股 60 元"],
                    "ledger_entries": [{"entry_id": "ledger-exit-1", "condition_id": "valuation-0"}],
                }

        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(planner=self.planner, pipeline=self.pipeline),
            ledger=self.ledger,
            exit_plan=Plans(),
        )
        run = executor.run(replace(self.requests["Q8"], user_id="user:a"))
        outcomes = {item.step_id: item for item in run.step_outcomes}
        self.assertEqual(outcomes["exit_plan"].status, StepOutcomeStatus.COMPLETED)
        self.assertIn("估值透支", outcomes["exit_plan"].summary)
        self.assertEqual(outcomes["behavioral_review"].summary, "以成本价为锚：浮亏 20%")
        self.assertIn("ledger-exit-1", run.ledger_entry_ids)

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

    def test_portfolio_review_runs_the_diagnosis(self):
        calls = []
        diagnosis = SimpleNamespace(
            run=lambda **kwargs: calls.append(kwargs) or {"main_risks": ["风险贡献最大的持仓：A"], "report_path": "r.md"}
        )
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(planner=self.planner, pipeline=self.pipeline),
            ledger=self.ledger,
            portfolio=_Portfolio([{"asset_id": "asset:510300", "quantity": 100.0}]),
            portfolio_diagnosis=diagnosis,
        )
        request = replace(self.requests["Q10"], intents=(ResearchIntent.EVALUATE,))

        run = executor.run(request, cash=20000.0)

        self.assertEqual(run.routing.recipe_ids[0], "portfolio_review")
        outcome = next(item for item in run.step_outcomes if item.step_id == "portfolio_diagnosis")
        self.assertEqual(outcome.status, StepOutcomeStatus.COMPLETED)
        self.assertEqual(calls, [{"user_id": request.user_id, "as_of_date": request.as_of_date, "cash": 20000.0}])

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

    def test_sector_brief_resolves_shenwan_industry_and_records_archive(self):
        sector = _Sector()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=self.ledger,
            sector=sector,
        )

        run = executor.run(self.requests["Q2"])
        unknown = executor.run(
            replace(self.requests["Q2"], subject_refs=(SubjectRef("快消"),))
        )

        definition, archive = run.step_outcomes
        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(definition.step_id, "sector_definition")
        self.assertEqual(definition.artifacts["member_count"], 3)
        self.assertEqual(archive.step_id, "sector_fundamentals")
        self.assertEqual(run.ledger_entry_ids, ("ledger-sector",))
        self.assertEqual(sector.calls, [(AS_OF, "850818")])
        self.assertIn("（估值、宽度）", archive.summary)
        (needs_input,) = unknown.step_outcomes
        self.assertEqual(unknown.status, RecipeRunStatus.NOT_EXECUTED)
        self.assertEqual(needs_input.status, StepOutcomeStatus.NEEDS_INPUT)
        self.assertIn("sector:sw:", needs_input.summary)

    def test_sector_runner_matches_shenwan_names_and_resolved_ids(self):
        catalog = (
            SwIndustry("801120", "食品饮料", 1, None, 120),
            SwIndustry("801125", "白酒Ⅱ", 2, "801120", 19),
            SwIndustry("851251", "白酒Ⅲ", 3, "801125", 19),
        )
        data = SimpleNamespace(
            industries=lambda **_: catalog,
            industry=lambda code: {item.code: item for item in catalog}.get(code),
            index_asset=lambda code: SimpleNamespace(asset_id=f"asset:sw-{code}"),
            master_data=SimpleNamespace(
                find_assets=lambda **_: [
                    SimpleNamespace(asset_id="asset:sw-000300", exchange="sw"),
                    SimpleNamespace(asset_id="asset:csi300", exchange="sh"),
                ]
            ),
        )
        runner = SectorBriefRunner(sector_data=data, recorder=None)

        self.assertEqual(runner.resolve(SubjectRef("白酒")).code, "801125")
        self.assertEqual(runner.resolve(SubjectRef("申万食品饮料")).code, "801120")
        self.assertEqual(
            runner.resolve(SubjectRef("白酒", "sector:sw:851251")).code,
            "851251",
        )
        self.assertIsNone(runner.resolve(SubjectRef("快消")))
        self.assertEqual(
            [i.code for i in runner.resolve_many(SubjectRef("快消", "sector:sw:801120,851251"))],
            ["801120", "851251"],
        )
        self.assertEqual(runner.resolve_many(SubjectRef("快消", "sector:sw:801120,999999")), [])
        template = runner.view_template(
            catalog[0], request_id="q9", user_id="user:a", as_of_date=AS_OF, horizon="medium_short"
        )
        self.assertEqual(
            template["settlement_rule"],
            {"method": "price_relative", "observed_id": "asset:sw-801120", "benchmark_id": "asset:csi300"},
        )

    def test_sector_discovery_runs_the_funnel_for_a_resolved_scope(self):
        funnel = _Funnel()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=self.ledger,
            funnel=funnel,
        )

        unresolved = executor.run(self.requests["Q6"])
        run = executor.run(
            replace(
                self.requests["Q6"],
                subject_refs=(SubjectRef("新能源", "sector:sw:801730"),),
            )
        )

        (needs_input,) = unresolved.step_outcomes
        self.assertEqual(needs_input.status, StepOutcomeStatus.NEEDS_INPUT)
        self.assertIn("symbols:", needs_input.summary)
        scope, funnel_step, packs, event = run.step_outcomes
        self.assertEqual(event.status, StepOutcomeStatus.GAP)
        self.assertIn("601012", packs.summary)
        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(scope.step_id, "research_scope")
        self.assertEqual(funnel_step.artifacts["run_id"], "discovery-funnel:x")
        self.assertIn("质量—估值", funnel_step.summary)
        self.assertEqual(packs.step_id, "multi_strategy_priority")
        self.assertIn("1 个完成", packs.summary)
        self.assertEqual(funnel.calls, [("801730", AS_OF)])

    def test_funnel_runner_resolves_scopes_and_builds_base_packs(self):
        service = _FunnelService()
        baseline = _BaselineRunner()
        master_data = SimpleNamespace(
            find_assets=lambda standard_symbol, asset_type: (
                [SimpleNamespace(asset_id="asset:stock-600519")] if standard_symbol == "600519" else []
            )
        )
        runner = DiscoveryFunnelRunner(funnel=service, single_asset=baseline, master_data=master_data)

        self.assertEqual(runner.resolve_scope(SubjectRef("沪深300", "index:000300")).code, "000300")
        self.assertEqual(
            runner.resolve_scope(SubjectRef("白酒龙头", "symbols:600519,000858")).symbols,
            ("600519", "000858"),
        )
        self.assertEqual(runner.resolve_scope(SubjectRef("A股", "market:cn_a")).kind, "market")
        self.assertIsNone(runner.resolve_scope(SubjectRef("新能源")))
        payload = runner.run(DiscoveryScope("market"), as_of_date=AS_OF, base_packs=2)

        self.assertEqual(service.calls, [2])
        self.assertEqual([p["status"] for p in payload["base_packs"]], ["completed", "blocked"])
        self.assertEqual(service.saved["base_packs"][0]["report_path"], "r.md")
        self.assertEqual(baseline.asset_ids, ["asset:stock-600519", None])

    def test_sector_view_records_once_then_archives_industries_and_runs_funnel(self):
        sector = _Sector()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(
                planner=self.planner,
                pipeline=self.pipeline,
            ),
            ledger=self.ledger,
            sector=sector,
            funnel=_Funnel(),
        )
        request = replace(self.requests["Q9"], horizon=ResearchHorizon.MEDIUM_SHORT)

        unresolved = executor.run(request)
        resolved = executor.run(
            replace(request, subject_refs=(SubjectRef("快消", "sector:sw:801730"),))
        )

        view, needs_input = unresolved.step_outcomes
        self.assertEqual(needs_input.status, StepOutcomeStatus.NEEDS_INPUT)
        self.assertIn("sector:sw:<代码>[,<代码>...]", needs_input.summary)
        self.assertEqual(
            [item.step_id for item in resolved.step_outcomes],
            ["record_user_view", "sector_brief", "sector_discovery", "candidate_decisions"],
        )
        self.assertEqual(resolved.step_outcomes[0].artifacts, view.artifacts)
        self.assertEqual(len(self.ledger.list_entries(user_id="user:a")), 1)
        brief = resolved.step_outcomes[1]
        self.assertEqual(brief.artifacts["view_templates"][0]["subject_id"], "sector:sw:801730")
        self.assertIn("ledger-sector", resolved.ledger_entry_ids)
        self.assertEqual(sector.calls, [(AS_OF, "801730")])

    def test_plan_summary_lists_active_actions_and_review_dates(self):
        self.ledger.record(
            (_action(),),
            scope=LedgerScope.PERSONAL,
            user_id="user:a",
            source_ref="final-report:test",
        )

        run = self._executor().run(self.requests["Q10"])

        summary, plan = run.step_outcomes
        self.assertEqual(run.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(len(summary.artifacts["actions"]), 1)
        self.assertEqual(plan.step_id, "plan_object")
        self.assertEqual(plan.status, StepOutcomeStatus.GAP)

    def test_plan_summary_leaves_out_objective_actions(self):
        self.ledger.record((_action(),), scope=LedgerScope.OBJECTIVE, user_id=None, source_ref="dev-seed:test")

        run = self._executor().run(self.requests["Q10"])

        self.assertEqual(run.step_outcomes[0].artifacts["actions"], [])

    def test_allocation_conflict_asks_the_user_instead_of_adjusting(self):
        conflict = {
            "status": "conflict",
            "allocation": {"summary": ["冲突：回撤约束只允许权益到 45%"]},
            "conflict": {"binding": ["可接受回撤"]},
            "before": {},
            "summary": ["冲突", "调整方案未生成", "选项一", "选项二", "记录新版本"],
        }
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(planner=self.planner, pipeline=self.pipeline),
            ledger=self.ledger,
            adjustment=SimpleNamespace(run=lambda **_: conflict),
        )

        outcomes = executor._adjust(self.requests["Q10"], cash=1000.0)

        self.assertEqual([item.status for item in outcomes], [StepOutcomeStatus.COMPLETED] + [StepOutcomeStatus.NEEDS_INPUT] * 3)
        self.assertIn("选项二", outcomes[1].summary)

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

    def test_strategy_recipe_asks_to_register_unknown_rules_and_runs_registered_ones(self):
        strategies = _Strategies()
        executor = RecipeExecutor(
            router=QuestionRouter(),
            single_asset=SingleAssetRecipeRunner(planner=self.planner, pipeline=self.pipeline),
            ledger=self.ledger,
            strategy=strategies,
        )

        unknown = executor.run(self.requests["Q4"])
        (ask,) = unknown.step_outcomes
        self.assertEqual(ask.status, StepOutcomeStatus.NEEDS_INPUT)
        self.assertIn("ma_timing", ask.artifacts["templates"])

        known = executor.run(replace(self.requests["Q4"], subject_refs=(SubjectRef("etf_dm"),)))
        outcomes = {item.step_id: item for item in known.step_outcomes}
        self.assertEqual(known.status, RecipeRunStatus.EXECUTED)
        self.assertEqual(
            [outcomes[step].status for step in ("strategy_spec", "selection_samples", "backtest_validation", "regime_analysis", "attempt_discount")],
            [StepOutcomeStatus.COMPLETED] * 5,
        )
        self.assertIn("不适用", outcomes["selection_samples"].summary)
        self.assertNotIn("均线之上", outcomes["regime_analysis"].summary)
        self.assertIn("截至 2026-10-28 处于：均线之下、估值中位", outcomes["regime_analysis"].summary)
        self.assertEqual(outcomes["forward_tracking"].status, StepOutcomeStatus.GAP)
        self.assertIn("transition forward_tracking", outcomes["forward_tracking"].next_actions[0])
        self.assertIn("DSR", outcomes["attempt_discount"].summary)
        self.assertEqual(strategies.tracked, [])


class _Strategies:
    def __init__(self):
        self.tracked = []

    def find(self, text, as_of_date):
        return "etf_dm" if text == "etf_dm" else None

    def templates(self):
        return {"templates": [{"template_id": "ma_timing"}], "not_expressible": ["做空"]}

    def show(self, strategy_id, as_of_date):
        return {
            "archive": {
                "name": "ETF 双动量", "version": "v1", "mechanism": "动量", "universe": "ETF", "parameters": {},
                "source_refs": ["docs/x.md"], "failure_conditions": ["跑输"], "limitations": [],
            },
            "state": "validating",
            "state_label": "验证中",
            "family_backtests": {"runs": 3, "trials": 2},
        }

    def evaluate(self, strategy_id, as_of_date):
        return {
            "evaluation_id": "strategy-evaluation:1",
            "full_sample": {
                "sample": {}, "metrics": {}, "comparison": [], "capacity": {},
                "significance": {"dsr": 0.7, "verdict": "单次看显著，但按 12 次尝试折扣后不显著（DSR 70%）"},
            },
            "rolling_oos": {"verdict": "不劣于简单基准"},
            "neighbourhood": {"verdict": "参数稳定"},
            "regimes": {
                "dimensions": {"trend": [{"label": "均线之上", "readable": True, "reading": "无显著差别"}]},
                "exposure_driven": ["trend"],
                "current": {"date": "2026-10-28", "trend": "均线之下", "volatility": None, "valuation": "估值中位"},
            },
            "validation": {}, "gaps": [], "limitations": [],
            "lifecycle_state": "validating", "state_label": "验证中",
            "next_state": "forward_tracking", "next_blockers": [],
        }

    def track(self, strategy_id, as_of_date):
        self.tracked.append(strategy_id)
        return {}


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


class _Sector:
    def __init__(self):
        self.calls = []

    def resolve(self, ref):
        if ref.text == "半导体设备":
            return SimpleNamespace(code="850818", name="半导体设备", level=3)
        return None

    def resolve_many(self, ref):
        if ref.resolved_id and ref.resolved_id.startswith("sector:sw:"):
            return [
                SimpleNamespace(code=code, name=f"行业{code}", level=1)
                for code in ref.resolved_id.removeprefix("sector:sw:").split(",")
            ]
        industry = self.resolve(ref)
        return [industry] if industry else []

    def view_template(self, industry, *, request_id, user_id, as_of_date, horizon):
        return {"subject_id": f"sector:sw:{industry.code}", "user_id": user_id}

    def definition(self, industry):
        return {"industry": {"code": industry.code}, "member_count": 3}

    def run(self, as_of_date, *, code):
        self.calls.append((as_of_date, code))
        return {
            "assessment": {"label": "申万半导体设备：估值偏高", "dimensions": [{"name": "估值"}, {"name": "宽度"}]},
            "ledger_entry_id": "ledger-sector",
        }


class _FunnelService:
    def __init__(self):
        self.calls = []
        self.saved = None

    def run(self, scope, *, as_of_date, base_pack_count, **options):
        self.calls.append(base_pack_count)
        return {"run_id": "run", "selected_for_base_pack": ["600519", "000858"]}

    def save(self, payload):
        self.saved = payload


class _BaselineRunner:
    def __init__(self):
        self.asset_ids = []

    def run_baseline(self, *, request, subject, holding, asset_type=None):
        self.asset_ids.append(subject.asset_id)
        if subject.text == "000858":
            raise RuntimeError("source down")
        return (
            StepOutcome(
                "baseline",
                StepOutcomeStatus.COMPLETED,
                "ok",
                artifacts={"report_path": "r.md"},
            ),
        )


class _Funnel:
    def __init__(self):
        self.calls = []

    def resolve_scope(self, ref):
        if ref.resolved_id == "sector:sw:801730":
            return SimpleNamespace(code="801730")
        return None

    def run(self, scope, *, as_of_date):
        self.calls.append((scope.code, as_of_date))
        return {
            "run_id": "discovery-funnel:x",
            "scope": {"label": "申万电力设备", "member_count": 280},
            "excluded": {},
            "context": {"median_pe_ttm": 30.0},
            "strategies": [
                {"name": "质量—估值", "computed": True},
                {"name": "高质量深度回撤", "computed": False},
            ],
            "candidate_count": 2,
            "candidates": [{"symbol": "300750"}, {"symbol": "601012"}],
            "gaps": [],
            "base_packs": [
                {"symbol": "300750", "status": "completed", "summary": "基础研究完成"},
                {"symbol": "601012", "status": "blocked", "summary": "规划未就绪"},
            ],
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
