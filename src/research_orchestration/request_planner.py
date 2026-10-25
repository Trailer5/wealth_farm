"""High-level identity and task planning for one research request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Protocol

from src.master_data import ASSET_TYPE_OPEN_FUND, Asset, MasterDataService
from src.research_workflow import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionIntent,
    DecisionLane,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchProfileAssignmentService,
    ResearchQuestionType,
    ResearchTaskFactory,
    ResearchTaskSpec,
)


DEFAULT_EXECUTION_PROFILES = (
    PROFILE_ORDINARY_COMPANY,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
)


class FundIdentityBootstrapProtocol(Protocol):
    def fetch_and_store_fund_basic_info(
        self,
        *,
        symbol: str,
    ) -> object: ...


class ResearchPlanningStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ResearchPlanningRequest:
    as_of_date: str
    asset_id: str | None = None
    symbol: str | None = None
    asset_type: str | None = None
    market: str | None = None
    exchange: str | None = None
    decision_intent: DecisionIntent = (
        DecisionIntent.UNDERSTANDING
    )
    decision_lanes: tuple[DecisionLane, ...] = ()
    horizon: ResearchHorizon = ResearchHorizon.MEDIUM_LONG

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if (self.asset_id is None) == (self.symbol is None):
            raise ValueError(
                "exactly one of asset_id or symbol is required"
            )
        _optional_text(self.asset_id, "asset_id")
        _optional_text(self.symbol, "symbol")
        _optional_text(self.asset_type, "asset_type")
        _optional_text(self.market, "market")
        _optional_text(self.exchange, "exchange")
        if not isinstance(self.decision_intent, DecisionIntent):
            raise TypeError(
                "decision_intent must be DecisionIntent"
            )
        if any(
            not isinstance(item, DecisionLane)
            for item in self.decision_lanes
        ):
            raise TypeError(
                "decision_lanes must contain DecisionLane"
            )
        if len(self.decision_lanes) != len(
            set(self.decision_lanes)
        ):
            raise ValueError("decision_lanes must be unique")
        if (
            self.decision_intent is DecisionIntent.UNDERSTANDING
            and self.decision_lanes
        ):
            raise ValueError(
                "understanding intent must not request decision lanes"
            )
        if not isinstance(self.horizon, ResearchHorizon):
            raise TypeError("horizon must be ResearchHorizon")

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "market": self.market,
            "exchange": self.exchange,
            "decision_intent": self.decision_intent.value,
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "horizon": self.horizon.value,
        }


@dataclass(frozen=True)
class ResearchIdentityCandidate:
    asset_id: str
    symbol: str
    asset_type: str
    display_name: str | None
    market: str | None
    exchange: str | None

    @classmethod
    def from_asset(cls, asset: Asset) -> "ResearchIdentityCandidate":
        return cls(
            asset_id=asset.asset_id,
            symbol=asset.standard_symbol,
            asset_type=asset.asset_type,
            display_name=asset.display_name,
            market=asset.market,
            exchange=asset.exchange,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "display_name": self.display_name,
            "market": self.market,
            "exchange": self.exchange,
        }


@dataclass(frozen=True)
class ResearchPlanningResult:
    status: ResearchPlanningStatus
    request: ResearchPlanningRequest
    effective_decision_lanes: tuple[DecisionLane, ...]
    identity: ResearchIdentityCandidate | None = None
    candidates: tuple[ResearchIdentityCandidate, ...] = ()
    profile_id: str | None = None
    task: ResearchTaskSpec | None = None
    routing_notes: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ResearchPlanningStatus.READY:
            if (
                self.identity is None
                or self.profile_id is None
                or self.task is None
            ):
                raise ValueError(
                    "ready plan requires identity, profile, and task"
                )
        elif not self.issues:
            raise ValueError("non-ready plan requires issues")
        if (
            self.status
            is ResearchPlanningStatus.NEEDS_CLARIFICATION
            and not self.candidates
        ):
            raise ValueError(
                "clarification plan requires identity candidates"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "request": self.request.to_dict(),
            "effective_decision_lanes": [
                item.value for item in self.effective_decision_lanes
            ],
            "identity": (
                self.identity.to_dict()
                if self.identity is not None
                else None
            ),
            "candidates": [
                item.to_dict() for item in self.candidates
            ],
            "profile_id": self.profile_id,
            "task": (
                self.task.to_dict()
                if self.task is not None
                else None
            ),
            "routing_notes": list(self.routing_notes),
            "issues": list(self.issues),
        }


class ResearchRequestPlanner:
    """Resolve one identity and create a supported baseline task."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        task_factory: ResearchTaskFactory,
        profile_assignment: (
            ResearchProfileAssignmentService | None
        ) = None,
        fund_identity_bootstrap: (
            FundIdentityBootstrapProtocol | None
        ) = None,
        supported_profile_ids: tuple[str, ...] = (
            DEFAULT_EXECUTION_PROFILES
        ),
    ) -> None:
        self.master_data = master_data
        self.task_factory = task_factory
        self.profile_assignment = profile_assignment
        self.fund_identity_bootstrap = fund_identity_bootstrap
        self.supported_profile_ids = frozenset(
            supported_profile_ids
        )

    def plan(
        self,
        request: ResearchPlanningRequest,
    ) -> ResearchPlanningResult:
        if not isinstance(request, ResearchPlanningRequest):
            raise TypeError(
                "request must be ResearchPlanningRequest"
            )
        effective_lanes = _effective_lanes(request)
        matches = self._resolve_candidates(request)
        if not matches:
            return ResearchPlanningResult(
                status=ResearchPlanningStatus.UNRESOLVED,
                request=request,
                effective_decision_lanes=effective_lanes,
                issues=("asset_identity_not_found",),
            )
        candidates = tuple(
            ResearchIdentityCandidate.from_asset(asset)
            for asset in matches
        )
        if len(matches) > 1:
            return ResearchPlanningResult(
                status=(
                    ResearchPlanningStatus.NEEDS_CLARIFICATION
                ),
                request=request,
                effective_decision_lanes=effective_lanes,
                candidates=candidates,
                issues=("asset_identity_ambiguous",),
            )

        asset = matches[0]
        profile_id, profile_note = self._profile_for(
            asset,
            request.as_of_date,
        )
        if profile_id not in self.supported_profile_ids:
            return ResearchPlanningResult(
                status=ResearchPlanningStatus.UNSUPPORTED,
                request=request,
                effective_decision_lanes=effective_lanes,
                identity=candidates[0],
                profile_id=profile_id,
                routing_notes=(profile_note,),
                issues=("profile_execution_not_available",),
            )
        question, question_notes = _baseline_question(
            profile_id=profile_id,
            decision_intent=request.decision_intent,
            decision_lanes=effective_lanes,
            horizon=request.horizon,
        )
        task = self.task_factory.create(
            asset_id=asset.asset_id,
            profile_id=profile_id,
            question_type=question,
            horizon=request.horizon,
            as_of_date=request.as_of_date,
        )
        return ResearchPlanningResult(
            status=ResearchPlanningStatus.READY,
            request=request,
            effective_decision_lanes=effective_lanes,
            identity=candidates[0],
            profile_id=profile_id,
            task=task,
            routing_notes=(profile_note, *question_notes),
        )

    def plan_decision_brief(
        self,
        brief: ResearchDecisionBrief,
    ) -> ResearchPlanningResult:
        if not isinstance(brief, ResearchDecisionBrief):
            raise TypeError(
                "brief must be ResearchDecisionBrief"
            )
        return self.plan(
            ResearchPlanningRequest(
                asset_id=brief.asset_id,
                as_of_date=brief.as_of_date,
                decision_intent=brief.decision_intent,
                decision_lanes=brief.decision_lanes,
                horizon=brief.horizon,
            )
        )

    def _resolve_candidates(
        self,
        request: ResearchPlanningRequest,
    ) -> list[Asset]:
        if request.asset_id is not None:
            asset = self.master_data.get_asset(request.asset_id)
            return (
                [asset]
                if asset is not None
                and _matches_hints(asset, request)
                else []
            )

        assert request.symbol is not None
        matches = self.master_data.find_assets(
            standard_symbol=request.symbol,
            asset_type=request.asset_type,
        )
        matches = [
            asset
            for asset in matches
            if _matches_hints(asset, request)
        ]
        if (
            not matches
            and request.asset_type == ASSET_TYPE_OPEN_FUND
            and self.fund_identity_bootstrap is not None
        ):
            self.fund_identity_bootstrap.fetch_and_store_fund_basic_info(
                symbol=request.symbol,
            )
            matches = self.master_data.find_assets(
                standard_symbol=request.symbol,
                asset_type=request.asset_type,
            )
            matches = [
                asset
                for asset in matches
                if _matches_hints(asset, request)
            ]
        return sorted(
            matches,
            key=lambda asset: (
                asset.asset_type,
                asset.market or "",
                asset.exchange or "",
                asset.asset_id,
            ),
        )

    def _profile_for(
        self,
        asset: Asset,
        as_of_date: str,
    ) -> tuple[str, str]:
        if self.profile_assignment is not None:
            assigned = self.profile_assignment.profile_map(
                [asset.asset_id],
                as_of_date=as_of_date,
            ).get(asset.asset_id)
            if assigned is not None:
                self.task_factory.profile_registry.validate_for_asset(
                    assigned,
                    asset,
                )
                return assigned, "profile_assignment"
        profile = self.task_factory.profile_registry.default_for_asset(
            asset
        )
        return profile.profile_id, "asset_type_default_profile"


