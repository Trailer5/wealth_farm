"""Deterministic portfolio-aware position plan assembly."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.portfolio_risk import (
    InvestmentMandate,
    PortfolioConstraintSnapshot,
    PortfolioRiskService,
    PortfolioValuation,
    PositionBudgetCalculator,
    PositionConstraintCalculator,
    portfolio_valuation_digest,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
)
from src.user_space import UserContext

from .entry_models import (
    EntryPlan,
    EntryPlanStatus,
    TimingConditionState,
)
from .exploration_models import (
    ExplorationStatus,
    ExplorationTrack,
    QuestionStatus,
    ResearchExplorationSession,
)
from .position_models import (
    PositionAction,
    PositionAdjustmentDirection,
    PositionCalculationInput,
    PositionConstraintBlock,
    PositionPlan,
    PositionPlanStatus,
    PositionProfilePolicy,
    PositionTrancheAdjustment,
    position_plan_id,
)
from .suitability_models import (
    SuitabilityAction,
    SuitabilityAssessment,
)


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class _AssemblyState:
    status: PositionPlanStatus
    gaps: tuple[str, ...]


class PositionPlanService:
    """Assemble ranges from persisted user constraints and calculators."""

    def __init__(
        self,
        portfolio_risk: PortfolioRiskService,
        *,
        budget_calculator: PositionBudgetCalculator | None = None,
        constraint_calculator: (
            PositionConstraintCalculator | None
        ) = None,
        policies: tuple[PositionProfilePolicy, ...] | None = None,
    ) -> None:
        self.portfolio_risk = portfolio_risk
        self.budget_calculator = (
            budget_calculator or PositionBudgetCalculator()
        )
        self.constraint_calculator = (
            constraint_calculator or PositionConstraintCalculator()
        )
        values = policies or default_position_policies()
        self._policies = {
            item.profile_id: item for item in values
        }
        if len(self._policies) != len(values):
            raise ValueError(
                "position profile policies must be unique"
            )

    def resolve_policy(
        self,
        profile_id: str,
    ) -> PositionProfilePolicy | None:
        return self._policies.get(profile_id)

    def assemble(
        self,
        *,
        context: UserContext,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        decision_brief: ResearchDecisionBrief | None,
        calculation_inputs: PositionCalculationInput | None,
    ) -> PositionPlan:
        self._validate_identity(
            context=context,
            session=session,
            suitability=suitability,
            entry_plan=entry_plan,
            decision_brief=decision_brief,
        )
        policy = self.resolve_policy(session.task.profile_id)
        state = self._initial_state(
            session=session,
            suitability=suitability,
            decision_brief=decision_brief,
            calculation_inputs=calculation_inputs,
            policy=policy,
        )
        policy_id = (
            policy.policy_id
            if policy is not None
            else f"{session.task.profile_id}:position_unsupported"
        )
        if state is not None:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=state,
            )

        assert decision_brief is not None
        assert calculation_inputs is not None
        assert decision_brief.mandate_id is not None
        assert (
            decision_brief.portfolio_constraint_snapshot_id
            is not None
        )
        mandate = self.portfolio_risk.get_mandate(
            context,
            decision_brief.mandate_id,
        )
        if mandate is None:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    ("investment_mandate_not_recorded",),
                ),
            )
        constraints = self.portfolio_risk.get_constraint_snapshot(
            context,
            decision_brief.portfolio_constraint_snapshot_id,
        )
        if constraints is None:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    ("portfolio_constraint_snapshot_not_recorded",),
                ),
            )
        valuation = self.portfolio_risk.get_snapshot(
            context,
            constraints.portfolio_snapshot_id,
        )
        if valuation is None:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    ("portfolio_valuation_snapshot_not_recorded",),
                ),
            )
        self._validate_loaded_context(
            session=session,
            decision_brief=decision_brief,
            calculation_inputs=calculation_inputs,
            mandate=mandate,
            constraints=constraints,
            valuation=valuation,
        )
        assert policy is not None
        if mandate.implementation_asset_id != session.task.asset_id:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.UNSUPPORTED,
                    (
                        "mandate_implementation_asset_does_not_"
                        "match_subject",
                    ),
                ),
            )
        position = next(
            (
                item
                for item in valuation.positions
                if item.asset_id == session.task.asset_id
            ),
            None,
        )
        current_quantity = (
            Decimal(str(position.quantity))
            if position is not None
            else ZERO
        )
        if (
            position is not None
            and calculation_inputs.subject_price
            != Decimal(str(position.market_price))
        ):
            raise ValueError(
                "calculation subject price does not match "
                "portfolio valuation"
            )
        if (
            decision_brief.position_state is PositionState.HELD
            and current_quantity == ZERO
        ) or (
            decision_brief.position_state
            is PositionState.NOT_HELD
            and current_quantity > ZERO
        ):
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    (
                        "decision_brief_position_state_conflicts_"
                        "with_portfolio",
                    ),
                ),
            )
        try:
            budget = self.budget_calculator.calculate(
                mandate,
                calculation_inputs.to_budget_input(),
            )
        except ValueError as exc:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    (f"position_budget_input_invalid:{exc}",),
                ),
            )
        if "tracking_error_budget_exceeded" in budget.warnings:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    (
                        "constraint_conflict:"
                        "tracking_error_budget_exceeded",
                    ),
                ),
            )
        result = self.constraint_calculator.calculate(
            asset_id=session.task.asset_id,
            valuation=valuation,
            constraints=constraints,
            budget=budget,
        )
        if not result.feasible:
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    tuple(
                        f"constraint_conflict:{item}"
                        for item in result.conflicts
                    ),
                ),
            )
        if (
            entry_plan.status is EntryPlanStatus.STAGE
            and _active_tranche_count(entry_plan) > 0
            and len(constraints.tranche_fractions)
            != _active_tranche_count(entry_plan)
        ):
            return self._unavailable_plan(
                session=session,
                suitability=suitability,
                entry_plan=entry_plan,
                decision_brief=decision_brief,
                policy_id=policy_id,
                state=_AssemblyState(
                    PositionPlanStatus.USER_CONTEXT_REQUIRED,
                    (
                        "tranche_fractions_do_not_match_active_"
                        "entry_rules",
                    ),
                ),
            )

        target_min = result.target_weight_min
        target_max = result.target_weight_max
        target_notional_min = result.target_notional_min
        target_notional_max = result.target_notional_max
        binding = result.binding_constraints
        stress_loss = result.stress_loss_at_target_max
        if suitability.action is SuitabilityAction.AVOID:
            target_min = ZERO
            target_max = ZERO
            target_notional_min = ZERO
            target_notional_max = ZERO
            binding = ("suitability_avoid",)
            stress_loss = ZERO
        action = _action(
            position_state=decision_brief.position_state,
            current_weight=result.current_weight,
            target_min=target_min,
            target_max=target_max,
            entry_status=entry_plan.status,
            suitability_action=suitability.action,
        )
        tranche_adjustments = _tranche_adjustments(
            action=action,
            current_notional=(
                current_quantity
                * calculation_inputs.subject_price
            ),
            target_notional_min=target_notional_min,
            target_notional_max=target_notional_max,
            subject_price=calculation_inputs.subject_price,
            entry_plan=entry_plan,
            fractions=constraints.tranche_fractions,
        )
        constraint_blocks = tuple(
            PositionConstraintBlock(
                constraint=item.constraint,
                ceiling_weight=item.weight,
                binding=item.constraint in binding,
                current_breach=(
                    item.constraint
                    in result.current_constraint_breaches
                ),
            )
            for item in result.ceilings
        )
        if suitability.action is SuitabilityAction.AVOID:
            constraint_blocks = (
                *constraint_blocks,
                PositionConstraintBlock(
                    constraint="suitability_avoid",
                    ceiling_weight=ZERO,
                    binding=True,
                    current_breach=result.current_weight > ZERO,
                ),
            )
        evidence_refs = _merge_texts(
            suitability.evidence_refs,
            entry_plan.evidence_refs,
            constraints.evidence_refs,
            calculation_inputs.evidence_refs,
            (
                f"mandate:{mandate.mandate_id}",
                f"portfolio:{valuation.snapshot.snapshot_id}",
                f"constraints:{constraints.snapshot_id}",
            ),
        )
        limitations = _merge_texts(
            calculation_inputs.limitations,
            tuple(f"预算提示：{item}" for item in budget.warnings),
            (
                policy.liquidity_semantics,
                "目标区间是约束边界，不是收益预测。",
                "分批金额是规划边界，不是订单。",
            ),
        )
        values = {
            "session_id": session.session_id,
            "workflow_run_id": session.workflow_run_id,
            "suitability_assessment_id": suitability.assessment_id,
            "entry_plan_id": entry_plan.plan_id,
            "decision_brief_id": decision_brief.brief_id,
            "asset_id": session.task.asset_id,
            "profile_id": session.task.profile_id,
            "policy_id": policy_id,
            "as_of_date": session.task.as_of_date,
            "horizon": session.task.horizon,
            "status": PositionPlanStatus.AVAILABLE,
            "action": action,
            "portfolio_role": decision_brief.portfolio_role,
            "position_state": decision_brief.position_state,
            "mandate_id": mandate.mandate_id,
            "portfolio_snapshot_id": (
                valuation.snapshot.snapshot_id
            ),
            "constraint_snapshot_id": constraints.snapshot_id,
            "currency": constraints.currency,
            "current_quantity": current_quantity,
            "current_weight": result.current_weight,
            "target_weight_min": target_min,
            "target_weight_max": target_max,
            "target_notional_min": target_notional_min,
            "target_notional_max": target_notional_max,
            "risk_budget_weight": budget.risk_weight,
            "stress_loss": stress_loss,
            "binding_constraints": binding,
            "constraint_blocks": constraint_blocks,
            "tranche_adjustments": tranche_adjustments,
            "calculation_inputs": calculation_inputs,
            "evidence_refs": evidence_refs,
            "limitations": limitations,
            "assembly_gaps": (),
        }
        return _make_plan(values)

    def _initial_state(
        self,
        *,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        decision_brief: ResearchDecisionBrief | None,
        calculation_inputs: PositionCalculationInput | None,
        policy: PositionProfilePolicy | None,
    ) -> _AssemblyState | None:
        unsupported: list[str] = []
        user_context: list[str] = []
        if DecisionLane.POSITION_SIZING not in session.decision_lanes:
            unsupported.append("position_sizing_lane_not_requested")
        if policy is None or not policy.sizing_supported:
            unsupported.append(
                f"profile_position_sizing_unsupported:"
                f"{session.task.profile_id}"
            )
        if suitability.action is SuitabilityAction.INSUFFICIENT:
            unsupported.append(
                "suitability_assessment_insufficient"
            )
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            unsupported.append("exploration_not_ready_for_report")
        questions = tuple(
            item
            for item in session.questions
            if item.track is ExplorationTrack.POSITION_SIZING
            and item.blocking
        )
        if not questions or any(
            item.status is not QuestionStatus.ANSWERED
            for item in questions
        ):
            unsupported.append(
                "position_sizing_question_not_answered"
            )
        if decision_brief is None:
            user_context.append("decision_brief_unavailable")
        else:
            if (
                DecisionLane.POSITION_SIZING
                not in decision_brief.decision_lanes
            ):
                unsupported.append(
                    "decision_brief_position_lane_not_requested"
                )
            user_context.extend(
                f"user_context_missing:{item.value}"
                for item in decision_brief.missing_user_context
            )
            if decision_brief.mandate_id is None:
                user_context.append("mandate_id_unavailable")
            if (
                decision_brief.portfolio_constraint_snapshot_id
                is None
            ):
                user_context.append(
                    "portfolio_constraint_snapshot_id_unavailable"
                )
            if (
                decision_brief.position_state
                is PositionState.UNKNOWN
            ):
                user_context.append(
                    "user_context_missing:position_state"
                )
        if session.user_id is None:
            user_context.append("personalized_session_user_unavailable")
        if calculation_inputs is None:
            user_context.append(
                "position_calculation_inputs_unavailable"
            )
        if unsupported:
            return _AssemblyState(
                PositionPlanStatus.UNSUPPORTED,
                _merge_texts(tuple(unsupported)),
            )
        if user_context:
            return _AssemblyState(
                PositionPlanStatus.USER_CONTEXT_REQUIRED,
                _merge_texts(tuple(user_context)),
            )
        return None

    def _unavailable_plan(
        self,
        *,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        decision_brief: ResearchDecisionBrief | None,
        policy_id: str,
        state: _AssemblyState,
    ) -> PositionPlan:
        values = {
            "session_id": session.session_id,
            "workflow_run_id": session.workflow_run_id,
            "suitability_assessment_id": suitability.assessment_id,
            "entry_plan_id": entry_plan.plan_id,
            "decision_brief_id": (
                decision_brief.brief_id
                if decision_brief is not None
                else session.decision_brief_id
            ),
            "asset_id": session.task.asset_id,
            "profile_id": session.task.profile_id,
            "policy_id": policy_id,
            "as_of_date": session.task.as_of_date,
            "horizon": session.task.horizon,
            "status": state.status,
            "action": PositionAction.UNAVAILABLE,
            "portfolio_role": (
                decision_brief.portfolio_role
                if decision_brief is not None
                else None
            ),
            "position_state": (
                decision_brief.position_state
                if decision_brief is not None
                else None
            ),
            "mandate_id": (
                decision_brief.mandate_id
                if decision_brief is not None
                else None
            ),
            "portfolio_snapshot_id": None,
            "constraint_snapshot_id": (
                decision_brief.portfolio_constraint_snapshot_id
                if decision_brief is not None
                else None
            ),
            "currency": None,
            "current_quantity": None,
            "current_weight": None,
            "target_weight_min": None,
            "target_weight_max": None,
            "target_notional_min": None,
            "target_notional_max": None,
            "risk_budget_weight": None,
            "stress_loss": None,
            "binding_constraints": (),
            "constraint_blocks": (),
            "tranche_adjustments": (),
            "calculation_inputs": None,
            "evidence_refs": (),
            "limitations": ("所有已列缺口解决前不给出仓位区间。",),
            "assembly_gaps": state.gaps,
        }
        return _make_plan(values)

    @staticmethod
    def _validate_identity(
        *,
        context: UserContext,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        decision_brief: ResearchDecisionBrief | None,
    ) -> None:
        if suitability.session_id != session.session_id:
            raise ValueError(
                "suitability session does not match exploration"
            )
        if entry_plan.session_id != session.session_id:
            raise ValueError(
                "entry plan session does not match exploration"
            )
        if (
            suitability.asset_id != session.task.asset_id
            or entry_plan.asset_id != session.task.asset_id
        ):
            raise ValueError(
                "upstream asset does not match exploration"
            )
        if (
            suitability.profile_id != session.task.profile_id
            or entry_plan.profile_id != session.task.profile_id
        ):
            raise ValueError(
                "upstream profile does not match exploration"
            )
        if (
            suitability.as_of_date != session.task.as_of_date
            or entry_plan.as_of_date != session.task.as_of_date
        ):
            raise ValueError(
                "upstream cutoff does not match exploration"
            )
        if suitability.workflow_run_id != session.workflow_run_id:
            raise ValueError(
                "suitability workflow does not match exploration"
            )
        if (
            entry_plan.suitability_assessment_id
            != suitability.assessment_id
        ):
            raise ValueError(
                "entry plan does not reference suitability"
            )
        if suitability.time_horizon is not session.task.horizon:
            raise ValueError(
                "suitability horizon does not match exploration"
            )
        if (
            entry_plan.suitability_horizon
            is not suitability.time_horizon
        ):
            raise ValueError(
                "entry suitability horizon does not match "
                "assessment"
            )
        if entry_plan.execution_horizon is not session.task.horizon:
            raise ValueError(
                "entry horizon does not match exploration"
            )
        if (
            session.user_id is not None
            and session.user_id != context.user_id
        ):
            raise ValueError(
                "user context does not match exploration"
            )
        if decision_brief is None:
            return
        if decision_brief.user_id != context.user_id:
            raise ValueError(
                "decision brief user does not match context"
            )
        if decision_brief.asset_id != session.task.asset_id:
            raise ValueError(
                "decision brief asset does not match exploration"
            )
        if (
            session.decision_brief_id is not None
            and decision_brief.brief_id
            != session.decision_brief_id
        ):
            raise ValueError(
                "decision brief id does not match exploration"
            )
        if (
            suitability.decision_brief_id is not None
            and suitability.decision_brief_id
            != decision_brief.brief_id
        ):
            raise ValueError(
                "decision brief id does not match suitability"
            )
        if (
            entry_plan.decision_brief_id is not None
            and entry_plan.decision_brief_id
            != decision_brief.brief_id
        ):
            raise ValueError(
                "decision brief id does not match entry plan"
            )
        if decision_brief.horizon is not session.task.horizon:
            raise ValueError(
                "decision brief horizon does not match exploration"
            )
        if decision_brief.as_of_date > session.task.as_of_date:
            raise ValueError(
                "decision brief exceeds exploration cutoff"
            )

    @staticmethod
    def _validate_loaded_context(
        *,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief,
        calculation_inputs: PositionCalculationInput,
        mandate: InvestmentMandate,
        constraints: PortfolioConstraintSnapshot,
        valuation: PortfolioValuation,
    ) -> None:
        if constraints.user_id != decision_brief.user_id:
            raise ValueError(
                "constraint snapshot user mismatch"
            )
        if constraints.mandate_id != mandate.mandate_id:
            raise ValueError(
                "constraint snapshot mandate mismatch"
            )
        if constraints.subject_asset_id != session.task.asset_id:
            raise ValueError(
                "constraint snapshot asset mismatch"
            )
        if constraints.as_of_date != session.task.as_of_date:
            raise ValueError(
                "constraint snapshot cutoff mismatch"
            )
        if valuation.snapshot.as_of_date != session.task.as_of_date:
            raise ValueError(
                "portfolio valuation cutoff mismatch"
            )
        if valuation.snapshot.base_currency != constraints.currency:
            raise ValueError(
                "portfolio valuation currency mismatch"
            )
        if (
            valuation.snapshot.holdings_as_of_date
            != session.task.as_of_date
            or not valuation.snapshot.evidence_refs
        ):
            raise ValueError(
                "portfolio valuation lacks cutoff provenance"
            )
        if (
            portfolio_valuation_digest(valuation)
            != constraints.portfolio_valuation_digest
        ):
            raise ValueError(
                "portfolio valuation digest does not match "
                "constraint snapshot"
            )
        if calculation_inputs.as_of_date != session.task.as_of_date:
            raise ValueError(
                "calculation input cutoff mismatch"
            )
        if calculation_inputs.asset_id != session.task.asset_id:
            raise ValueError(
                "calculation input asset mismatch"
            )


def default_position_policies() -> tuple[PositionProfilePolicy, ...]:
    return (
        PositionProfilePolicy(
            profile_id=PROFILE_ORDINARY_COMPANY,
            policy_id=(
                f"{PROFILE_ORDINARY_COMPANY}:position_v1"
            ),
            sizing_supported=True,
            liquidity_semantics=(
                "流动性上限按用户确认的日均成交额、参与率和执行周期计算。"
            ),
        ),
        PositionProfilePolicy(
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            policy_id=(
                f"{PROFILE_FINANCIAL_INSTITUTION}:position_v1"
            ),
            sizing_supported=True,
            liquidity_semantics=(
                "金融机构仓位使用同样的可观察市场流动性上限。"
            ),
        ),
        PositionProfilePolicy(
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            policy_id=(
                f"{PROFILE_CYCLICAL_RESOURCE}:position_v1"
            ),
            sizing_supported=True,
            liquidity_semantics=(
                "周期资源公司仓位需要固定的压力冲击和可观察的流动性输入。"
            ),
        ),
        PositionProfilePolicy(
            profile_id=PROFILE_HIGH_GROWTH_UNPROFITABLE,
            policy_id=(
                f"{PROFILE_HIGH_GROWTH_UNPROFITABLE}:position_v1"
            ),
            sizing_supported=True,
            liquidity_semantics=(
                "高成长未盈利公司波动和融资稀释风险较高，仓位上限应由用户确认的压力冲击和流动性输入共同约束。"
            ),
        ),
        PositionProfilePolicy(
            profile_id=PROFILE_EXCHANGE_FUND,
            policy_id=f"{PROFILE_EXCHANGE_FUND}:position_v1",
            sizing_supported=True,
            liquidity_semantics=(
                "ETF 流动性上限按成交额计算，不用基金规模代替可执行成交量。"
            ),
        ),
        PositionProfilePolicy(
            profile_id=PROFILE_OPEN_FUND,
            policy_id=f"{PROFILE_OPEN_FUND}:position_v1",
            sizing_supported=True,
            liquidity_semantics=(
                "场外基金按净值申赎，没有盘口冲击；流动性输入填日累计申购限额（无限额时填基金规模的 10%，即巨额赎回阈值）、参与率 1，执行周期按分批天数；风险用基金自身净值的波动与回撤。"
            ),
        ),
    )


def _action(
    *,
    position_state: PositionState,
    current_weight: Decimal,
    target_min: Decimal,
    target_max: Decimal,
    entry_status: EntryPlanStatus,
    suitability_action: SuitabilityAction,
) -> PositionAction:
    if suitability_action is SuitabilityAction.AVOID:
        return (
            PositionAction.REDUCE
            if current_weight > ZERO
            else PositionAction.NO_ACTION
        )
    if current_weight > target_max:
        return PositionAction.REDUCE
    if entry_status in (
        EntryPlanStatus.WAIT,
        EntryPlanStatus.INSUFFICIENT,
        EntryPlanStatus.NOT_APPLICABLE,
    ):
        return PositionAction.WAIT
    if current_weight < target_min:
        return (
            PositionAction.NEW_ALLOCATION
            if position_state is PositionState.NOT_HELD
            else PositionAction.INCREASE
        )
    if (
        position_state is PositionState.NOT_HELD
        and current_weight < target_max
    ):
        return PositionAction.NEW_ALLOCATION
    return PositionAction.MAINTAIN


def _tranche_adjustments(
    *,
    action: PositionAction,
    current_notional: Decimal,
    target_notional_min: Decimal,
    target_notional_max: Decimal,
    subject_price: Decimal,
    entry_plan: EntryPlan,
    fractions: tuple[Decimal, ...],
) -> tuple[PositionTrancheAdjustment, ...]:
    if action not in (
        PositionAction.NEW_ALLOCATION,
        PositionAction.INCREASE,
        PositionAction.REDUCE,
    ):
        return ()
    if action is PositionAction.REDUCE:
        amount_min = max(
            ZERO,
            current_notional - target_notional_max,
        )
        amount_max = max(
            ZERO,
            current_notional - target_notional_min,
        )
        direction = PositionAdjustmentDirection.REDUCE
        rules: tuple[object, ...] = ()
        applied_fractions = (ONE,)
    else:
        amount_min = max(
            ZERO,
            target_notional_min - current_notional,
        )
        amount_max = max(
            ZERO,
            target_notional_max - current_notional,
        )
        direction = PositionAdjustmentDirection.INCREASE
        rules = tuple(
            item
            for item in entry_plan.tranche_rules
            if item.state is TimingConditionState.MET
        )
        applied_fractions = (
            fractions
            if entry_plan.status is EntryPlanStatus.STAGE
            else (ONE,)
        )
    if amount_max == ZERO:
        return ()
    return tuple(
        PositionTrancheAdjustment(
            tranche_id=f"{entry_plan.plan_id}:tranche:{index}",
            source_condition_id=(
                rules[index - 1].condition_id
                if rules
                else None
            ),
            direction=direction,
            target_fraction=fraction,
            notional_min=amount_min * fraction,
            notional_max=amount_max * fraction,
            quantity_min=(
                amount_min * fraction / subject_price
            ),
            quantity_max=(
                amount_max * fraction / subject_price
            ),
        )
        for index, fraction in enumerate(
            applied_fractions,
            start=1,
        )
    )


def _active_tranche_count(entry_plan: EntryPlan) -> int:
    return sum(
        item.state is TimingConditionState.MET
        for item in entry_plan.tranche_rules
    )


def _make_plan(values: dict[str, object]) -> PositionPlan:
    payload = _position_payload(values)
    return PositionPlan(
        plan_id=position_plan_id(payload),
        **values,
    )


def _position_payload(
    values: dict[str, object],
) -> dict[str, object]:
    return {
        "session_id": values["session_id"],
        "workflow_run_id": values["workflow_run_id"],
        "suitability_assessment_id": values[
            "suitability_assessment_id"
        ],
        "entry_plan_id": values["entry_plan_id"],
        "decision_brief_id": values["decision_brief_id"],
        "asset_id": values["asset_id"],
        "profile_id": values["profile_id"],
        "policy_id": values["policy_id"],
        "as_of_date": values["as_of_date"],
        "horizon": values["horizon"].value,
        "status": values["status"].value,
        "action": values["action"].value,
        "portfolio_role": values["portfolio_role"],
        "position_state": (
            values["position_state"].value
            if values["position_state"] is not None
            else None
        ),
        "mandate_id": values["mandate_id"],
        "portfolio_snapshot_id": values[
            "portfolio_snapshot_id"
        ],
        "constraint_snapshot_id": values[
            "constraint_snapshot_id"
        ],
        "currency": values["currency"],
        "current_quantity": _decimal_text(
            values["current_quantity"]
        ),
        "current_weight": _decimal_text(
            values["current_weight"]
        ),
        "target_weight_min": _decimal_text(
            values["target_weight_min"]
        ),
        "target_weight_max": _decimal_text(
            values["target_weight_max"]
        ),
        "target_notional_min": _decimal_text(
            values["target_notional_min"]
        ),
        "target_notional_max": _decimal_text(
            values["target_notional_max"]
        ),
        "risk_budget_weight": _decimal_text(
            values["risk_budget_weight"]
        ),
        "stress_loss": _decimal_text(values["stress_loss"]),
        "binding_constraints": list(
            values["binding_constraints"]
        ),
        "constraint_blocks": [
            item.to_dict() for item in values["constraint_blocks"]
        ],
        "tranche_adjustments": [
            item.to_dict()
            for item in values["tranche_adjustments"]
        ],
        "calculation_inputs": (
            values["calculation_inputs"].to_dict()
            if values["calculation_inputs"] is not None
            else None
        ),
        "evidence_refs": list(values["evidence_refs"]),
        "limitations": list(values["limitations"]),
        "assembly_gaps": list(values["assembly_gaps"]),
    }


def _decimal_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _merge_texts(
    *groups: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in groups
            for item in group
            if item.strip()
        )
    )
