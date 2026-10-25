import unittest

from src.research_workflow import (
    DecisionContextRequirement,
    DecisionContextStatus,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)


class ResearchDecisionBriefTest(unittest.TestCase):
    def test_brief_round_trips_with_position_context_gap(self):
        brief = self._brief()

        restored = ResearchDecisionBrief.from_dict(
            brief.to_dict()
        )

        self.assertEqual(restored, brief)
        self.assertEqual(
            restored.position_context_status,
            DecisionContextStatus.USER_CONTEXT_REQUIRED,
        )

    def test_complete_references_make_position_context_ready(self):
        brief = self._brief(
            mandate_id="mandate:core",
            portfolio_constraint_snapshot_id="snapshot:2026-08-23",
            missing_user_context=(),
        )

        self.assertEqual(
            brief.position_context_status,
            DecisionContextStatus.READY,
        )
        payload = brief.to_dict()
        for duplicated_numeric_field in (
            "planned_capital",
            "maximum_acceptable_loss",
            "target_volatility",
            "max_drawdown",
            "max_risk_weight",
        ):
            self.assertNotIn(duplicated_numeric_field, payload)

    def test_baseline_task_does_not_contain_private_user_context(self):
        task = ResearchTaskSpec(
            task_id="research:test:decision-boundary",
            asset_id="asset:test",
            profile_id="ordinary_company",
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-23",
        )

        payload = task.to_dict()

        for private_field in (
            "user_id",
            "decision_intent",
            "position_state",
            "portfolio_role",
            "mandate_id",
            "missing_user_context",
        ):
            self.assertNotIn(private_field, payload)

    def _brief(self, **overrides) -> ResearchDecisionBrief:
        values = {
            "brief_id": "decision-brief:test",
            "user_id": "user:test",
            "asset_id": "asset:test",
            "decision_intent": DecisionIntent.EVALUATE_PURCHASE,
            "decision_lanes": (
                DecisionLane.SUITABILITY,
                DecisionLane.POSITION_SIZING,
            ),
            "position_state": PositionState.NOT_HELD,
            "horizon": ResearchHorizon.MEDIUM_LONG,
            "as_of_date": "2026-08-23",
            "portfolio_role": "核心配置",
            "mandate_id": None,
            "portfolio_constraint_snapshot_id": None,
            "constraints": ("不使用杠杆",),
            "assumptions": (),
            "missing_user_context": (
                DecisionContextRequirement.INVESTMENT_MANDATE,
                DecisionContextRequirement.PORTFOLIO_CONSTRAINT_SNAPSHOT,
            ),
            "created_at": "2026-08-23T13:00:00+00:00",
            "updated_at": "2026-08-23T13:00:00+00:00",
        }
        values.update(overrides)
        return ResearchDecisionBrief(**values)


if __name__ == "__main__":
    unittest.main()
