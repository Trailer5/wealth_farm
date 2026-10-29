import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
)
from src.portfolio_risk import (
    InvestmentMandate,
    PortfolioConstraintSnapshot,
    portfolio_valuation_digest,
)
from src.research_orchestration import (
    EntryPlanService,
    ExplorationQuestion,
    ExplorationTrack,
    PositionAction,
    PositionCalculationInput,
    PositionPlan,
    PositionPlanStatus,
    QuestionStatus,
    TimingConditionState,
    create_default_container,
)
from src.research_orchestration.position_models import (
    position_plan_id,
)
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionLane,
    PositionState,
)

from src.tests.research_orchestration.unit_tests import (
    test_entry_plan as entry_fixtures,
)


class PositionPlanTest(unittest.TestCase):
    def test_stock_range_uses_multiple_formal_constraint_minimums(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.status, PositionPlanStatus.AVAILABLE)
            self.assertIs(
                plan.action,
                PositionAction.NEW_ALLOCATION,
            )
            self.assertEqual(
                plan.target_weight_min,
                Decimal("0.10"),
            )
            self.assertEqual(
                plan.target_weight_max,
                Decimal("0.15"),
            )
            self.assertEqual(
                set(plan.binding_constraints),
                {"stress_loss", "planned_deployable_cash"},
            )
            self.assertEqual(plan.stress_loss, Decimal("60.000"))
            self.assertEqual(len(plan.tranche_adjustments), 1)
            self.assertEqual(
                plan.tranche_adjustments[0].notional_max,
                Decimal("300.00"),
            )
            self.assertEqual(
                PositionPlan.from_dict(plan.to_dict()),
                plan,
            )
            self.assertNotIn("order", plan.to_dict())

    def test_single_concentration_constraint_binds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
                constraint_overrides={
                    "max_single_asset_weight": Decimal("0.12"),
                    "max_stress_loss": Decimal("0.20"),
                    "planned_deployable_cash": Decimal("1000"),
                },
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertEqual(
                plan.target_weight_max,
                Decimal("0.12"),
            )
            self.assertEqual(
                plan.binding_constraints,
                ("single_asset_concentration",),
            )

    def test_stress_overrun_for_held_stock_requires_reduction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=True,
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.action, PositionAction.REDUCE)
            stress = next(
                item
                for item in plan.constraint_blocks
                if item.constraint == "stress_loss"
            )
            self.assertTrue(stress.current_breach)
            self.assertEqual(
                plan.tranche_adjustments[0].notional_min,
                Decimal("700.00"),
            )
            self.assertEqual(
                plan.tranche_adjustments[0].notional_max,
                Decimal("800.00"),
            )

    def test_etf_staging_uses_user_confirmed_tranche_fraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_EXCHANGE_FUND,
                held=False,
                tranche_state=TimingConditionState.MET,
                constraint_overrides={
                    "tranche_fractions": (Decimal("1"),),
                },
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.status, PositionPlanStatus.AVAILABLE)
            self.assertIs(
                plan.action,
                PositionAction.NEW_ALLOCATION,
            )
            self.assertEqual(len(plan.tranche_adjustments), 1)
            self.assertIsNotNone(
                plan.tranche_adjustments[0].source_condition_id
            )
            self.assertIn(
                "ETF 流动性上限",
                " ".join(plan.limitations),
            )

    def test_missing_tranche_fractions_requires_user_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_EXCHANGE_FUND,
                held=False,
                tranche_state=TimingConditionState.MET,
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(
                plan.status,
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
            )
            self.assertIn(
                "tranche_fractions_do_not_match_active_entry_rules",
                plan.assembly_gaps,
            )
            self.assertIsNone(plan.target_weight_max)

    def test_conflicting_minimum_and_ceiling_requires_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
                constraint_overrides={
                    "min_target_weight": Decimal("0.20"),
                    "max_single_asset_weight": Decimal("0.20"),
                    "max_stress_loss": Decimal("0.02"),
                },
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(
                plan.status,
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
            )
            self.assertTrue(
                any(
                    item.startswith("constraint_conflict:")
                    for item in plan.assembly_gaps
                )
            )

    def test_held_position_inside_range_is_maintained(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=True,
                constraint_overrides={
                    "min_target_weight": Decimal("0.40"),
                    "max_single_asset_weight": Decimal("0.80"),
                    "min_cash_weight": Decimal("0"),
                    "max_stress_loss": Decimal("0.20"),
                    "planned_deployable_cash": Decimal("1000"),
                },
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.action, PositionAction.MAINTAIN)
            self.assertEqual(plan.current_weight, Decimal("0.5"))
            self.assertFalse(plan.tranche_adjustments)

    def test_negative_correlation_does_not_trigger_positive_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
                constraint_overrides={
                    "observed_correlation_to_portfolio": Decimal(
                        "-0.90"
                    ),
                    "max_high_correlation_weight": Decimal("0.05"),
                },
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertEqual(
                plan.target_weight_max,
                Decimal("0.15"),
            )
            correlation = next(
                item
                for item in plan.constraint_blocks
                if item.constraint == "portfolio_correlation"
            )
            self.assertEqual(correlation.ceiling_weight, Decimal("1"))

    def test_unknown_position_state_requires_user_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
            )
            fixture.brief = replace(
                fixture.brief,
                position_state=PositionState.UNKNOWN,
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(
                plan.status,
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
            )
            self.assertIn(
                "user_context_missing:position_state",
                plan.assembly_gaps,
            )

    def test_calculation_input_asset_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
            )
            arguments = fixture.assemble_kwargs()
            arguments["calculation_inputs"] = _calculation_inputs(
                "asset:wrong"
            )

            with self.assertRaisesRegex(
                ValueError,
                "calculation input asset mismatch",
            ):
                fixture.container.position.assemble(**arguments)

    def test_fractional_holding_uses_decimal_notional_consistently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=True,
                holding_quantity=Decimal("0.1"),
                subject_price=Decimal("0.2"),
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.status, PositionPlanStatus.AVAILABLE)
            self.assertIs(plan.action, PositionAction.INCREASE)
            self.assertTrue(plan.tranche_adjustments)

    def test_tracking_error_conflict_is_not_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
                mandate_overrides={
                    "objective": "relative_return",
                    "max_tracking_error": Decimal("0.04"),
                },
            )
            arguments = fixture.assemble_kwargs()
            arguments["calculation_inputs"] = replace(
                arguments["calculation_inputs"],
                benchmark_volatility=Decimal("0.20"),
            )

            plan = fixture.container.position.assemble(**arguments)

            self.assertIs(
                plan.status,
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
            )
            self.assertIn(
                "constraint_conflict:"
                "tracking_error_budget_exceeded",
                plan.assembly_gaps,
            )

    def test_open_fund_position_sizing_uses_dealing_liquidity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_OPEN_FUND,
                held=False,
            )

            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )

            self.assertIs(plan.status, PositionPlanStatus.AVAILABLE)
            self.assertIs(
                plan.action,
                PositionAction.NEW_ALLOCATION,
            )
            self.assertIn(
                "按净值申赎",
                " ".join(plan.limitations),
            )

    def test_missing_recorded_mandate_requires_user_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            container = create_default_container(
                db_path=Path(temp_dir) / "missing.sqlite3",
                report_root=Path(temp_dir) / "report",
            )
            session, brief, suitability, entry_plan = _upstream(
                PROFILE_ORDINARY_COMPANY,
            )
            brief = replace(
                brief,
                mandate_id="mandate:missing",
                portfolio_constraint_snapshot_id=(
                    "constraints:missing"
                ),
            )

            plan = container.position.assemble(
                context=container.user_space.default_context(),
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=brief,
                calculation_inputs=_calculation_inputs(
                    session.task.asset_id
                ),
            )

            self.assertIs(
                plan.status,
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
            )
            self.assertIn(
                "investment_mandate_not_recorded",
                plan.assembly_gaps,
            )

    def test_deserialization_revalidates_id_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _fixture(
                Path(temp_dir),
                PROFILE_ORDINARY_COMPANY,
                held=False,
            )
            plan = fixture.container.position.assemble(
                **fixture.assemble_kwargs()
            )
            payload = plan.to_dict()
            payload["target_weight_max"] = "0.99"
            with self.assertRaisesRegex(
                ValueError,
                "plan_id does not match",
            ):
                PositionPlan.from_dict(payload)

            payload = plan.to_dict()
            payload["unknown"] = True
            with self.assertRaisesRegex(ValueError, "Unknown"):
                PositionPlan.from_dict(payload)

            payload = plan.to_dict()
            payload["tranche_adjustments"][0][
                "target_fraction"
            ] = "0.5"
            payload["plan_id"] = position_plan_id(payload)
            with self.assertRaisesRegex(
                ValueError,
                "fractions must sum",
            ):
                PositionPlan.from_dict(payload)

            payload = plan.to_dict()
            payload["calculation_inputs"]["asset_id"] = "asset:wrong"
            payload["plan_id"] = position_plan_id(payload)
            with self.assertRaisesRegex(
                ValueError,
                "identity must match",
            ):
                PositionPlan.from_dict(payload)

            payload = plan.to_dict()
            payload["tranche_adjustments"] = []
            payload["plan_id"] = position_plan_id(payload)
            with self.assertRaisesRegex(
                ValueError,
                "requires tranche",
            ):
                PositionPlan.from_dict(payload)

            payload = plan.to_dict()
            payload["tranche_adjustments"][0][
                "quantity_max"
            ] = "999"
            payload["plan_id"] = position_plan_id(payload)
            with self.assertRaisesRegex(
                ValueError,
                "fraction and price",
            ):
                PositionPlan.from_dict(payload)


