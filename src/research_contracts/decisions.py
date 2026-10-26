"""User-owned decision context kept separate from baseline research tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .tasks import ResearchHorizon


class DecisionIntent(str, Enum):
    UNDERSTANDING = "understanding"
    EVALUATE_PURCHASE = "evaluate_purchase"
    REVIEW_HOLDING = "review_holding"


class DecisionLane(str, Enum):
    SUITABILITY = "suitability"
    TIMING = "timing"
    POSITION_SIZING = "position_sizing"


class PositionState(str, Enum):
    NOT_HELD = "not_held"
    HELD = "held"
    UNKNOWN = "unknown"


class DecisionContextRequirement(str, Enum):
    POSITION_STATE = "position_state"
    CURRENT_POSITION_DETAILS = "current_position_details"
    PORTFOLIO_ROLE = "portfolio_role"
    INVESTMENT_MANDATE = "investment_mandate"
    PORTFOLIO_CONSTRAINT_SNAPSHOT = (
        "portfolio_constraint_snapshot"
    )


class DecisionContextStatus(str, Enum):
    READY = "ready"
    USER_CONTEXT_REQUIRED = "user_context_required"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ResearchDecisionBrief:
    """Private user intent and references for one asset decision."""

    brief_id: str
    user_id: str
    asset_id: str
    decision_intent: DecisionIntent
    decision_lanes: tuple[DecisionLane, ...]
    position_state: PositionState
    horizon: ResearchHorizon
    as_of_date: str
    portfolio_role: str | None
    mandate_id: str | None
    portfolio_constraint_snapshot_id: str | None
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    missing_user_context: tuple[
        DecisionContextRequirement,
        ...,
    ]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.brief_id, "brief_id"),
            (self.user_id, "user_id"),
            (self.asset_id, "asset_id"),
        ):
            _text(value, name)
        if not isinstance(self.decision_intent, DecisionIntent):
            raise TypeError(
                "decision_intent must be DecisionIntent"
            )
        if not isinstance(self.position_state, PositionState):
            raise TypeError("position_state must be PositionState")
        if not isinstance(self.horizon, ResearchHorizon):
            raise TypeError("horizon must be ResearchHorizon")
        _enum_tuple(
            self.decision_lanes,
            DecisionLane,
            "decision_lanes",
        )
        _enum_tuple(
            self.missing_user_context,
            DecisionContextRequirement,
            "missing_user_context",
        )
        _text_tuple(self.constraints, "constraints")
        _text_tuple(self.assumptions, "assumptions")
        date.fromisoformat(self.as_of_date)
        datetime.fromisoformat(self.created_at)
        datetime.fromisoformat(self.updated_at)
        _optional_text(self.portfolio_role, "portfolio_role")
        _optional_text(self.mandate_id, "mandate_id")
        _optional_text(
            self.portfolio_constraint_snapshot_id,
            "portfolio_constraint_snapshot_id",
        )
        _validate_decision_shape(self)

    @property
    def position_context_status(self) -> DecisionContextStatus:
        if DecisionLane.POSITION_SIZING not in self.decision_lanes:
            return DecisionContextStatus.NOT_APPLICABLE
        if self.missing_user_context:
            return DecisionContextStatus.USER_CONTEXT_REQUIRED
        return DecisionContextStatus.READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "decision_intent": self.decision_intent.value,
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "position_state": self.position_state.value,
            "horizon": self.horizon.value,
            "as_of_date": self.as_of_date,
            "portfolio_role": self.portfolio_role,
            "mandate_id": self.mandate_id,
            "portfolio_constraint_snapshot_id": (
                self.portfolio_constraint_snapshot_id
            ),
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
            "missing_user_context": [
                item.value for item in self.missing_user_context
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchDecisionBrief":
        allowed = {
            "brief_id",
            "user_id",
            "asset_id",
            "decision_intent",
            "decision_lanes",
            "position_state",
            "horizon",
            "as_of_date",
            "portfolio_role",
            "mandate_id",
            "portfolio_constraint_snapshot_id",
            "constraints",
            "assumptions",
            "missing_user_context",
            "created_at",
            "updated_at",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Unknown ResearchDecisionBrief fields: "
                f"{sorted(unknown)}"
            )
        return cls(
            brief_id=str(payload["brief_id"]),
            user_id=str(payload["user_id"]),
            asset_id=str(payload["asset_id"]),
            decision_intent=DecisionIntent(
                payload["decision_intent"]
            ),
            decision_lanes=tuple(
                DecisionLane(item)
                for item in payload.get("decision_lanes", ())
            ),
            position_state=PositionState(
                payload["position_state"]
            ),
            horizon=ResearchHorizon(payload["horizon"]),
            as_of_date=str(payload["as_of_date"]),
            portfolio_role=_optional_payload_text(
                payload.get("portfolio_role")
            ),
            mandate_id=_optional_payload_text(
                payload.get("mandate_id")
            ),
            portfolio_constraint_snapshot_id=(
                _optional_payload_text(
                    payload.get(
                        "portfolio_constraint_snapshot_id"
                    )
                )
            ),
            constraints=tuple(
                str(item)
                for item in payload.get("constraints", ())
            ),
            assumptions=tuple(
                str(item)
                for item in payload.get("assumptions", ())
            ),
            missing_user_context=tuple(
                DecisionContextRequirement(item)
                for item in payload.get(
                    "missing_user_context",
                    (),
                )
            ),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


def required_user_context(
    *,
    decision_intent: DecisionIntent,
    decision_lanes: tuple[DecisionLane, ...],
    position_state: PositionState,
    has_current_position_details: bool,
    portfolio_role: str | None,
    mandate_id: str | None,
    portfolio_constraint_snapshot_id: str | None,
) -> tuple[DecisionContextRequirement, ...]:
    """Return missing private context without inventing defaults."""

    missing: list[DecisionContextRequirement] = []
    if (
        decision_intent is not DecisionIntent.UNDERSTANDING
        and not _has_text(portfolio_role)
    ):
        missing.append(
            DecisionContextRequirement.PORTFOLIO_ROLE
        )
    if (
        decision_lanes
        and position_state is PositionState.UNKNOWN
    ):
        missing.append(
            DecisionContextRequirement.POSITION_STATE
        )
    if (
        decision_intent is DecisionIntent.REVIEW_HOLDING
        or position_state is PositionState.HELD
    ) and not has_current_position_details:
        missing.append(
            DecisionContextRequirement.CURRENT_POSITION_DETAILS
        )
    if DecisionLane.POSITION_SIZING in decision_lanes:
        if not _has_text(mandate_id):
            missing.append(
                DecisionContextRequirement.INVESTMENT_MANDATE
            )
        if not _has_text(portfolio_constraint_snapshot_id):
            missing.append(
                DecisionContextRequirement.PORTFOLIO_CONSTRAINT_SNAPSHOT
            )
    return tuple(missing)


def _validate_decision_shape(brief: ResearchDecisionBrief) -> None:
    lanes = set(brief.decision_lanes)
    if brief.decision_intent is DecisionIntent.UNDERSTANDING:
        if lanes:
            raise ValueError(
                "understanding intent must not request decision lanes"
            )
    elif DecisionLane.SUITABILITY not in lanes:
        raise ValueError(
            "decision intent requires the suitability lane"
        )
    if (
        DecisionLane.TIMING in lanes
        or DecisionLane.POSITION_SIZING in lanes
    ) and DecisionLane.SUITABILITY not in lanes:
        raise ValueError(
            "timing and position sizing require suitability"
        )
    if (
        brief.decision_intent is DecisionIntent.REVIEW_HOLDING
        and brief.position_state is PositionState.NOT_HELD
    ):
        raise ValueError(
            "review_holding intent cannot use not_held state"
        )


def _enum_tuple(
    values: tuple[Enum, ...],
    enum_type: type[Enum],
    name: str,
) -> None:
    if any(not isinstance(item, enum_type) for item in values):
        raise TypeError(f"{name} must contain {enum_type.__name__}")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text_tuple(values: tuple[str, ...], name: str) -> None:
    if any(not _has_text(item) for item in values):
        raise ValueError(f"{name} must contain non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not _has_text(value):
        raise ValueError(f"{name} must not be blank")


def _optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _text(value, name)


def _optional_payload_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
