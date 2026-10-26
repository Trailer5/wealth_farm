"""Project published final decisions onto ledger views and actions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from src.research_contracts import (
    ActionTrigger,
    ActionType,
    ClaimComparison,
    Confidence,
    DecisionLane,
    PositionState,
    QuantityRule,
    QuantityRuleKind,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    TriggerKind,
    View,
    ViewClaim,
    ViewOrigin,
    last_default_checkpoint,
)
from src.research_ledger import LedgerScope, ResearchLedgerService

from .entry_models import EntryConditionBlock, EntryPlan
from .final_decision_models import (
    FinalDecisionOutcome,
    FinalDecisionResearchReport,
)
from .position_models import PositionAction, PositionPlan
from .suitability_models import SuitabilityAssessment, SuitabilityConfidence


_SUITABILITY_CLAIMS = {
    FinalDecisionOutcome.SUITABILITY_CONSIDER: ClaimComparison.OUTPERFORM,
    FinalDecisionOutcome.SUITABILITY_AVOID: ClaimComparison.UNDERPERFORM,
}
_CONFIDENCE = {
    SuitabilityConfidence.SUPPORTED: Confidence.HIGH,
    SuitabilityConfidence.BOUNDED: Confidence.MEDIUM,
    SuitabilityConfidence.INSUFFICIENT: Confidence.LOW,
}
_POSITION_ACTION_TYPES = {
    PositionAction.NEW_ALLOCATION: ActionType.BUY,
    PositionAction.INCREASE: ActionType.ADD,
    PositionAction.REDUCE: ActionType.REDUCE,
    PositionAction.MAINTAIN: ActionType.HOLD,
}


@dataclass(frozen=True)
class PublishedJudgements:
    """The settleable content of one published final report."""

    views: tuple[View, ...]
    actions: tuple[SettleableAction, ...]

    @property
    def artifacts(self) -> tuple[View | SettleableAction, ...]:
        return (*self.views, *self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "views": [item.to_dict() for item in self.views],
            "actions": [item.to_dict() for item in self.actions],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishedJudgements":
        if set(payload) != {"views", "actions"}:
            raise ValueError("published judgements need views and actions")
        return cls(
            views=tuple(View.from_dict(item) for item in payload["views"]),
            actions=tuple(
                SettleableAction.from_dict(item)
                for item in payload["actions"]
            ),
        )


def final_report_source_ref(report_id: str) -> str:
    return f"final-report:{report_id}"


def project_final_decision(
    *,
    report: FinalDecisionResearchReport,
    suitability: SuitabilityAssessment,
    entry_plan: EntryPlan,
    position_plan: PositionPlan,
    benchmark_asset_id: str | None,
) -> PublishedJudgements:
    """Pure mapping of 智囊骨架 decision 6 from a published decision."""

    sections = {item.lane: item for item in report.decision_sections}
    suitability_section = sections[DecisionLane.SUITABILITY]
    timing_section = sections[DecisionLane.TIMING]
    position_section = sections[DecisionLane.POSITION_SIZING]
    rule = SettlementRule(
        SettlementMethod.PRICE_RELATIVE,
        report.asset_id,
        benchmark_id=benchmark_asset_id,
    )
    conditions = report.monitoring_plan.conditions
    falsifiers = _unique(item.text for item in conditions)
    comparison = _SUITABILITY_CLAIMS.get(suitability_section.outcome)
    claim = (
        ViewClaim("total_return", comparison)
        if comparison is not None
        and benchmark_asset_id is not None
        and falsifiers
        else None
    )
    view = View(
        view_id=f"view:{report.report_id}:suitability",
        subject_type=SubjectType.ASSET,
        subject_id=report.asset_id,
        origin=ViewOrigin.SYSTEM,
        proposition=f"{report.asset_name}：{suitability_section.summary}",
        horizon=report.horizon,
        as_of_date=report.as_of_date,
        expires_on=last_default_checkpoint(
            report.horizon,
            report.as_of_date,
        ),
        confidence=_CONFIDENCE[suitability.confidence],
        claim=claim,
        support_refs=_unique(report.thesis.evidence_refs),
        counter_refs=_unique(
            report.strongest_counterargument.evidence_refs
        ),
        falsifiers=falsifiers,
        implications=_unique(
            (timing_section.summary, position_section.summary)
        ),
        probability=report.claim_probability if claim is not None else None,
        settlement_rule=rule,
    )
    quantity = (
        QuantityRule(
            QuantityRuleKind.POSITION_PLAN,
            reference=report.position_plan_id,
        )
        if position_section.outcome
        is FinalDecisionOutcome.POSITION_AVAILABLE
        else QuantityRule(
            QuantityRuleKind.UNAVAILABLE,
            description=position_section.summary,
        )
    )
    action_type = _POSITION_ACTION_TYPES.get(position_plan.action) or (
        ActionType.ADD
        if report.user_boundary.position_state is PositionState.HELD
        else ActionType.BUY
    )
    invalidation_refs = _unique(item.condition_id for item in conditions)

    def scheduled_now() -> SettleableAction:
        start = report.as_of_date
        until = max(start, entry_plan.signal_valid_until or start)
        return SettleableAction(
            action_id=f"action:{report.report_id}:now",
            asset_id=report.asset_id,
            action_type=action_type,
            horizon=report.horizon,
            trigger=ActionTrigger(
                TriggerKind.SCHEDULED,
                valid_from=start,
                valid_until=until,
                scheduled_on=start,
            ),
            quantity_rule=quantity,
            settlement_rule=rule,
            invalidation_refs=invalidation_refs,
        )

    def conditional(block: EntryConditionBlock) -> SettleableAction:
        return SettleableAction(
            action_id=f"action:{report.report_id}:{block.condition_id}",
            asset_id=report.asset_id,
            action_type=action_type,
            horizon=report.horizon,
            trigger=ActionTrigger(
                TriggerKind.CONDITIONAL,
                valid_from=block.valid_from,
                valid_until=block.valid_until,
                observability_key=block.observability_key,
                condition=block.text,
            ),
            quantity_rule=quantity,
            settlement_rule=rule,
            invalidation_refs=invalidation_refs,
        )

    timing = timing_section.outcome
    if timing is FinalDecisionOutcome.TIMING_ACT:
        actions = (scheduled_now(),)
    elif timing is FinalDecisionOutcome.TIMING_STAGE:
        actions = (
            scheduled_now(),
            *(conditional(item) for item in entry_plan.tranche_rules),
        )
    elif timing is FinalDecisionOutcome.TIMING_WAIT:
        actions = tuple(
            conditional(item) for item in entry_plan.entry_conditions
        )
    else:
        actions = ()
    return PublishedJudgements(views=(view,), actions=actions)


class LedgerRecordingStatus(str, Enum):
    RECORDED = "recorded"
    PENDING_REPLAY = "pending_replay"


@dataclass(frozen=True)
class LedgerRecordingOutcome:
    report_id: str
    status: LedgerRecordingStatus
    entry_ids: tuple[str, ...] = ()
    error: str | None = None


class FinalPublicationSourceProtocol(Protocol):
    def list_final_publications(self) -> tuple[Any, ...]: ...


class FinalDecisionLedgerRecorder:
    """Record published judgements; failed recordings can be replayed."""

    def __init__(
        self,
        *,
        ledger: ResearchLedgerService,
        publications: FinalPublicationSourceProtocol,
    ) -> None:
        self.ledger = ledger
        self.publications = publications

    def record(self, attestation: Any) -> LedgerRecordingOutcome:
        try:
            entries = self.ledger.record(
                attestation.judgements.artifacts,
                scope=LedgerScope.PERSONAL,
                user_id=attestation.user_id,
                source_ref=final_report_source_ref(attestation.report_id),
            )
        except (sqlite3.Error, ValueError) as exc:
            return LedgerRecordingOutcome(
                report_id=attestation.report_id,
                status=LedgerRecordingStatus.PENDING_REPLAY,
                error=str(exc),
            )
        return LedgerRecordingOutcome(
            report_id=attestation.report_id,
            status=LedgerRecordingStatus.RECORDED,
            entry_ids=tuple(item.entry_id for item in entries),
        )

    def replay(self) -> tuple[LedgerRecordingOutcome, ...]:
        """Record every publication whose judgements are not yet in the ledger."""

        return tuple(
            self.record(attestation)
            for attestation in self.publications.list_final_publications()
            if not self.ledger.has_source(
                final_report_source_ref(attestation.report_id)
            )
        )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item.strip()))
