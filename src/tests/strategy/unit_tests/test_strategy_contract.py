import json
import unittest
from decimal import Decimal

from src.research_workflow import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
)
from src.strategy import (
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    ConditionOutcome,
    MetricCondition,
    StrategyExecutor,
    StrategyInput,
    StrategyMaturity,
    StrategyMetricInput,
    StrategyRegistry,
    StrategyRunStatus,
    StrategySpec,
    WeightedCondition,
    evaluate_condition,
)


class StrategyContractTest(unittest.TestCase):
    def setUp(self):
        self.spec = _strategy_spec()
        self.executor = StrategyExecutor()

    def test_passing_strategy_is_explainable(self):
        result = self.executor.execute(
            self.spec,
            _input(),
        )

        self.assertEqual(result.status, StrategyRunStatus.PASS)
        self.assertEqual(result.score, Decimal("1"))
        self.assertEqual(result.confidence, Decimal("1"))
        self.assertEqual(result.valid_until, "2026-04-30")
        self.assertIn("quality_gate", result.matched_conditions)
        self.assertNotIn(
            "veto_leverage",
            result.failed_conditions,
        )
        self.assertEqual(
            result.metric_versions["roe"],
            "fundamental_v1",
        )

    def test_veto_overrides_high_score(self):
        strategy_input = _input(debt_to_assets="0.9")
        result = self.executor.execute(self.spec, strategy_input)

        self.assertEqual(result.status, StrategyRunStatus.VETOED)
        self.assertEqual(result.signal, "vetoed")
        self.assertIn("veto_leverage", result.vetoed_conditions)
        self.assertIn(
            "veto_leverage",
            result.counterevidence,
        )

    def test_hard_filter_failure_differs_from_missing_data(self):
        failed = self.executor.execute(
            self.spec,
            _input(roe="0.10"),
        )
        missing_metrics = _input().metrics
        del missing_metrics["roe"]
        missing = self.executor.execute(
            self.spec,
            _input(metrics=missing_metrics),
        )

        self.assertEqual(failed.status, StrategyRunStatus.FAIL)
        self.assertIn("roe_min", failed.failed_conditions)
        self.assertEqual(
            missing.status,
            StrategyRunStatus.INSUFFICIENT_DATA,
        )
        self.assertEqual(missing.missing_metrics, ("roe",))
        self.assertLess(
            missing.input_completeness,
            Decimal("1"),
        )

    def test_weighted_score_uses_declared_threshold(self):
        result = self.executor.execute(
            self.spec,
            _input(roic="0.10"),
        )

        self.assertEqual(result.score, Decimal("0.6"))
        self.assertEqual(result.status, StrategyRunStatus.PASS)
        self.assertIn("roic_score", result.failed_conditions)

    def test_missing_required_evidence_is_insufficient(self):
        strategy_input = _input(evidence_types=())
        result = self.executor.execute(self.spec, strategy_input)

        self.assertEqual(
            result.status,
            StrategyRunStatus.INSUFFICIENT_DATA,
        )
        self.assertEqual(
            result.missing_evidence_types,
            ("financial_statement",),
        )

    def test_profile_and_horizon_are_enforced(self):
        with self.assertRaisesRegex(
            ValueError,
            "does not support profile",
        ):
            self.executor.execute(
                self.spec,
                _input(profile_id=PROFILE_EXCHANGE_FUND),
            )
        with self.assertRaisesRegex(
            ValueError,
            "does not support horizon",
        ):
            self.executor.execute(
                self.spec,
                _input(horizon=ResearchHorizon.SHORT),
            )

    def test_and_or_condition_tristate_rules(self):
        missing_condition = MetricCondition(
            condition_id="missing",
            metric_name="missing_metric",
            operator=ConditionOperator.GT,
            threshold=Decimal("0"),
            description="Missing input",
        )
        true_condition = MetricCondition(
            condition_id="true",
            metric_name="roe",
            operator=ConditionOperator.GT,
            threshold=Decimal("0.1"),
            description="ROE positive",
        )
        false_condition = MetricCondition(
            condition_id="false",
            metric_name="roe",
            operator=ConditionOperator.LT,
            threshold=Decimal("0"),
            description="ROE negative",
        )
        or_result = evaluate_condition(
            ConditionGroup(
                condition_id="or",
                logic=ConditionLogic.OR,
                children=(missing_condition, true_condition),
                description="OR group",
            ),
            _input(),
        )
        and_result = evaluate_condition(
            ConditionGroup(
                condition_id="and",
                logic=ConditionLogic.AND,
                children=(missing_condition, false_condition),
                description="AND group",
            ),
            _input(),
        )

        self.assertEqual(or_result.outcome, ConditionOutcome.TRUE)
        self.assertEqual(and_result.outcome, ConditionOutcome.FALSE)

    def test_strategy_spec_round_trips_and_registry_is_versioned(self):
        payload = json.loads(json.dumps(self.spec.to_dict()))
        restored = StrategySpec.from_dict(payload)
        registry = StrategyRegistry()
        registry.register(restored)

        self.assertEqual(restored, self.spec)
        self.assertEqual(
            registry.require(
                self.spec.strategy_id,
                self.spec.version,
            ),
            self.spec,
        )
        with self.assertRaisesRegex(
            ValueError,
            "already registered",
        ):
            registry.register(self.spec)

    def test_undeclared_metric_and_future_input_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "undeclared required_metrics",
        ):
            StrategySpec(
                strategy_id="broken",
                name="Broken",
                version="v1",
                maturity=StrategyMaturity.RESEARCH_RULE,
                supported_profiles=(
                    PROFILE_ORDINARY_COMPANY,
                ),
                horizons=(ResearchHorizon.MEDIUM_LONG,),
                required_metrics=(),
                required_evidence_types=(),
                data_window_days=365,
                valid_for_days=30,
                hard_filters=(
                    MetricCondition(
                        condition_id="uses_roe",
                        metric_name="roe",
                        operator=ConditionOperator.GT,
                        threshold=Decimal("0"),
                        description="Uses undeclared ROE",
                    ),
                ),
            )
        with self.assertRaisesRegex(
            ValueError,
            "exceeds strategy as_of_date",
        ):
            StrategyInput(
                asset_id="asset:test",
                profile_id=PROFILE_ORDINARY_COMPANY,
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-03-31",
                metrics={
                    "roe": StrategyMetricInput(
                        metric_name="roe",
                        value=Decimal("0.2"),
                        as_of_date="2026-04-01",
                        calculation_version="fundamental_v1",
                    )
                },
            )


