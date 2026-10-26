"""Research task vocabulary shared across layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class ResearchHorizon(str, Enum):
    SHORT = "short"
    MEDIUM_SHORT = "medium_short"
    MEDIUM_LONG = "medium_long"
    LONG = "long"


class ResearchQuestionType(str, Enum):
    UNDERSTANDING = "understanding"
    COMPARISON = "comparison"
    TIMING = "timing"
    SPECULATION = "speculation"
    INVESTMENT = "investment"
    CYCLE_READING = "cycle_reading"
    THESIS_REVIEW = "thesis_review"
    RISK_REVIEW = "risk_review"


class ResearchOutputDepth(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


_QUESTION_HORIZONS: dict[
    ResearchQuestionType,
    frozenset[ResearchHorizon],
] = {
    ResearchQuestionType.TIMING: frozenset(
        {ResearchHorizon.SHORT}
    ),
    ResearchQuestionType.SPECULATION: frozenset(
        {ResearchHorizon.MEDIUM_SHORT}
    ),
    ResearchQuestionType.INVESTMENT: frozenset(
        {ResearchHorizon.MEDIUM_LONG}
    ),
    ResearchQuestionType.CYCLE_READING: frozenset(
        {ResearchHorizon.LONG}
    ),
    ResearchQuestionType.THESIS_REVIEW: frozenset(
        {
            ResearchHorizon.MEDIUM_LONG,
            ResearchHorizon.LONG,
        }
    ),
}


@dataclass(frozen=True)
class ResearchTaskSpec:
    """Validated, serializable description of one research request."""

    task_id: str
    asset_id: str
    profile_id: str
    question_type: ResearchQuestionType
    horizon: ResearchHorizon
    as_of_date: str
    benchmark_asset_id: str | None = None
    peer_asset_ids: tuple[str, ...] = ()
    output_depth: ResearchOutputDepth = (
        ResearchOutputDepth.STANDARD
    )
    requested_metrics: tuple[str, ...] = ()
    requested_outputs: tuple[str, ...] = field(
        default_factory=lambda: (
            "data_summary",
            "metrics",
            "charts",
            "comparison",
            "strategy_results",
            "research_report",
        )
    )

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.asset_id, "asset_id")
        _require_text(self.profile_id, "profile_id")
        if not isinstance(self.question_type, ResearchQuestionType):
            raise TypeError(
                "question_type must be ResearchQuestionType"
            )
        if not isinstance(self.horizon, ResearchHorizon):
            raise TypeError("horizon must be ResearchHorizon")
        if not isinstance(self.output_depth, ResearchOutputDepth):
            raise TypeError(
                "output_depth must be ResearchOutputDepth"
            )
        _parse_iso_date(self.as_of_date)
        _validate_question_horizon(
            self.question_type,
            self.horizon,
        )
        _require_unique(self.peer_asset_ids, "peer_asset_ids")
        if self.asset_id in self.peer_asset_ids:
            raise ValueError(
                "peer_asset_ids must not contain the research asset"
            )
        if (
            self.benchmark_asset_id is not None
            and not self.benchmark_asset_id.strip()
        ):
            raise ValueError(
                "benchmark_asset_id must not be blank"
            )
        if self.benchmark_asset_id == self.asset_id:
            raise ValueError(
                "benchmark_asset_id must differ from asset_id"
            )
        _require_nonempty_unique(
            self.requested_outputs,
            "requested_outputs",
        )
        _require_unique(
            self.requested_metrics,
            "requested_metrics",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "question_type": self.question_type.value,
            "horizon": self.horizon.value,
            "as_of_date": self.as_of_date,
            "benchmark_asset_id": self.benchmark_asset_id,
            "peer_asset_ids": list(self.peer_asset_ids),
            "output_depth": self.output_depth.value,
            "requested_metrics": list(self.requested_metrics),
            "requested_outputs": list(self.requested_outputs),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchTaskSpec":
        allowed = {
            "task_id",
            "asset_id",
            "profile_id",
            "question_type",
            "horizon",
            "as_of_date",
            "benchmark_asset_id",
            "peer_asset_ids",
            "output_depth",
            "requested_outputs",
            "requested_metrics",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                f"Unknown ResearchTaskSpec fields: {sorted(unknown)}"
            )
        return cls(
            task_id=str(payload["task_id"]),
            asset_id=str(payload["asset_id"]),
            profile_id=str(payload["profile_id"]),
            question_type=ResearchQuestionType(
                payload["question_type"]
            ),
            horizon=ResearchHorizon(payload["horizon"]),
            as_of_date=str(payload["as_of_date"]),
            benchmark_asset_id=(
                str(payload["benchmark_asset_id"])
                if payload.get("benchmark_asset_id") is not None
                else None
            ),
            peer_asset_ids=tuple(
                str(value)
                for value in payload.get("peer_asset_ids", ())
            ),
            output_depth=ResearchOutputDepth(
                payload.get(
                    "output_depth",
                    ResearchOutputDepth.STANDARD.value,
                )
            ),
            requested_outputs=tuple(
                str(value)
                for value in payload.get(
                    "requested_outputs",
                    (
                        "data_summary",
                        "metrics",
                        "charts",
                        "comparison",
                        "strategy_results",
                        "research_report",
                    ),
                )
            ),
            requested_metrics=tuple(
                str(value)
                for value in payload.get(
                    "requested_metrics",
                    (),
                )
            ),
        )


def _validate_question_horizon(
    question_type: ResearchQuestionType,
    horizon: ResearchHorizon,
) -> None:
    allowed = _QUESTION_HORIZONS.get(question_type)
    if allowed is not None and horizon not in allowed:
        allowed_values = ", ".join(
            sorted(item.value for item in allowed)
        )
        raise ValueError(
            f"{question_type.value} requires horizon: "
            f"{allowed_values}"
        )


def _parse_iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "as_of_date must use YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != value:
        raise ValueError("as_of_date must use YYYY-MM-DD")
    return parsed


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_unique(
    values: tuple[str, ...],
    field_name: str,
) -> None:
    for value in values:
        _require_text(value, field_name)
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")


def _require_nonempty_unique(
    values: tuple[str, ...],
    field_name: str,
) -> None:
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    _require_unique(values, field_name)
