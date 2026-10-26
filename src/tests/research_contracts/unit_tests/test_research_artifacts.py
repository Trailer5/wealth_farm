import unittest
from decimal import Decimal

from src.research_contracts import (
    ActionTrigger,
    ActionType,
    AssessmentDimension,
    ClaimComparison,
    Confidence,
    Plan,
    ProbabilityInterval,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    ReviewNode,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    StateAssessment,
    SubjectType,
    TargetRange,
    TriggerKind,
    View,
    ViewClaim,
    ViewOrigin,
)


AS_OF = "2026-10-23"


class StateAssessmentTest(unittest.TestCase):
    def test_round_trip_keeps_decimal_percentile(self):
        assessment = StateAssessment(
            assessment_id="market:2026-10-23",
            subject_type=SubjectType.MARKET,
            subject_id="market:a_share",
            as_of_date=AS_OF,
            dimensions=(
                AssessmentDimension(
                    name="valuation",
                    value="high",
                    evidence_refs=("csi_all_pe_ttm",),
                    historical_percentile=Decimal("0.85"),
                ),
            ),
            label="高估值、弱宽度",
            confidence=Confidence.MEDIUM,
            change_signals=("宽度连续四周改善",),
        )

        payload = assessment.to_dict()

        self.assertEqual(
            payload["dimensions"][0]["historical_percentile"],
            "0.85",
        )
        self.assertEqual(StateAssessment.from_dict(payload), assessment)

    def test_dimension_needs_evidence_and_valid_percentile(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            AssessmentDimension("valuation", "high", ())
        with self.assertRaisesRegex(ValueError, "within"):
            AssessmentDimension(
                "valuation",
                "high",
                ("pe",),
                historical_percentile=Decimal("1.2"),
            )


class ViewTest(unittest.TestCase):
    def test_directional_system_view_round_trips(self):
        view = _asset_view()

        self.assertTrue(view.directional)
        self.assertEqual(View.from_dict(view.to_dict()), view)

    def test_directional_view_needs_settlement_rule(self):
        with self.assertRaisesRegex(ValueError, "require settlement_rule"):
            _asset_view(settlement_rule=None)

    def test_relative_claim_needs_benchmark(self):
        with self.assertRaisesRegex(ValueError, "settlement benchmark"):
            _asset_view(
                settlement_rule=SettlementRule(
                    SettlementMethod.PRICE_RELATIVE,
                    "asset:600519",
                )
            )

    def test_absolute_claim_needs_threshold(self):
        with self.assertRaisesRegex(ValueError, "require a threshold"):
            ViewClaim("cpi_yoy", ClaimComparison.ABOVE)

    def test_user_view_is_recorded_without_claim(self):
        view = View(
            view_id="view:user:crisis",
            subject_type=SubjectType.MACRO,
            subject_id="macro:cn",
            origin=ViewOrigin.USER,
            user_id="user:a",
            proposition="快要经济危机了",
            horizon=ResearchHorizon.MEDIUM_SHORT,
            as_of_date=AS_OF,
            expires_on="2027-04-23",
            confidence=Confidence.LOW,
        )

        self.assertFalse(view.directional)
        with self.assertRaisesRegex(ValueError, "require user_id"):
            View.from_dict({**view.to_dict(), "user_id": None})

    def test_system_view_cannot_belong_to_user(self):
        with self.assertRaisesRegex(ValueError, "must not carry user_id"):
            _asset_view(user_id="user:a")

    def test_system_directional_view_needs_falsifiers(self):
        with self.assertRaisesRegex(ValueError, "require falsifiers"):
            _asset_view(falsifiers=())

    def test_asset_view_settles_on_its_subject(self):
        with self.assertRaisesRegex(ValueError, "subject asset"):
            _asset_view(
                settlement_rule=SettlementRule(
                    SettlementMethod.PRICE_RELATIVE,
                    "asset:other",
                    benchmark_id="index:000300",
                )
            )

    def test_expiry_must_follow_as_of_date(self):
        with self.assertRaisesRegex(ValueError, "expires_on"):
            _asset_view(expires_on=AS_OF)

    def test_probability_must_be_proper_interval(self):
        for low, high in (("0.6", "0.6"), ("0.7", "0.5"), ("-0.1", "0.4")):
            with self.subTest(low=low, high=high):
                with self.assertRaisesRegex(ValueError, "probability"):
                    ProbabilityInterval(Decimal(low), Decimal(high))
        with self.assertRaisesRegex(TypeError, "Decimal"):
            ProbabilityInterval(0.4, 0.6)


class SettlementRuleTest(unittest.TestCase):
    def test_metric_observation_has_no_benchmark(self):
        with self.assertRaisesRegex(ValueError, "does not use"):
            SettlementRule(
                SettlementMethod.METRIC_OBSERVATION,
                "cpi_yoy",
                benchmark_id="index:000300",
            )


class SettleableActionTest(unittest.TestCase):
    def test_conditional_action_round_trips(self):
        action = _action()

        self.assertEqual(
            SettleableAction.from_dict(action.to_dict()),
            action,
        )

    def test_conditional_trigger_needs_observable_condition(self):
        with self.assertRaisesRegex(ValueError, "observability_key"):
            ActionTrigger(
                TriggerKind.CONDITIONAL,
                valid_from=AS_OF,
                valid_until="2027-01-23",
                condition="pe_ttm <= 20",
            )

    def test_scheduled_trigger_stays_in_window(self):
        with self.assertRaisesRegex(ValueError, "within the valid window"):
            ActionTrigger(
                TriggerKind.SCHEDULED,
                valid_from=AS_OF,
                valid_until="2026-12-31",
                scheduled_on="2027-01-15",
            )

    def test_unavailable_quantity_needs_reason(self):
        with self.assertRaisesRegex(ValueError, "needs description"):
            QuantityRule(QuantityRuleKind.UNAVAILABLE)

    def test_action_settles_on_its_own_asset(self):
        with self.assertRaisesRegex(ValueError, "own asset"):
            _action(
                settlement_rule=SettlementRule(
                    SettlementMethod.METRIC_OBSERVATION,
                    "asset:600519",
                )
            )


class PlanTest(unittest.TestCase):
    def test_plan_round_trips_with_range_target(self):
        plan = _plan()

        self.assertEqual(Plan.from_dict(plan.to_dict()), plan)

    def test_point_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "low < high"):
            _target(low=Decimal("1800"), high=Decimal("1800"))

    def test_exact_date_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "more than one date"):
            _target(window_start="2027-06-30", window_end="2027-06-30")

    def test_plan_needs_unique_actions_and_review_nodes(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            _plan(actions=(_action(), _action()))
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            _plan(review_nodes=())

    def test_from_dict_rejects_float_decimals(self):
        payload = _plan().to_dict()
        payload["targets"][0]["low"] = 1500.0
        with self.assertRaisesRegex(TypeError, "decimal string"):
            Plan.from_dict(payload)


def _asset_view(**overrides) -> View:
    fields = {
        "view_id": "view:600519:consider",
        "subject_type": SubjectType.ASSET,
        "subject_id": "asset:600519",
        "origin": ViewOrigin.SYSTEM,
        "proposition": "未来一年跑赢沪深300",
        "horizon": ResearchHorizon.MEDIUM_LONG,
        "as_of_date": AS_OF,
        "expires_on": "2027-10-23",
        "confidence": Confidence.MEDIUM,
        "claim": ViewClaim("total_return", ClaimComparison.OUTPERFORM),
        "support_refs": ("evidence:roe",),
        "counter_refs": ("evidence:demand",),
        "probability": ProbabilityInterval(
            Decimal("0.55"),
            Decimal("0.65"),
        ),
        "falsifiers": ("连续两个季度批价下跌",),
        "settlement_rule": SettlementRule(
            SettlementMethod.PRICE_RELATIVE,
            "asset:600519",
            benchmark_id="index:000300",
        ),
    }
    fields.update(overrides)
    return View(**fields)


def _action(**overrides) -> SettleableAction:
    fields = {
        "action_id": "action:600519:buy",
        "asset_id": "asset:600519",
        "action_type": ActionType.BUY,
        "horizon": ResearchHorizon.MEDIUM_LONG,
        "trigger": ActionTrigger(
            TriggerKind.CONDITIONAL,
            valid_from=AS_OF,
            valid_until="2027-01-23",
            observability_key="pe_ttm",
            condition="pe_ttm <= 20",
        ),
        "quantity_rule": QuantityRule(
            QuantityRuleKind.POSITION_PLAN,
            reference="position:600519",
        ),
        "settlement_rule": SettlementRule(
            SettlementMethod.PRICE_RELATIVE,
            "asset:600519",
            benchmark_id="index:000300",
        ),
    }
    fields.update(overrides)
    return SettleableAction(**fields)


def _target(**overrides) -> TargetRange:
    fields = {
        "subject_id": "asset:600519",
        "metric_key": "close",
        "low": Decimal("1500"),
        "high": Decimal("1800"),
        "window_start": "2027-04-01",
        "window_end": "2027-10-23",
        "basis": "中长期估值回到历史中位",
    }
    fields.update(overrides)
    return TargetRange(**fields)


def _plan(**overrides) -> Plan:
    fields = {
        "plan_id": "plan:user:a",
        "user_id": "user:a",
        "as_of_date": AS_OF,
        "version": 1,
        "actions": (_action(),),
        "review_nodes": (ReviewNode("2027-04-30", "一季报复查"),),
        "targets": (_target(),),
    }
    fields.update(overrides)
    return Plan(**fields)


if __name__ == "__main__":
    unittest.main()