def _strategy_spec():
    return StrategySpec(
        strategy_id="quality_value_contract_test",
        name="Quality Value Contract Test",
        version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        supported_profiles=(PROFILE_ORDINARY_COMPANY,),
        horizons=(ResearchHorizon.MEDIUM_LONG,),
        required_metrics=(
            "roe",
            "fcf_yield",
            "debt_to_assets",
            "pe_ttm",
            "roic",
        ),
        required_evidence_types=("financial_statement",),
        data_window_days=730,
        valid_for_days=30,
        hard_filters=(
            ConditionGroup(
                condition_id="quality_gate",
                logic=ConditionLogic.AND,
                children=(
                    MetricCondition(
                        condition_id="roe_min",
                        metric_name="roe",
                        operator=ConditionOperator.GTE,
                        threshold=Decimal("0.15"),
                        description="ROE at least 15%",
                    ),
                    MetricCondition(
                        condition_id="fcf_yield_min",
                        metric_name="fcf_yield",
                        operator=ConditionOperator.GTE,
                        threshold=Decimal("0.03"),
                        description="FCF yield at least 3%",
                    ),
                ),
                description="Quality hard gate",
            ),
        ),
        vetoes=(
            MetricCondition(
                condition_id="veto_leverage",
                metric_name="debt_to_assets",
                operator=ConditionOperator.GT,
                threshold=Decimal("0.8"),
                description="Excessive leverage",
            ),
        ),
        score_rules=(
            WeightedCondition(
                MetricCondition(
                    condition_id="pe_score",
                    metric_name="pe_ttm",
                    operator=ConditionOperator.LTE,
                    threshold=Decimal("25"),
                    description="PE no higher than 25",
                ),
                Decimal("0.6"),
            ),
            WeightedCondition(
                MetricCondition(
                    condition_id="roic_score",
                    metric_name="roic",
                    operator=ConditionOperator.GTE,
                    threshold=Decimal("0.12"),
                    description="ROIC at least 12%",
                ),
                Decimal("0.4"),
            ),
        ),
        passing_score=Decimal("0.6"),
        signal_on_pass="research_pass",
        signal_on_fail="research_fail",
        limitations=("Not backtested",),
    )


def _input(
    *,
    roe="0.2",
    fcf_yield="0.05",
    debt_to_assets="0.4",
    pe_ttm="20",
    roic="0.15",
    profile_id=PROFILE_ORDINARY_COMPANY,
    horizon=ResearchHorizon.MEDIUM_LONG,
    evidence_types=("financial_statement",),
    metrics=None,
):
    values = {
        "roe": roe,
        "fcf_yield": fcf_yield,
        "debt_to_assets": debt_to_assets,
        "pe_ttm": pe_ttm,
        "roic": roic,
    }
    metric_inputs = (
        metrics
        if metrics is not None
        else {
            name: StrategyMetricInput(
                metric_name=name,
                value=Decimal(value),
                as_of_date="2026-03-31",
                calculation_version="fundamental_v1",
            )
            for name, value in values.items()
        }
    )
    return StrategyInput(
        asset_id="asset:test",
        profile_id=profile_id,
        horizon=horizon,
        as_of_date="2026-03-31",
        metrics=metric_inputs,
        evidence_types=evidence_types,
    )


if __name__ == "__main__":
    unittest.main()