class _Fixture:
    def __init__(
        self,
        *,
        container,
        context,
        session,
        suitability,
        entry_plan,
        brief,
        subject_price,
    ):
        self.container = container
        self.context = context
        self.session = session
        self.suitability = suitability
        self.entry_plan = entry_plan
        self.brief = brief
        self.subject_price = subject_price

    def assemble_kwargs(self):
        return {
            "context": self.context,
            "session": self.session,
            "suitability": self.suitability,
            "entry_plan": self.entry_plan,
            "decision_brief": self.brief,
            "calculation_inputs": _calculation_inputs(
                self.session.task.asset_id,
                self.subject_price,
            ),
        }


def _fixture(
    root,
    profile_id,
    *,
    held,
    tranche_state=TimingConditionState.NOT_APPLICABLE,
    constraint_overrides=None,
    mandate_overrides=None,
    holding_quantity=100,
    subject_price=Decimal("10"),
):
    container = create_default_container(
        db_path=root / "position.sqlite3",
        report_root=root / "report",
    )
    context = container.user_space.default_context()
    session, brief, suitability, _ = _upstream(
        profile_id,
        tranche_state=tranche_state,
    )
    asset_type = {
        PROFILE_EXCHANGE_FUND: ASSET_TYPE_EXCHANGE_FUND,
        PROFILE_OPEN_FUND: ASSET_TYPE_OPEN_FUND,
    }.get(profile_id, ASSET_TYPE_STOCK)
    _register_asset(
        container,
        session.task.asset_id,
        asset_type,
    )
    auxiliary_id = f"asset:auxiliary:{profile_id}"
    _register_asset(
        container,
        auxiliary_id,
        ASSET_TYPE_EXCHANGE_FUND,
    )
    held_asset_id = session.task.asset_id if held else auxiliary_id
    container.portfolio_risk.research_state.upsert_holding(
        context,
        asset_id=held_asset_id,
        quantity=holding_quantity,
        average_cost=Decimal("9"),
    )
    valuation = container.portfolio_risk.value_current_holdings(
        context,
        as_of_date=session.task.as_of_date,
        prices={held_asset_id: float(subject_price)},
        holdings_as_of_date=session.task.as_of_date,
        evidence_refs=(
            "user-confirmation:holdings",
            "evidence:portfolio-prices",
        ),
    )
    mandate_values = {
        "mandate_id": f"mandate:{profile_id}",
        "objective": "absolute_return",
        "implementation_asset_id": session.task.asset_id,
        "benchmark_asset_id": f"benchmark:{profile_id}",
        "defensive_asset_id": None,
        "target_volatility": Decimal("0.10"),
        "max_drawdown": Decimal("0.20"),
        "max_tracking_error": None,
        "max_risk_weight": Decimal("0.80"),
        "min_cash_weight": Decimal("0.10"),
    }
    mandate_values.update(mandate_overrides or {})
    mandate = InvestmentMandate(**mandate_values)
    container.portfolio_risk.save_mandate(context, mandate)
    values = {
        "snapshot_id": f"constraints:{profile_id}",
        "user_id": context.user_id,
        "mandate_id": mandate.mandate_id,
        "portfolio_snapshot_id": valuation.snapshot.snapshot_id,
        "portfolio_valuation_digest": (
            portfolio_valuation_digest(valuation)
        ),
        "subject_asset_id": session.task.asset_id,
        "as_of_date": session.task.as_of_date,
        "currency": "CNY",
        "current_cash": Decimal("1000"),
        "planned_deployable_cash": Decimal("300"),
        "min_target_weight": Decimal("0.10"),
        "max_single_asset_weight": Decimal("0.40"),
        "min_cash_weight": Decimal("0.10"),
        "max_stress_loss": Decimal("0.03"),
        "stress_scenario_return": Decimal("-0.20"),
        "max_correlation_to_portfolio": Decimal("0.80"),
        "observed_correlation_to_portfolio": Decimal("0.20"),
        "max_high_correlation_weight": Decimal("0.25"),
        "average_daily_traded_notional": Decimal("10000"),
        "max_liquidity_participation": Decimal("0.10"),
        "liquidity_horizon_days": 1,
        "tranche_fractions": (),
        "evidence_refs": (
            "user-confirmation:risk-constraints",
            "evidence:liquidity-and-correlation",
        ),
        "created_at": "2026-08-23T02:00:00+00:00",
    }
    values.update(constraint_overrides or {})
    constraints = PortfolioConstraintSnapshot(**values)
    container.portfolio_risk.save_constraint_snapshot(
        context,
        constraints,
    )
    brief = replace(
        brief,
        position_state=(
            PositionState.HELD
            if held
            else PositionState.NOT_HELD
        ),
        mandate_id=mandate.mandate_id,
        portfolio_constraint_snapshot_id=constraints.snapshot_id,
    )
    entry_plan = EntryPlanService().assemble(
        session=session,
        suitability=suitability,
        decision_brief=brief,
    )
    return _Fixture(
        container=container,
        context=context,
        session=session,
        suitability=suitability,
        entry_plan=entry_plan,
        brief=brief,
        subject_price=subject_price,
    )


