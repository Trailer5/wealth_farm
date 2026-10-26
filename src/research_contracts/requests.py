"""Structured research request produced by the Agent from a user question."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ._checks import (
    optional_text,
    require_date,
    require_instance,
    require_items,
    require_text,
    require_texts,
)
from .codec import from_payload, to_payload
from .tasks import ResearchHorizon


class SubjectType(str, Enum):
    MACRO = "macro"
    MARKET = "market"
    SECTOR = "sector"
    ASSET = "asset"
    STRATEGY = "strategy"
    PORTFOLIO = "portfolio"


class ResearchIntent(str, Enum):
    UNDERSTAND = "understand"
    EVALUATE = "evaluate"
    DISCOVER = "discover"
    TEST_VIEW = "test_view"
    DECIDE = "decide"
    PLAN = "plan"
    REVIEW = "review"


class Personalization(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ResearchDepth(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


_SUBJECTS_NEEDING_REFS = frozenset(
    {SubjectType.SECTOR, SubjectType.ASSET, SubjectType.STRATEGY}
)


@dataclass(frozen=True)
class SubjectRef:
    """User wording of a research subject and its resolved ID, if any."""

    text: str
    resolved_id: str | None = None

    def __post_init__(self) -> None:
        require_text(self.text, "subject_ref.text")
        optional_text(self.resolved_id, "subject_ref.resolved_id")


@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    subject_type: SubjectType
    subject_refs: tuple[SubjectRef, ...]
    intents: tuple[ResearchIntent, ...]
    personalization: Personalization
    as_of_date: str
    user_id: str | None = None
    user_view: str | None = None
    depth: ResearchDepth = ResearchDepth.STANDARD
    horizon: ResearchHorizon | None = None
    personal_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.request_id, "request_id")
        require_instance(self.subject_type, SubjectType, "subject_type")
        require_items(self.subject_refs, SubjectRef, "subject_refs")
        require_items(
            self.intents,
            ResearchIntent,
            "intents",
            nonempty=True,
        )
        if len(set(self.intents)) != len(self.intents):
            raise ValueError("intents must not contain duplicates")
        require_instance(
            self.personalization,
            Personalization,
            "personalization",
        )
        require_date(self.as_of_date, "as_of_date")
        optional_text(self.user_id, "user_id")
        optional_text(self.user_view, "user_view")
        require_instance(self.depth, ResearchDepth, "depth")
        if self.horizon is not None:
            require_instance(self.horizon, ResearchHorizon, "horizon")
        require_texts(self.personal_refs, "personal_refs")
        self._validate_shape()

    def _validate_shape(self) -> None:
        if (
            self.subject_type in _SUBJECTS_NEEDING_REFS
            and not self.subject_refs
        ):
            raise ValueError(
                f"{self.subject_type.value} requests need subject_refs"
            )
        if (
            self.subject_type is SubjectType.PORTFOLIO
            and self.personalization is not Personalization.REQUIRED
        ):
            raise ValueError(
                "portfolio requests require personalization=required"
            )
        if ResearchIntent.TEST_VIEW in self.intents:
            if self.user_view is None:
                raise ValueError("test_view requires user_view")
        elif self.user_view is not None:
            raise ValueError("user_view is only allowed with test_view")
        if (
            self.personalization is Personalization.NONE
            and self.personal_refs
        ):
            raise ValueError(
                "personal_refs require personalization other than none"
            )
        needs_user = (
            self.personalization is Personalization.REQUIRED
            or self.user_view is not None
            or bool(self.personal_refs)
        )
        if needs_user and self.user_id is None:
            raise ValueError("user_id is required for personal context")

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResearchRequest":
        return from_payload(cls, payload)
