"""Auditable contracts for event-to-stock name attention discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from src.research_contracts import ResearchTaskSpec


REQUIRED_RELATION_CHECKS = (
    "business_scope",
    "ownership",
    "commercial_link",
    "disclosure_link",
)
REQUIRED_CATALYST_CHECKS = (
    "company_disclosure",
    "industry_theme",
    "corporate_action",
    "market_narrative",
)


class EventDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class AttentionMatchChannel(str, Enum):
    NAME_SEMANTIC = "name_semantic"
    PHONETIC = "phonetic"
    CROSS_LANGUAGE_ACRONYM = "cross_language_acronym"
    BRAND_NAME = "brand_name"
    TICKER_CONFUSION = "ticker_confusion"


class EventAttentionScope(str, Enum):
    GLOBAL_EVENT = "global_event"
    GLOBAL_NARRATIVE = "global_narrative"
    DOMESTIC_EVENT = "domestic_event"


class NameMatchType(str, Enum):
    EXACT_SUBSTRING = "exact_substring"
    COMPOSITIONAL = "compositional"
    PHONETIC = "phonetic"
    PINYIN_INITIALS = "pinyin_initials"


class RelationCheckResult(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class BusinessRelationStatus(str, Enum):
    CONFIRMED_UNRELATED = "confirmed_unrelated"
    RELATED = "related"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AlternativeCatalystStatus(str, Enum):
    CLEAR = "clear"
    CONTAMINATED = "contaminated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ConsensusDimension(str, Enum):
    EVENT_SALIENCE = "event_salience"
    GLOBAL_REACH = "global_reach"
    CHINA_ATTENTION = "china_attention"
    NARRATIVE_DISTANCE = "narrative_distance"
    NARRATIVE_SIMPLICITY = "narrative_simplicity"
    COORDINATION_FOCAL_POINT = "coordination_focal_point"
    PRICE_ELASTICITY = "price_elasticity"
    PROPAGATION = "propagation"
    ARBITRAGE_CONSTRAINTS = "arbitrage_constraints"
    RISK_APPETITE = "risk_appetite"


class ConsensusFormationStatus(str, Enum):
    CONFIRMED = "confirmed"
    FORMING = "forming"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EventShockDimension(str, Enum):
    DISCRETE_TIMESTAMP = "discrete_timestamp"
    SURPRISE_OR_DECISIVENESS = "surprise_or_decisiveness"
    GLOBAL_MASS_ATTENTION = "global_mass_attention"
    CHINA_MASS_ATTENTION = "china_mass_attention"
    CLEAR_DIRECTIONAL_VALENCE = "clear_directional_valence"
    UNIQUE_NARRATIVE = "unique_narrative"


class EventShockStatus(str, Enum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AttentionAttributionMode(str, Enum):
    PURE_NAME_EFFECT = "pure_name_effect"
    MIXED_CATALYST_OVERLAY = "mixed_catalyst_overlay"


class AttentionAttributionStatus(str, Enum):
    CONFIRMED = "confirmed"
    PLAUSIBLE = "plausible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AttentionOutcomeLayer(str, Enum):
    SEARCH_ATTENTION = "search_attention"
    SENTIMENT = "sentiment"
    PURCHASE_INTENT = "purchase_intent"
    REALIZED_SALES = "realized_sales"
    SECURITY_PRICE = "security_price"


class NameAssociationConclusion(str, Enum):
    ATTENTION_ONLY = "attention_only"
    BEHAVIOR_CONFIRMED = "behavior_confirmed"
    ECONOMIC_EFFECT_CONFIRMED = "economic_effect_confirmed"
    COUNTEREVIDENCE = "counterevidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EventTimingQuality(str, Enum):
    PREOPEN = "preopen"
    POSTCLOSE = "postclose"
    NON_TRADING_DAY = "non_trading_day"
    INTRADAY_AMBIGUOUS = "intraday_ambiguous"


class MarketConfirmationMode(str, Enum):
    PRICE_OR_VOLUME = "price_or_volume"
    PRICE_AND_VOLUME = "price_and_volume"


class EventNameDiscoveryMode(str, Enum):
    PREDICTIVE = "predictive"
    CONFIRMATORY = "confirmatory"


class EventNameMemberStatus(str, Enum):
    CANDIDATE = "candidate"
    NO_LANGUAGE_MATCH = "no_language_match"
    MATCH_DIRECTION_AMBIGUOUS = "match_direction_ambiguous"
    RELATED_EXCLUDED = "related_excluded"
    RELATION_EVIDENCE_INSUFFICIENT = (
        "relation_evidence_insufficient"
    )
    MARKET_REACTION_MISSING = "market_reaction_missing"
    MARKET_REACTION_NOT_CONFIRMED = "market_reaction_not_confirmed"
    DIRECTION_MISMATCH = "direction_mismatch"
    TIMING_AMBIGUOUS = "timing_ambiguous"
    ALTERNATIVE_CATALYST_PRESENT = "alternative_catalyst_present"
    CATALYST_EVIDENCE_INSUFFICIENT = (
        "catalyst_evidence_insufficient"
    )
    CONSENSUS_EVIDENCE_INSUFFICIENT = (
        "consensus_evidence_insufficient"
    )
    CANDIDATE_LIMIT = "candidate_limit"


@dataclass(frozen=True)
class EventPhrase:
    phrase_id: str
    text: str
    direction: EventDirection
    components: tuple[str, ...]
    phonetic_form: str | None = None
    initials: str | None = None
    priority: int = 100
    channel: AttentionMatchChannel = (
        AttentionMatchChannel.NAME_SEMANTIC
    )

    def __post_init__(self) -> None:
        _text(self.phrase_id, "phrase_id")
        _text(self.text, "text")
        _unique_texts(self.components, "components", require=True)
        if self.phonetic_form is not None:
            _text(self.phonetic_form, "phonetic_form")
        if self.initials is not None:
            _text(self.initials, "initials")
        if self.priority < 0:
            raise ValueError("phrase priority must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phrase_id": self.phrase_id,
            "text": self.text,
            "direction": self.direction.value,
            "components": list(self.components),
            "phonetic_form": self.phonetic_form,
            "initials": self.initials,
            "priority": self.priority,
            "channel": self.channel.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EventPhrase":
        return cls(
            phrase_id=str(payload["phrase_id"]),
            text=str(payload["text"]),
            direction=EventDirection(payload["direction"]),
            components=tuple(payload["components"]),
            phonetic_form=payload.get("phonetic_form"),
            initials=payload.get("initials"),
            priority=int(payload.get("priority", 100)),
            channel=AttentionMatchChannel(
                payload.get(
                    "channel",
                    AttentionMatchChannel.NAME_SEMANTIC.value,
                )
            ),
        )


@dataclass(frozen=True)
class ExternalEventAnchor:
    event_id: str
    title: str
    event_kind: str
    occurred_at: str
    first_public_at: str
    source: str
    source_url: str
    phrases: tuple[EventPhrase, ...]
    evidence_refs: tuple[str, ...]
    attention_scope: EventAttentionScope = (
        EventAttentionScope.DOMESTIC_EVENT
    )

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.title, "title"),
            (self.event_kind, "event_kind"),
            (self.source, "source"),
            (self.source_url, "source_url"),
        ):
            _text(value, name)
        _aware_datetime(self.occurred_at, "occurred_at")
        _aware_datetime(self.first_public_at, "first_public_at")
        if not self.source_url.startswith("https://"):
            raise ValueError("source_url must use https")
        if not self.phrases:
            raise ValueError("external event requires phrases")
        phrase_ids = [item.phrase_id for item in self.phrases]
        if len(phrase_ids) != len(set(phrase_ids)):
            raise ValueError("event phrase IDs must be unique")
        priorities = [item.priority for item in self.phrases]
        if len(priorities) != len(set(priorities)):
            raise ValueError("event phrase priorities must be unique")
        _unique_texts(
            self.evidence_refs,
            "evidence_refs",
            require=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "event_kind": self.event_kind,
            "occurred_at": self.occurred_at,
            "first_public_at": self.first_public_at,
            "source": self.source,
            "source_url": self.source_url,
            "phrases": [item.to_dict() for item in self.phrases],
            "evidence_refs": list(self.evidence_refs),
            "attention_scope": self.attention_scope.value,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ExternalEventAnchor":
        return cls(
            event_id=str(payload["event_id"]),
            title=str(payload["title"]),
            event_kind=str(payload["event_kind"]),
            occurred_at=str(payload["occurred_at"]),
            first_public_at=str(payload["first_public_at"]),
            source=str(payload["source"]),
            source_url=str(payload["source_url"]),
            phrases=tuple(
                EventPhrase.from_dict(item)
                for item in payload["phrases"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            attention_scope=EventAttentionScope(
                payload.get(
                    "attention_scope",
                    EventAttentionScope.DOMESTIC_EVENT.value,
                )
            ),
        )


@dataclass(frozen=True)
class AssetNameAlias:
    name: str
    alias_type: str
    confidence: Decimal
    source: str

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _text(self.alias_type, "alias_type")
        _text(self.source, "source")
        _finite(self.confidence, "confidence")
        if not ZERO <= self.confidence <= Decimal("1"):
            raise ValueError("alias confidence must be in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alias_type": self.alias_type,
            "confidence": format(self.confidence, "f"),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AssetNameAlias":
        return cls(
            name=str(payload["name"]),
            alias_type=str(payload["alias_type"]),
            confidence=Decimal(str(payload["confidence"])),
            source=str(payload["source"]),
        )


ZERO = Decimal("0")


@dataclass(frozen=True)
class AssetNameSnapshot:
    asset_id: str
    standard_symbol: str
    name: str
    effective_from: str
    effective_to: str | None
    aliases: tuple[AssetNameAlias, ...]
    profile_id: str
    source: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.standard_symbol, "standard_symbol"),
            (self.name, "name"),
            (self.profile_id, "profile_id"),
            (self.source, "source"),
        ):
            _text(value, name)
        start = date.fromisoformat(self.effective_from)
        if self.effective_to is not None:
            end = date.fromisoformat(self.effective_to)
            if end < start:
                raise ValueError(
                    "effective_to must not precede effective_from"
                )
        alias_names = [item.name for item in self.aliases]
        if len(alias_names) != len(set(alias_names)):
            raise ValueError("alias names must not contain duplicates")

    def active_on(self, value_date: str) -> bool:
        target = date.fromisoformat(value_date)
        start = date.fromisoformat(self.effective_from)
        end = (
            date.fromisoformat(self.effective_to)
            if self.effective_to is not None
            else None
        )
        return target >= start and (end is None or target <= end)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (self.name, *(item.name for item in self.aliases))
            )
        )

    @property
    def name_variants(
        self,
    ) -> tuple[tuple[str, str, Decimal], ...]:
        return (
            (self.name, "official", Decimal("1")),
            *tuple(
                (item.name, item.alias_type, item.confidence)
                for item in self.aliases
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "standard_symbol": self.standard_symbol,
            "name": self.name,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "aliases": [item.to_dict() for item in self.aliases],
            "profile_id": self.profile_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "AssetNameSnapshot":
        return cls(
            asset_id=str(payload["asset_id"]),
            standard_symbol=str(payload["standard_symbol"]),
            name=str(payload["name"]),
            effective_from=str(payload["effective_from"]),
            effective_to=payload.get("effective_to"),
            aliases=tuple(
                AssetNameAlias.from_dict(item)
                for item in (payload.get("aliases") or ())
            ),
            profile_id=str(payload["profile_id"]),
            source=str(payload["source"]),
        )


@dataclass(frozen=True)
class NameMatchEvidence:
    asset_id: str
    stock_name: str
    phrase_id: str
    phrase_text: str
    phrase_priority: int
    attention_channel: AttentionMatchChannel
    direction: EventDirection
    match_type: NameMatchType
    tier: int
    matched_components: tuple[str, ...]
    normalized_stock_name: str
    normalized_phrase: str
    stock_phonetic: str | None
    phrase_phonetic: str | None
    phonetic_distance: int | None
    name_source_type: str
    name_confidence: Decimal
    explanation: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.stock_name, "stock_name"),
            (self.phrase_id, "phrase_id"),
            (self.phrase_text, "phrase_text"),
            (self.normalized_stock_name, "normalized_stock_name"),
            (self.normalized_phrase, "normalized_phrase"),
            (self.name_source_type, "name_source_type"),
            (self.explanation, "explanation"),
        ):
            _text(value, name)
        if self.tier not in {1, 2, 3, 4}:
            raise ValueError("match tier must be 1, 2, 3, or 4")
        if self.phrase_priority < 0:
            raise ValueError("phrase_priority must be non-negative")
        _finite(self.name_confidence, "name_confidence")
        if not ZERO <= self.name_confidence <= Decimal("1"):
            raise ValueError("name_confidence must be in [0, 1]")
        _unique_texts(
            self.matched_components,
            "matched_components",
            require=True,
        )
        if self.phonetic_distance is not None:
            if self.phonetic_distance < 0:
                raise ValueError(
                    "phonetic_distance must be non-negative"
                )
            if (
                self.stock_phonetic is None
                or self.phrase_phonetic is None
            ):
                raise ValueError(
                    "phonetic distance requires both phonetic forms"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "stock_name": self.stock_name,
            "phrase_id": self.phrase_id,
            "phrase_text": self.phrase_text,
            "phrase_priority": self.phrase_priority,
            "attention_channel": self.attention_channel.value,
            "direction": self.direction.value,
            "match_type": self.match_type.value,
            "tier": self.tier,
            "matched_components": list(self.matched_components),
            "normalized_stock_name": self.normalized_stock_name,
            "normalized_phrase": self.normalized_phrase,
            "stock_phonetic": self.stock_phonetic,
            "phrase_phonetic": self.phrase_phonetic,
            "phonetic_distance": self.phonetic_distance,
            "name_source_type": self.name_source_type,
            "name_confidence": format(self.name_confidence, "f"),
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "NameMatchEvidence":
        return cls(
            asset_id=str(payload["asset_id"]),
            stock_name=str(payload["stock_name"]),
            phrase_id=str(payload["phrase_id"]),
            phrase_text=str(payload["phrase_text"]),
            phrase_priority=int(payload["phrase_priority"]),
            attention_channel=AttentionMatchChannel(
                payload["attention_channel"]
            ),
            direction=EventDirection(payload["direction"]),
            match_type=NameMatchType(payload["match_type"]),
            tier=int(payload["tier"]),
            matched_components=tuple(payload["matched_components"]),
            normalized_stock_name=str(
                payload["normalized_stock_name"]
            ),
            normalized_phrase=str(payload["normalized_phrase"]),
            stock_phonetic=payload.get("stock_phonetic"),
            phrase_phonetic=payload.get("phrase_phonetic"),
            phonetic_distance=payload.get("phonetic_distance"),
            name_source_type=str(payload["name_source_type"]),
            name_confidence=Decimal(
                str(payload["name_confidence"])
            ),
            explanation=str(payload["explanation"]),
        )


@dataclass(frozen=True)
class BusinessRelationEvidence:
    check_type: str
    result: RelationCheckResult
    detail: str
    source_ref: str
    available_at: str

    def __post_init__(self) -> None:
        if self.check_type not in REQUIRED_RELATION_CHECKS:
            raise ValueError(
                f"unsupported relation check: {self.check_type}"
            )
        _text(self.detail, "detail")
        _text(self.source_ref, "source_ref")
        _aware_datetime(self.available_at, "available_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type,
            "result": self.result.value,
            "detail": self.detail,
            "source_ref": self.source_ref,
            "available_at": self.available_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "BusinessRelationEvidence":
        return cls(
            check_type=str(payload["check_type"]),
            result=RelationCheckResult(payload["result"]),
            detail=str(payload["detail"]),
            source_ref=str(payload["source_ref"]),
            available_at=str(payload["available_at"]),
        )


@dataclass(frozen=True)
class BusinessRelationAssessment:
    asset_id: str
    status: BusinessRelationStatus
    evidence: tuple[BusinessRelationEvidence, ...]
    assessed_at: str

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _aware_datetime(self.assessed_at, "assessed_at")
        check_types = [item.check_type for item in self.evidence]
        if len(check_types) != len(set(check_types)):
            raise ValueError("relation check types must be unique")
        expected = _relation_status(self.evidence)
        if self.status is not expected:
            raise ValueError(
                f"relation status must be {expected.value}"
            )

    @classmethod
    def evaluate(
        cls,
        *,
        asset_id: str,
        evidence: tuple[BusinessRelationEvidence, ...],
        assessed_at: str,
    ) -> "BusinessRelationAssessment":
        return cls(
            asset_id=asset_id,
            status=_relation_status(evidence),
            evidence=evidence,
            assessed_at=assessed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "BusinessRelationAssessment":
        return cls(
            asset_id=str(payload["asset_id"]),
            status=BusinessRelationStatus(payload["status"]),
            evidence=tuple(
                BusinessRelationEvidence.from_dict(item)
                for item in payload["evidence"]
            ),
            assessed_at=str(payload["assessed_at"]),
        )


@dataclass(frozen=True)
class AlternativeCatalystEvidence:
    check_type: str
    result: RelationCheckResult
    detail: str
    source_ref: str
    available_at: str

    def __post_init__(self) -> None:
        if self.check_type not in REQUIRED_CATALYST_CHECKS:
            raise ValueError(
                f"unsupported catalyst check: {self.check_type}"
            )
        _text(self.detail, "detail")
        _text(self.source_ref, "source_ref")
        _aware_datetime(self.available_at, "available_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type,
            "result": self.result.value,
            "detail": self.detail,
            "source_ref": self.source_ref,
            "available_at": self.available_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "AlternativeCatalystEvidence":
        return cls(
            check_type=str(payload["check_type"]),
            result=RelationCheckResult(payload["result"]),
            detail=str(payload["detail"]),
            source_ref=str(payload["source_ref"]),
            available_at=str(payload["available_at"]),
        )


@dataclass(frozen=True)
class AlternativeCatalystAssessment:
    asset_id: str
    status: AlternativeCatalystStatus
    evidence: tuple[AlternativeCatalystEvidence, ...]
    window_start_at: str
    window_end_at: str
    queried_sources: tuple[str, ...]
    query_refs: tuple[str, ...]
    coverage_complete: bool
    assessed_at: str

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _aware_datetime(self.assessed_at, "assessed_at")
        window_start = _aware_datetime(
            self.window_start_at,
            "window_start_at",
        )
        window_end = _aware_datetime(
            self.window_end_at,
            "window_end_at",
        )
        if window_end < window_start:
            raise ValueError(
                "catalyst window_end_at must not precede window_start_at"
            )
        _unique_texts(
            self.queried_sources,
            "queried_sources",
            require=self.coverage_complete,
        )
        _unique_texts(
            self.query_refs,
            "query_refs",
            require=self.coverage_complete,
        )
        if not isinstance(self.coverage_complete, bool):
            raise TypeError("coverage_complete must be bool")
        check_types = [item.check_type for item in self.evidence]
        if len(check_types) != len(set(check_types)):
            raise ValueError("catalyst check types must be unique")
        expected = _catalyst_status(
            self.evidence,
            coverage_complete=self.coverage_complete,
        )
        if self.status is not expected:
            raise ValueError(
                f"catalyst status must be {expected.value}"
            )

    @classmethod
    def evaluate(
        cls,
        *,
        asset_id: str,
        evidence: tuple[AlternativeCatalystEvidence, ...],
        window_start_at: str,
        window_end_at: str,
        queried_sources: tuple[str, ...],
        query_refs: tuple[str, ...],
        coverage_complete: bool,
        assessed_at: str,
    ) -> "AlternativeCatalystAssessment":
        return cls(
            asset_id=asset_id,
            status=_catalyst_status(
                evidence,
                coverage_complete=coverage_complete,
            ),
            evidence=evidence,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
            queried_sources=queried_sources,
            query_refs=query_refs,
            coverage_complete=coverage_complete,
            assessed_at=assessed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "window_start_at": self.window_start_at,
            "window_end_at": self.window_end_at,
            "queried_sources": list(self.queried_sources),
            "query_refs": list(self.query_refs),
            "coverage_complete": self.coverage_complete,
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "AlternativeCatalystAssessment":
        return cls(
            asset_id=str(payload["asset_id"]),
            status=AlternativeCatalystStatus(payload["status"]),
            evidence=tuple(
                AlternativeCatalystEvidence.from_dict(item)
                for item in payload["evidence"]
            ),
            window_start_at=str(payload["window_start_at"]),
            window_end_at=str(payload["window_end_at"]),
            queried_sources=tuple(payload["queried_sources"]),
            query_refs=tuple(payload["query_refs"]),
            coverage_complete=bool(payload["coverage_complete"]),
            assessed_at=str(payload["assessed_at"]),
        )


@dataclass(frozen=True)
class EventShockEvidence:
    dimension: EventShockDimension
    result: RelationCheckResult
    detail: str
    source_ref: str
    available_at: str

    def __post_init__(self) -> None:
        _text(self.detail, "detail")
        _text(self.source_ref, "source_ref")
        _aware_datetime(self.available_at, "available_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "result": self.result.value,
            "detail": self.detail,
            "source_ref": self.source_ref,
            "available_at": self.available_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EventShockEvidence":
        return cls(
            dimension=EventShockDimension(payload["dimension"]),
            result=RelationCheckResult(payload["result"]),
            detail=str(payload["detail"]),
            source_ref=str(payload["source_ref"]),
            available_at=str(payload["available_at"]),
        )


@dataclass(frozen=True)
class EventShockAssessment:
    event_id: str
    status: EventShockStatus
    evidence: tuple[EventShockEvidence, ...]
    coverage_complete: bool
    assessed_at: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _aware_datetime(self.assessed_at, "assessed_at")
        dimensions = [item.dimension for item in self.evidence]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("event shock dimensions must be unique")
        expected = _event_shock_status(
            self.evidence,
            coverage_complete=self.coverage_complete,
        )
        if self.status is not expected:
            raise ValueError(
                f"event shock status must be {expected.value}"
            )

    @classmethod
    def evaluate(
        cls,
        *,
        event_id: str,
        evidence: tuple[EventShockEvidence, ...],
        coverage_complete: bool,
        assessed_at: str,
    ) -> "EventShockAssessment":
        return cls(
            event_id=event_id,
            status=_event_shock_status(
                evidence,
                coverage_complete=coverage_complete,
            ),
            evidence=evidence,
            coverage_complete=coverage_complete,
            assessed_at=assessed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "coverage_complete": self.coverage_complete,
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EventShockAssessment":
        return cls(
            event_id=str(payload["event_id"]),
            status=EventShockStatus(payload["status"]),
            evidence=tuple(
                EventShockEvidence.from_dict(item)
                for item in payload["evidence"]
            ),
            coverage_complete=bool(payload["coverage_complete"]),
            assessed_at=str(payload["assessed_at"]),
        )


@dataclass(frozen=True)
class ConsensusConditionEvidence:
    dimension: ConsensusDimension
    result: RelationCheckResult
    detail: str
    source_ref: str
    available_at: str

    def __post_init__(self) -> None:
        _text(self.detail, "detail")
        _text(self.source_ref, "source_ref")
        _aware_datetime(self.available_at, "available_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "result": self.result.value,
            "detail": self.detail,
            "source_ref": self.source_ref,
            "available_at": self.available_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ConsensusConditionEvidence":
        return cls(
            dimension=ConsensusDimension(payload["dimension"]),
            result=RelationCheckResult(payload["result"]),
            detail=str(payload["detail"]),
            source_ref=str(payload["source_ref"]),
            available_at=str(payload["available_at"]),
        )


@dataclass(frozen=True)
class ConsensusFormationAssessment:
    asset_id: str
    event_id: str
    status: ConsensusFormationStatus
    evidence: tuple[ConsensusConditionEvidence, ...]
    coverage_complete: bool
    assessed_at: str

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.event_id, "event_id")
        _aware_datetime(self.assessed_at, "assessed_at")
        dimensions = [item.dimension for item in self.evidence]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("consensus dimensions must be unique")
        if not isinstance(self.coverage_complete, bool):
            raise TypeError("coverage_complete must be bool")
        expected = _consensus_status(
            self.evidence,
            coverage_complete=self.coverage_complete,
        )
        if self.status is not expected:
            raise ValueError(
                f"consensus status must be {expected.value}"
            )

    @classmethod
    def evaluate(
        cls,
        *,
        asset_id: str,
        event_id: str,
        evidence: tuple[ConsensusConditionEvidence, ...],
        coverage_complete: bool,
        assessed_at: str,
    ) -> "ConsensusFormationAssessment":
        return cls(
            asset_id=asset_id,
            event_id=event_id,
            status=_consensus_status(
                evidence,
                coverage_complete=coverage_complete,
            ),
            evidence=evidence,
            coverage_complete=coverage_complete,
            assessed_at=assessed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "event_id": self.event_id,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "coverage_complete": self.coverage_complete,
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ConsensusFormationAssessment":
        return cls(
            asset_id=str(payload["asset_id"]),
            event_id=str(payload["event_id"]),
            status=ConsensusFormationStatus(payload["status"]),
            evidence=tuple(
                ConsensusConditionEvidence.from_dict(item)
                for item in payload["evidence"]
            ),
            coverage_complete=bool(payload["coverage_complete"]),
            assessed_at=str(payload["assessed_at"]),
        )


@dataclass(frozen=True)
class AttentionAttributionAssessment:
    asset_id: str
    event_id: str
    mode: AttentionAttributionMode
    status: AttentionAttributionStatus
    direct_catalysts: tuple[str, ...]
    comparison_refs: tuple[str, ...]
    observed_return: Decimal | None
    baseline_expected_return: Decimal | None
    incremental_return: Decimal | None
    methodology: str
    available_at: str

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.event_id, "event_id")
        _text(self.methodology, "methodology")
        _aware_datetime(self.available_at, "available_at")
        _unique_texts(self.direct_catalysts, "direct_catalysts")
        _unique_texts(self.comparison_refs, "comparison_refs")
        for value, name in (
            (self.observed_return, "observed_return"),
            (
                self.baseline_expected_return,
                "baseline_expected_return",
            ),
            (self.incremental_return, "incremental_return"),
        ):
            if value is not None:
                _finite(value, name)
        if (
            self.mode
            is AttentionAttributionMode.MIXED_CATALYST_OVERLAY
            and not self.direct_catalysts
        ):
            raise ValueError(
                "mixed attribution requires direct catalysts"
            )
        if (
            self.status is AttentionAttributionStatus.CONFIRMED
            and (
                self.baseline_expected_return is None
                or self.incremental_return is None
                or not self.comparison_refs
            )
        ):
            raise ValueError(
                "confirmed attribution requires baseline and comparisons"
            )
        if (
            self.status is AttentionAttributionStatus.PLAUSIBLE
            and not self.comparison_refs
        ):
            raise ValueError(
                "plausible attribution requires comparison evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "event_id": self.event_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "direct_catalysts": list(self.direct_catalysts),
            "comparison_refs": list(self.comparison_refs),
            "observed_return": _decimal_text(self.observed_return),
            "baseline_expected_return": _decimal_text(
                self.baseline_expected_return
            ),
            "incremental_return": _decimal_text(
                self.incremental_return
            ),
            "methodology": self.methodology,
            "available_at": self.available_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "AttentionAttributionAssessment":
        return cls(
            asset_id=str(payload["asset_id"]),
            event_id=str(payload["event_id"]),
            mode=AttentionAttributionMode(payload["mode"]),
            status=AttentionAttributionStatus(payload["status"]),
            direct_catalysts=tuple(payload["direct_catalysts"]),
            comparison_refs=tuple(payload["comparison_refs"]),
            observed_return=_optional_decimal(
                payload.get("observed_return")
            ),
            baseline_expected_return=_optional_decimal(
                payload.get("baseline_expected_return")
            ),
            incremental_return=_optional_decimal(
                payload.get("incremental_return")
            ),
            methodology=str(payload["methodology"]),
            available_at=str(payload["available_at"]),
        )


@dataclass(frozen=True)
class AttentionOutcomeObservation:
    target_id: str
    target_type: str
    target_name: str
    event_id: str
    layer: AttentionOutcomeLayer
    expected_direction: EventDirection
    result: RelationCheckResult
    observed_change: Decimal | None
    unit: str
    window_start: str
    window_end: str
    source_ref: str
    available_at: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.target_id, "target_id"),
            (self.target_type, "target_type"),
            (self.target_name, "target_name"),
            (self.event_id, "event_id"),
            (self.unit, "unit"),
            (self.source_ref, "source_ref"),
        ):
            _text(value, name)
        start = date.fromisoformat(self.window_start)
        end = date.fromisoformat(self.window_end)
        if end < start:
            raise ValueError(
                "outcome window_end must not precede window_start"
            )
        _aware_datetime(self.available_at, "available_at")
        if self.observed_change is not None:
            _finite(self.observed_change, "observed_change")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type,
            "target_name": self.target_name,
            "event_id": self.event_id,
            "layer": self.layer.value,
            "expected_direction": self.expected_direction.value,
            "result": self.result.value,
            "observed_change": _decimal_text(self.observed_change),
            "unit": self.unit,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "source_ref": self.source_ref,
            "available_at": self.available_at,
        }


@dataclass(frozen=True)
class NameAssociationCaseAssessment:
    event_id: str
    target_id: str
    target_name: str
    channel: AttentionMatchChannel
    observations: tuple[AttentionOutcomeObservation, ...]
    conclusion: NameAssociationConclusion

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.target_id, "target_id")
        _text(self.target_name, "target_name")
        if not self.observations:
            raise ValueError(
                "name association case requires observations"
            )
        if any(
            item.event_id != self.event_id
            or item.target_id != self.target_id
            for item in self.observations
        ):
            raise ValueError(
                "outcomes must match case event and target"
            )
        layers = [item.layer for item in self.observations]
        if len(layers) != len(set(layers)):
            raise ValueError("outcome layers must be unique")
        expected = _name_association_conclusion(self.observations)
        if self.conclusion is not expected:
            raise ValueError(
                f"name association conclusion must be {expected.value}"
            )

    @classmethod
    def evaluate(
        cls,
        *,
        event_id: str,
        target_id: str,
        target_name: str,
        channel: AttentionMatchChannel,
        observations: tuple[AttentionOutcomeObservation, ...],
    ) -> "NameAssociationCaseAssessment":
        return cls(
            event_id=event_id,
            target_id=target_id,
            target_name=target_name,
            channel=channel,
            observations=observations,
            conclusion=_name_association_conclusion(observations),
        )


@dataclass(frozen=True)
class MarketReactionSnapshot:
    asset_id: str
    event_id: str
    event_first_public_at: str
    event_trade_date: str
    observed_through: str
    abnormal_return: Decimal
    volume_zscore: Decimal | None
    reversal_abnormal_return: Decimal | None
    timing_quality: EventTimingQuality
    limit_state: str
    calendar_id: str
    calculation_version: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.event_id, "event_id")
        _aware_datetime(
            self.event_first_public_at,
            "event_first_public_at",
        )
        event_date = date.fromisoformat(self.event_trade_date)
        observed = date.fromisoformat(self.observed_through)
        if observed < event_date:
            raise ValueError(
                "observed_through must not precede event_trade_date"
            )
        _finite(self.abnormal_return, "abnormal_return")
        if self.volume_zscore is not None:
            _finite(self.volume_zscore, "volume_zscore")
        if self.reversal_abnormal_return is not None:
            _finite(
                self.reversal_abnormal_return,
                "reversal_abnormal_return",
            )
            if observed <= event_date:
                raise ValueError(
                    "reversal observation requires a later "
                    "observed_through date"
                )
        _text(self.limit_state, "limit_state")
        _text(self.calendar_id, "calendar_id")
        _text(self.calculation_version, "calculation_version")
        _unique_texts(
            self.source_refs,
            "source_refs",
            require=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "event_id": self.event_id,
            "event_first_public_at": self.event_first_public_at,
            "event_trade_date": self.event_trade_date,
            "observed_through": self.observed_through,
            "abnormal_return": format(self.abnormal_return, "f"),
            "volume_zscore": (
                format(self.volume_zscore, "f")
                if self.volume_zscore is not None
                else None
            ),
            "reversal_abnormal_return": (
                format(self.reversal_abnormal_return, "f")
                if self.reversal_abnormal_return is not None
                else None
            ),
            "timing_quality": self.timing_quality.value,
            "limit_state": self.limit_state,
            "calendar_id": self.calendar_id,
            "calculation_version": self.calculation_version,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MarketReactionSnapshot":
        reversal = payload.get("reversal_abnormal_return")
        return cls(
            asset_id=str(payload["asset_id"]),
            event_id=str(payload["event_id"]),
            event_first_public_at=str(
                payload["event_first_public_at"]
            ),
            event_trade_date=str(payload["event_trade_date"]),
            observed_through=str(payload["observed_through"]),
            abnormal_return=Decimal(
                str(payload["abnormal_return"])
            ),
            volume_zscore=(
                Decimal(str(payload["volume_zscore"]))
                if payload.get("volume_zscore") is not None
                else None
            ),
            reversal_abnormal_return=(
                Decimal(str(reversal)) if reversal is not None else None
            ),
            timing_quality=EventTimingQuality(
                payload["timing_quality"]
            ),
            limit_state=str(payload["limit_state"]),
            calendar_id=str(payload["calendar_id"]),
            calculation_version=str(
                payload["calculation_version"]
            ),
            source_refs=tuple(payload["source_refs"]),
        )


@dataclass(frozen=True)
class EventNameDiscoverySpec:
    as_of_date: str
    knowledge_cutoff_at: str
    min_absolute_abnormal_return: Decimal = Decimal("0.03")
    min_volume_zscore: Decimal = Decimal("2")
    min_reversal_return: Decimal = Decimal("0.02")
    max_candidates: int = 10
    require_direction_confirmation: bool = True
    confirmation_mode: MarketConfirmationMode = (
        MarketConfirmationMode.PRICE_OR_VOLUME
    )
    retrospective_label: bool = False
    mode: EventNameDiscoveryMode = EventNameDiscoveryMode.CONFIRMATORY
    require_global_scope: bool = False
    require_event_shock: bool = False
    limitations: tuple[str, ...] = (
        "Candidates are research leads, not trade instructions.",
        "External events are manually confirmed in v1.",
        "Confirmed-unrelated requires four evidence checks.",
        "Daily data cannot resolve intraday causal ordering.",
    )

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        knowledge_cutoff = _aware_datetime(
            self.knowledge_cutoff_at,
            "knowledge_cutoff_at",
        )
        if knowledge_cutoff.date().isoformat() > self.as_of_date:
            raise ValueError(
                "knowledge_cutoff_at must not exceed as_of_date"
            )
        for value, name in (
            (
                self.min_absolute_abnormal_return,
                "min_absolute_abnormal_return",
            ),
            (self.min_volume_zscore, "min_volume_zscore"),
            (self.min_reversal_return, "min_reversal_return"),
        ):
            _finite(value, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if not isinstance(self.require_direction_confirmation, bool):
            raise TypeError(
                "require_direction_confirmation must be bool"
            )
        if not isinstance(
            self.confirmation_mode,
            MarketConfirmationMode,
        ):
            raise TypeError(
                "confirmation_mode must be MarketConfirmationMode"
            )
        if not isinstance(self.retrospective_label, bool):
            raise TypeError("retrospective_label must be bool")
        if not isinstance(self.mode, EventNameDiscoveryMode):
            raise TypeError("mode must be EventNameDiscoveryMode")
        if not isinstance(self.require_global_scope, bool):
            raise TypeError("require_global_scope must be bool")
        if not isinstance(self.require_event_shock, bool):
            raise TypeError("require_event_shock must be bool")
        _unique_texts(
            self.limitations,
            "limitations",
            require=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "min_absolute_abnormal_return": format(
                self.min_absolute_abnormal_return,
                "f",
            ),
            "min_volume_zscore": format(
                self.min_volume_zscore,
                "f",
            ),
            "min_reversal_return": format(
                self.min_reversal_return,
                "f",
            ),
            "max_candidates": self.max_candidates,
            "require_direction_confirmation": (
                self.require_direction_confirmation
            ),
            "confirmation_mode": self.confirmation_mode.value,
            "retrospective_label": self.retrospective_label,
            "mode": self.mode.value,
            "require_global_scope": self.require_global_scope,
            "require_event_shock": self.require_event_shock,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EventNameDiscoverySpec":
        return cls(
            as_of_date=str(payload["as_of_date"]),
            knowledge_cutoff_at=str(payload["knowledge_cutoff_at"]),
            min_absolute_abnormal_return=Decimal(
                str(payload["min_absolute_abnormal_return"])
            ),
            min_volume_zscore=Decimal(
                str(payload["min_volume_zscore"])
            ),
            min_reversal_return=Decimal(
                str(payload["min_reversal_return"])
            ),
            max_candidates=int(payload["max_candidates"]),
            require_direction_confirmation=bool(
                payload["require_direction_confirmation"]
            ),
            confirmation_mode=MarketConfirmationMode(
                payload.get(
                    "confirmation_mode",
                    MarketConfirmationMode.PRICE_OR_VOLUME.value,
                )
            ),
            retrospective_label=bool(
                payload.get("retrospective_label", False)
            ),
            mode=EventNameDiscoveryMode(
                payload.get(
                    "mode",
                    EventNameDiscoveryMode.CONFIRMATORY.value,
                )
            ),
            require_global_scope=bool(
                payload.get("require_global_scope", False)
            ),
            require_event_shock=bool(
                payload.get("require_event_shock", False)
            ),
            limitations=tuple(payload["limitations"]),
        )


@dataclass(frozen=True)
class EventNameMemberOutcome:
    asset_id: str
    standard_symbol: str
    stock_name: str
    status: EventNameMemberStatus
    reasons: tuple[str, ...]
    name_matches: tuple[NameMatchEvidence, ...] = ()
    relation: BusinessRelationAssessment | None = None
    catalyst: AlternativeCatalystAssessment | None = None
    consensus: ConsensusFormationAssessment | None = None
    attribution: AttentionAttributionAssessment | None = None
    reaction: MarketReactionSnapshot | None = None
    matched_tracks: tuple[str, ...] = ()
    selection_rank: int | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.standard_symbol, "standard_symbol"),
            (self.stock_name, "stock_name"),
        ):
            _text(value, name)
        _unique_texts(self.reasons, "reasons", require=True)
        _unique_texts(self.matched_tracks, "matched_tracks")
        if self.selection_rank is not None and self.selection_rank < 1:
            raise ValueError("selection_rank must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "standard_symbol": self.standard_symbol,
            "stock_name": self.stock_name,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "name_matches": [
                item.to_dict() for item in self.name_matches
            ],
            "relation": (
                self.relation.to_dict()
                if self.relation is not None
                else None
            ),
            "catalyst": (
                self.catalyst.to_dict()
                if self.catalyst is not None
                else None
            ),
            "consensus": (
                self.consensus.to_dict()
                if self.consensus is not None
                else None
            ),
            "attribution": (
                self.attribution.to_dict()
                if self.attribution is not None
                else None
            ),
            "reaction": (
                self.reaction.to_dict()
                if self.reaction is not None
                else None
            ),
            "matched_tracks": list(self.matched_tracks),
            "selection_rank": self.selection_rank,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EventNameMemberOutcome":
        relation = payload.get("relation")
        catalyst = payload.get("catalyst")
        consensus = payload.get("consensus")
        attribution = payload.get("attribution")
        reaction = payload.get("reaction")
        return cls(
            asset_id=str(payload["asset_id"]),
            standard_symbol=str(payload["standard_symbol"]),
            stock_name=str(payload["stock_name"]),
            status=EventNameMemberStatus(payload["status"]),
            reasons=tuple(payload["reasons"]),
            name_matches=tuple(
                NameMatchEvidence.from_dict(item)
                for item in payload["name_matches"]
            ),
            relation=(
                BusinessRelationAssessment.from_dict(relation)
                if relation is not None
                else None
            ),
            catalyst=(
                AlternativeCatalystAssessment.from_dict(catalyst)
                if catalyst is not None
                else None
            ),
            consensus=(
                ConsensusFormationAssessment.from_dict(consensus)
                if consensus is not None
                else None
            ),
            attribution=(
                AttentionAttributionAssessment.from_dict(attribution)
                if attribution is not None
                else None
            ),
            reaction=(
                MarketReactionSnapshot.from_dict(reaction)
                if reaction is not None
                else None
            ),
            matched_tracks=tuple(payload["matched_tracks"]),
            selection_rank=payload.get("selection_rank"),
        )


@dataclass(frozen=True)
class EventNameCandidate:
    asset_id: str
    standard_symbol: str
    stock_name: str
    selected_track: str
    rank: int
    name_match: NameMatchEvidence
    relation: BusinessRelationAssessment
    catalyst: AlternativeCatalystAssessment
    consensus: ConsensusFormationAssessment
    attribution: AttentionAttributionAssessment | None
    reaction: MarketReactionSnapshot | None
    research_task_draft: ResearchTaskSpec

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("candidate rank must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "standard_symbol": self.standard_symbol,
            "stock_name": self.stock_name,
            "selected_track": self.selected_track,
            "rank": self.rank,
            "name_match": self.name_match.to_dict(),
            "relation": self.relation.to_dict(),
            "catalyst": self.catalyst.to_dict(),
            "consensus": self.consensus.to_dict(),
            "attribution": (
                self.attribution.to_dict()
                if self.attribution is not None
                else None
            ),
            "reaction": (
                self.reaction.to_dict()
                if self.reaction is not None
                else None
            ),
            "research_task_draft": self.research_task_draft.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EventNameCandidate":
        return cls(
            asset_id=str(payload["asset_id"]),
            standard_symbol=str(payload["standard_symbol"]),
            stock_name=str(payload["stock_name"]),
            selected_track=str(payload["selected_track"]),
            rank=int(payload["rank"]),
            name_match=NameMatchEvidence.from_dict(
                payload["name_match"]
            ),
            relation=BusinessRelationAssessment.from_dict(
                payload["relation"]
            ),
            catalyst=AlternativeCatalystAssessment.from_dict(
                payload["catalyst"]
            ),
            consensus=ConsensusFormationAssessment.from_dict(
                payload["consensus"]
            ),
            attribution=(
                AttentionAttributionAssessment.from_dict(
                    payload["attribution"]
                )
                if payload.get("attribution") is not None
                else None
            ),
            reaction=(
                MarketReactionSnapshot.from_dict(payload["reaction"])
                if payload.get("reaction") is not None
                else None
            ),
            research_task_draft=ResearchTaskSpec.from_dict(
                payload["research_task_draft"]
            ),
        )


@dataclass(frozen=True)
class EventNameDiscoveryRun:
    run_id: str
    event: ExternalEventAnchor
    spec: EventNameDiscoverySpec
    started_at: str
    completed_at: str
    outcomes: tuple[EventNameMemberOutcome, ...]
    candidates: tuple[EventNameCandidate, ...]
    is_buy_recommendation: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _aware_datetime(self.started_at, "started_at")
        _aware_datetime(self.completed_at, "completed_at")
        if not self.outcomes:
            raise ValueError("discovery run requires outcomes")
        outcome_ids = [item.asset_id for item in self.outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("run outcomes must have unique asset IDs")
        candidate_ids = {item.asset_id for item in self.candidates}
        expected_ids = {
            item.asset_id
            for item in self.outcomes
            if item.status is EventNameMemberStatus.CANDIDATE
        }
        if candidate_ids != expected_ids:
            raise ValueError(
                "candidate list must match candidate outcomes"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "event": self.event.to_dict(),
            "spec": self.spec.to_dict(),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "candidates": [item.to_dict() for item in self.candidates],
            "is_buy_recommendation": False,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EventNameDiscoveryRun":
        return cls(
            run_id=str(payload["run_id"]),
            event=ExternalEventAnchor.from_dict(payload["event"]),
            spec=EventNameDiscoverySpec.from_dict(payload["spec"]),
            started_at=str(payload["started_at"]),
            completed_at=str(payload["completed_at"]),
            outcomes=tuple(
                EventNameMemberOutcome.from_dict(item)
                for item in payload["outcomes"]
            ),
            candidates=tuple(
                EventNameCandidate.from_dict(item)
                for item in payload["candidates"]
            ),
        )


def _relation_status(
    evidence: tuple[BusinessRelationEvidence, ...],
) -> BusinessRelationStatus:
    if any(
        item.result is RelationCheckResult.PRESENT for item in evidence
    ):
        return BusinessRelationStatus.RELATED
    by_type = {item.check_type: item.result for item in evidence}
    if all(
        by_type.get(check_type) is RelationCheckResult.ABSENT
        for check_type in REQUIRED_RELATION_CHECKS
    ):
        return BusinessRelationStatus.CONFIRMED_UNRELATED
    return BusinessRelationStatus.INSUFFICIENT_EVIDENCE


def _catalyst_status(
    evidence: tuple[AlternativeCatalystEvidence, ...],
    *,
    coverage_complete: bool,
) -> AlternativeCatalystStatus:
    if any(
        item.result is RelationCheckResult.PRESENT for item in evidence
    ):
        return AlternativeCatalystStatus.CONTAMINATED
    if not coverage_complete:
        return AlternativeCatalystStatus.INSUFFICIENT_EVIDENCE
    by_type = {item.check_type: item.result for item in evidence}
    if all(
        by_type.get(check_type) is RelationCheckResult.ABSENT
        for check_type in REQUIRED_CATALYST_CHECKS
    ):
        return AlternativeCatalystStatus.CLEAR
    return AlternativeCatalystStatus.INSUFFICIENT_EVIDENCE


def _event_shock_status(
    evidence: tuple[EventShockEvidence, ...],
    *,
    coverage_complete: bool,
) -> EventShockStatus:
    if not coverage_complete:
        return EventShockStatus.INSUFFICIENT_EVIDENCE
    by_dimension = {
        item.dimension: item.result for item in evidence
    }
    if any(
        result is RelationCheckResult.ABSENT
        for result in by_dimension.values()
    ):
        return EventShockStatus.REJECTED
    if all(
        by_dimension.get(dimension) is RelationCheckResult.PRESENT
        for dimension in EventShockDimension
    ):
        return EventShockStatus.QUALIFIED
    return EventShockStatus.INSUFFICIENT_EVIDENCE


def _consensus_status(
    evidence: tuple[ConsensusConditionEvidence, ...],
    *,
    coverage_complete: bool,
) -> ConsensusFormationStatus:
    if not coverage_complete:
        return ConsensusFormationStatus.INSUFFICIENT_EVIDENCE
    by_dimension = {
        item.dimension: item.result for item in evidence
    }
    core_dimensions = (
        ConsensusDimension.EVENT_SALIENCE,
        ConsensusDimension.GLOBAL_REACH,
        ConsensusDimension.CHINA_ATTENTION,
        ConsensusDimension.NARRATIVE_DISTANCE,
        ConsensusDimension.NARRATIVE_SIMPLICITY,
        ConsensusDimension.COORDINATION_FOCAL_POINT,
    )
    if not all(
        by_dimension.get(dimension) is RelationCheckResult.PRESENT
        for dimension in core_dimensions
    ):
        return ConsensusFormationStatus.INSUFFICIENT_EVIDENCE
    supporting_dimensions = (
        ConsensusDimension.PRICE_ELASTICITY,
        ConsensusDimension.PROPAGATION,
        ConsensusDimension.ARBITRAGE_CONSTRAINTS,
        ConsensusDimension.RISK_APPETITE,
    )
    supporting_count = sum(
        by_dimension.get(dimension) is RelationCheckResult.PRESENT
        for dimension in supporting_dimensions
    )
    return (
        ConsensusFormationStatus.CONFIRMED
        if supporting_count >= 2
        else ConsensusFormationStatus.FORMING
    )


def _name_association_conclusion(
    observations: tuple[AttentionOutcomeObservation, ...],
) -> NameAssociationConclusion:
    present = {
        item.layer
        for item in observations
        if item.result is RelationCheckResult.PRESENT
    }
    if present & {
        AttentionOutcomeLayer.REALIZED_SALES,
        AttentionOutcomeLayer.SECURITY_PRICE,
    }:
        return NameAssociationConclusion.ECONOMIC_EFFECT_CONFIRMED
    if AttentionOutcomeLayer.PURCHASE_INTENT in present:
        return NameAssociationConclusion.BEHAVIOR_CONFIRMED
    if present & {
        AttentionOutcomeLayer.SEARCH_ATTENTION,
        AttentionOutcomeLayer.SENTIMENT,
    }:
        return NameAssociationConclusion.ATTENTION_ONLY
    if any(
        item.result is RelationCheckResult.ABSENT
        for item in observations
    ):
        return NameAssociationConclusion.COUNTEREVIDENCE
    return NameAssociationConclusion.INSUFFICIENT_EVIDENCE


def _aware_datetime(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _unique_texts(
    values: tuple[str, ...],
    name: str,
    *,
    require: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if require and not values:
        raise ValueError(f"{name} must not be empty")
    for value in values:
        _text(value, name)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")


def _finite(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _optional_decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