def _effective_lanes(
    request: ResearchPlanningRequest,
) -> tuple[DecisionLane, ...]:
    if request.decision_intent is DecisionIntent.UNDERSTANDING:
        return ()
    lanes = list(request.decision_lanes)
    if DecisionLane.SUITABILITY not in lanes:
        lanes.insert(0, DecisionLane.SUITABILITY)
    return tuple(lanes)


def _baseline_question(
    *,
    profile_id: str,
    decision_intent: DecisionIntent,
    decision_lanes: tuple[DecisionLane, ...],
    horizon: ResearchHorizon,
) -> tuple[ResearchQuestionType, tuple[str, ...]]:
    if profile_id == PROFILE_OPEN_FUND:
        notes = ("open_fund_baseline_uses_understanding",)
        if decision_intent is not DecisionIntent.UNDERSTANDING:
            notes = (
                *notes,
                "user_decision_objective_preserved_for_exploration",
            )
        return ResearchQuestionType.UNDERSTANDING, notes
    if decision_intent is DecisionIntent.UNDERSTANDING:
        return ResearchQuestionType.UNDERSTANDING, ()
    if decision_intent is DecisionIntent.REVIEW_HOLDING:
        if horizon in {
            ResearchHorizon.MEDIUM_LONG,
            ResearchHorizon.LONG,
        }:
            return ResearchQuestionType.THESIS_REVIEW, ()
        return ResearchQuestionType.RISK_REVIEW, ()
    if (
        DecisionLane.TIMING in decision_lanes
        and horizon is ResearchHorizon.SHORT
    ):
        return ResearchQuestionType.TIMING, ()
    if horizon is ResearchHorizon.MEDIUM_LONG:
        return ResearchQuestionType.INVESTMENT, ()
    return (
        ResearchQuestionType.UNDERSTANDING,
        ("decision_horizon_uses_understanding_baseline",),
    )


def _matches_hints(
    asset: Asset,
    request: ResearchPlanningRequest,
) -> bool:
    return (
        _matches(asset.asset_type, request.asset_type)
        and _matches(asset.market, request.market)
        and _matches(asset.exchange, request.exchange)
    )


def _matches(value: str | None, hint: str | None) -> bool:
    if hint is None:
        return True
    return value is not None and value.casefold() == hint.casefold()


def _optional_text(value: str | None, name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{name} must not be blank")