def _upstream(
    profile_id,
    *,
    tranche_state=TimingConditionState.NOT_APPLICABLE,
):
    session, brief, suitability = entry_fixtures._entry_fixture(
        entry_fixtures.SuitabilityAssessmentService(),
        profile_id,
        tranche_state=tranche_state,
    )
    session = replace(
        session,
        questions=(
            *session.questions,
            ExplorationQuestion(
                question_id="decision-question:position-sizing",
                text="Which portfolio constraints bind sizing?",
                reason="Position decision gate.",
                blocking=True,
                status=QuestionStatus.ANSWERED,
                answer=(
                    "The mandate and constraint snapshot were "
                    "confirmed."
                ),
                evidence_refs=(
                    "user-confirmation:risk-constraints",
                ),
                track=ExplorationTrack.POSITION_SIZING,
            ),
        ),
        decision_lanes=(
            DecisionLane.SUITABILITY,
            DecisionLane.TIMING,
            DecisionLane.POSITION_SIZING,
        ),
    )
    brief = replace(
        brief,
        decision_lanes=(
            DecisionLane.SUITABILITY,
            DecisionLane.TIMING,
            DecisionLane.POSITION_SIZING,
        ),
    )
    entry_plan = EntryPlanService().assemble(
        session=session,
        suitability=suitability,
        decision_brief=brief,
    )
    return session, brief, suitability, entry_plan


def _register_asset(container, asset_id, asset_type):
    container.master_data.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=asset_id.replace(":", "_"),
            display_name=asset_id,
            market="cn",
            exchange="sh",
        ),
        source="unit-test",
        raw_symbol=asset_id,
    )


def _calculation_inputs(asset_id, subject_price=Decimal("10")):
    return PositionCalculationInput(
        asset_id=asset_id,
        as_of_date="2026-08-23",
        subject_price=subject_price,
        realized_volatility=Decimal("0.20"),
        current_drawdown=Decimal("-0.05"),
        timing_score_percentile=None,
        benchmark_volatility=None,
        evidence_refs=(
            "evidence:latest-price",
            "evidence:risk-metrics",
        ),
        limitations=(
            "Risk metrics use the recorded trailing window.",
        ),
    )


if __name__ == "__main__":
    unittest.main()
