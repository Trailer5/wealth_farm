"""Debate records: positions from each party, rebuttals between parties, resolutions and signposts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.research_contracts import ResearchHorizon

PARTY_USER = "user"
PARTY_AGENT = "agent"
PERSONA_PREFIX = "persona:"

# Layer 1: industry, whole-market and world views drawn from news and facts.
# Layer 2: views on a specific asset once the candidates are narrowed down.
LAYER_WORLD = "world"
LAYER_ASSET = "asset"
LAYERS = (LAYER_WORLD, LAYER_ASSET)
LAYER_LABELS = {LAYER_WORLD: "第一层：行业、市场与世界观", LAYER_ASSET: "第二层：具体标的"}

DEBATE_OPEN = "open"
DEBATE_CONCLUDED = "concluded"

# A major event (one of the few per year that move an industry) must end in decisions; only routine
# news and daily observations may conclude without one.
SIGNIFICANCE_MAJOR = "major"
SIGNIFICANCE_ROUTINE = "routine"
SIGNIFICANCES = (SIGNIFICANCE_MAJOR, SIGNIFICANCE_ROUTINE)

DECISION_ACTIONS = ("buy", "add", "hold", "reduce", "sell", "avoid")
DECISION_ACTION_LABELS = {
    "buy": "买入",
    "add": "加仓",
    "hold": "持有",
    "reduce": "减仓",
    "sell": "卖出",
    "avoid": "回避",
}
SIZED_ACTIONS = ("buy", "add", "hold", "reduce")

POSITION_OPEN = "open"
POSITION_STANDS = "stands"
POSITION_REFINED = "refined"
POSITION_REFUTED = "refuted"
POSITION_WITHDRAWN = "withdrawn"
POSITION_STATUSES = (POSITION_OPEN, POSITION_STANDS, POSITION_REFINED, POSITION_REFUTED, POSITION_WITHDRAWN)
POSITION_LABELS = {
    POSITION_OPEN: "待驳斥",
    POSITION_STANDS: "立住",
    POSITION_REFINED: "被修正",
    POSITION_REFUTED: "被驳倒",
    POSITION_WITHDRAWN: "撤回",
}

REBUTTAL_PENDING = "pending"
REBUTTAL_ACCEPTED = "accepted"
REBUTTAL_ANSWERED = "answered"
REBUTTAL_KINDS = ("fact", "inference", "domain")

HORIZONS = tuple(item.value for item in ResearchHorizon)


@dataclass(frozen=True)
class Debate:
    debate_id: str
    user_id: str
    topic: str
    trigger: str
    as_of_date: str
    status: str = DEBATE_OPEN
    evidence_refs: tuple[str, ...] = ()
    layers: tuple[str, ...] = (LAYER_WORLD,)
    subjects: tuple[str, ...] = ()
    parent_debate_id: str | None = None
    significance: str = SIGNIFICANCE_MAJOR
    conclusion: str | None = None
    decisions: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    created_at: str = ""
    concluded_at: str | None = None
    # Each: amended_at, as_of_date, reason, decisions (as given) and replaced (the decisions they took over).
    amendments: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Debate":
        return cls(
            **{
                **payload,
                "evidence_refs": tuple(payload.get("evidence_refs") or ()),
                "layers": tuple(payload.get("layers") or (LAYER_WORLD,)),
                "subjects": tuple(payload.get("subjects") or ()),
                "decisions": tuple(payload.get("decisions") or ()),
                "blockers": tuple(payload.get("blockers") or ()),
                "amendments": tuple(payload.get("amendments") or ()),
            }
        )


@dataclass(frozen=True)
class Position:
    position_id: str
    debate_id: str
    party: str
    statement: str
    mechanism: str
    horizon: str
    falsifiers: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    layer: str = LAYER_WORLD
    subject: str | None = None
    supersedes: str | None = None
    status: str = POSITION_OPEN
    resolution_note: str | None = None
    ledger_refs: tuple[str, ...] = ()
    created_at: str = ""
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self)) | {"status_label": POSITION_LABELS[self.status]}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Position":
        data = {key: value for key, value in payload.items() if key != "status_label"}
        for key in ("falsifiers", "evidence_refs", "ledger_refs"):
            data[key] = tuple(data.get(key) or ())
        return cls(**data)


@dataclass(frozen=True)
class Rebuttal:
    rebuttal_id: str
    debate_id: str
    position_id: str
    party: str
    argument: str
    kind: str
    evidence_refs: tuple[str, ...] = ()
    status: str = REBUTTAL_PENDING
    response: str | None = None
    created_at: str = ""
    closed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Rebuttal":
        return cls(**{**payload, "evidence_refs": tuple(payload.get("evidence_refs") or ())})


@dataclass(frozen=True)
class Signpost:
    """An observation point: what to watch, what counts as triggered, and the action it calls for."""

    signpost_id: str
    debate_id: str
    observation: str
    trigger: str
    action: str
    refs: tuple[str, ...] = ()
    position_ids: tuple[str, ...] = ()
    due_on: str | None = None
    replaced_by: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Signpost":
        return cls(
            **{
                **payload,
                "refs": tuple(payload.get("refs") or ()),
                "position_ids": tuple(payload.get("position_ids") or ()),
            }
        )


@dataclass(frozen=True)
class Participation:
    """One round a persona took part in, including when it had no view: absence is visible, not silent."""

    participation_id: str
    debate_id: str
    party: str
    positions: tuple[str, ...] = ()
    rebuttals: tuple[str, ...] = ()
    cannot_rebut: tuple[dict[str, Any], ...] = ()
    outside_circle: tuple[str, ...] = ()
    no_view: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Participation":
        return cls(
            **{
                **payload,
                **{
                    key: tuple(payload.get(key) or ())
                    for key in ("positions", "rebuttals", "cannot_rebut", "outside_circle")
                },
            }
        )


@dataclass(frozen=True)
class DebateRecord:
    """One debate with everything recorded under it."""

    debate: Debate
    positions: tuple[Position, ...] = field(default_factory=tuple)
    rebuttals: tuple[Rebuttal, ...] = field(default_factory=tuple)
    signposts: tuple[Signpost, ...] = field(default_factory=tuple)
    participations: tuple[Participation, ...] = field(default_factory=tuple)


def _plain(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: list(value) if isinstance(value, tuple) else value for key, value in payload.items()}
