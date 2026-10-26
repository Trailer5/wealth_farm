"""Serializable, horizon-aware strategy contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, TypeAlias

from src.research_contracts import ResearchHorizon


class StrategyMaturity(str, Enum):
    RESEARCH_RULE = "research_rule"
    VALIDATED_STRATEGY = "validated_strategy"


class ConditionOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    BETWEEN = "between"


class ConditionLogic(str, Enum):
    AND = "and"
    OR = "or"


class ConditionOutcome(str, Enum):
    TRUE = "true"
    FALSE = "false"
    MISSING = "missing"


class StrategyRunStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    VETOED = "vetoed"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class StrategyMetricInput:
    metric_name: str
    value: Decimal | None
    as_of_date: str
    calculation_version: str
    applicable: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if not self.metric_name.strip():
            raise ValueError("metric_name must not be blank")
        if not self.calculation_version.strip():
            raise ValueError(
                "calculation_version must not be blank"
            )
        if self.applicable and self.value is None:
            raise ValueError(
                "Applicable metric input requires a value"
            )


@dataclass(frozen=True)
class StrategyInput:
    asset_id: str
    profile_id: str
    horizon: ResearchHorizon
    as_of_date: str
    metrics: dict[str, StrategyMetricInput]
    evidence_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if not self.asset_id.strip() or not self.profile_id.strip():
            raise ValueError(
                "asset_id and profile_id must not be blank"
            )
        if not isinstance(self.horizon, ResearchHorizon):
            raise TypeError("horizon must be ResearchHorizon")
        for name, metric in self.metrics.items():
            if name != metric.metric_name:
                raise ValueError(
                    "Metric dictionary key must equal metric_name"
                )
            if metric.as_of_date > self.as_of_date:
                raise ValueError(
                    f"Metric {name} exceeds strategy as_of_date"
                )


@dataclass(frozen=True)
class MetricCondition:
    condition_id: str
    metric_name: str
    operator: ConditionOperator
    threshold: Decimal
    description: str
    upper_threshold: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.condition_id.strip():
            raise ValueError("condition_id must not be blank")
        if not self.metric_name.strip():
            raise ValueError("metric_name must not be blank")
        if not self.description.strip():
            raise ValueError("description must not be blank")
        if self.operator is ConditionOperator.BETWEEN:
            if self.upper_threshold is None:
                raise ValueError(
                    "between condition requires upper_threshold"
                )
            if self.upper_threshold < self.threshold:
                raise ValueError(
                    "upper_threshold must not be below threshold"
                )
        elif self.upper_threshold is not None:
            raise ValueError(
                "upper_threshold is only valid for between"
            )


@dataclass(frozen=True)
class ConditionGroup:
    condition_id: str
    logic: ConditionLogic
    children: tuple["ConditionNode", ...]
    description: str

    def __post_init__(self) -> None:
        if not self.condition_id.strip():
            raise ValueError("condition_id must not be blank")
        if not self.description.strip():
            raise ValueError("description must not be blank")
        if not self.children:
            raise ValueError(
                "Condition group children must not be empty"
            )


ConditionNode: TypeAlias = MetricCondition | ConditionGroup


@dataclass(frozen=True)
class WeightedCondition:
    condition: ConditionNode
    weight: Decimal

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("weight must be positive")


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    version: str
    maturity: StrategyMaturity
    supported_profiles: tuple[str, ...]
    horizons: tuple[ResearchHorizon, ...]
    required_metrics: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    data_window_days: int
    valid_for_days: int
    hard_filters: tuple[ConditionNode, ...] = ()
    vetoes: tuple[ConditionNode, ...] = ()
    score_rules: tuple[WeightedCondition, ...] = ()
    passing_score: Decimal = Decimal("0.5")
    signal_on_pass: str = "research_pass"
    signal_on_fail: str = "research_fail"
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.strategy_id, "strategy_id"),
            (self.name, "name"),
            (self.version, "version"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if not self.supported_profiles:
            raise ValueError(
                "supported_profiles must not be empty"
            )
        if not self.horizons:
            raise ValueError("horizons must not be empty")
        if self.data_window_days < 1:
            raise ValueError("data_window_days must be positive")
        if self.valid_for_days < 1:
            raise ValueError("valid_for_days must be positive")
        if not Decimal("0") <= self.passing_score <= Decimal("1"):
            raise ValueError(
                "passing_score must be between 0 and 1"
            )
        if (
            self.maturity is StrategyMaturity.VALIDATED_STRATEGY
            and not self.limitations
        ):
            raise ValueError(
                "Validated strategy requires validation limitations"
            )
        nodes = (
            self.hard_filters
            + self.vetoes
            + tuple(item.condition for item in self.score_rules)
        )
        condition_ids = [
            condition_id
            for node in nodes
            for condition_id in _condition_ids(node)
        ]
        if len(set(condition_ids)) != len(condition_ids):
            raise ValueError("condition_id must be unique")
        used_metrics = {
            metric_name
            for node in nodes
            for metric_name in _condition_metrics(node)
        }
        missing_declarations = used_metrics - set(
            self.required_metrics
        )
        if missing_declarations:
            raise ValueError(
                "Conditions use undeclared required_metrics: "
                f"{sorted(missing_declarations)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "version": self.version,
            "maturity": self.maturity.value,
            "supported_profiles": list(self.supported_profiles),
            "horizons": [
                horizon.value for horizon in self.horizons
            ],
            "required_metrics": list(self.required_metrics),
            "required_evidence_types": list(
                self.required_evidence_types
            ),
            "data_window_days": self.data_window_days,
            "valid_for_days": self.valid_for_days,
            "hard_filters": [
                condition_to_dict(node)
                for node in self.hard_filters
            ],
            "vetoes": [
                condition_to_dict(node)
                for node in self.vetoes
            ],
            "score_rules": [
                {
                    "condition": condition_to_dict(item.condition),
                    "weight": format(item.weight, "f"),
                }
                for item in self.score_rules
            ],
            "passing_score": format(self.passing_score, "f"),
            "signal_on_pass": self.signal_on_pass,
            "signal_on_fail": self.signal_on_fail,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategySpec":
        return cls(
            strategy_id=str(payload["strategy_id"]),
            name=str(payload["name"]),
            version=str(payload["version"]),
            maturity=StrategyMaturity(payload["maturity"]),
            supported_profiles=tuple(
                str(value)
                for value in payload["supported_profiles"]
            ),
            horizons=tuple(
                ResearchHorizon(value)
                for value in payload["horizons"]
            ),
            required_metrics=tuple(
                str(value)
                for value in payload["required_metrics"]
            ),
            required_evidence_types=tuple(
                str(value)
                for value in payload["required_evidence_types"]
            ),
            data_window_days=int(payload["data_window_days"]),
            valid_for_days=int(payload["valid_for_days"]),
            hard_filters=tuple(
                condition_from_dict(value)
                for value in payload.get("hard_filters", ())
            ),
            vetoes=tuple(
                condition_from_dict(value)
                for value in payload.get("vetoes", ())
            ),
            score_rules=tuple(
                WeightedCondition(
                    condition=condition_from_dict(
                        value["condition"]
                    ),
                    weight=Decimal(str(value["weight"])),
                )
                for value in payload.get("score_rules", ())
            ),
            passing_score=Decimal(
                str(payload["passing_score"])
            ),
            signal_on_pass=str(payload["signal_on_pass"]),
            signal_on_fail=str(payload["signal_on_fail"]),
            limitations=tuple(
                str(value)
                for value in payload.get("limitations", ())
            ),
        )


@dataclass(frozen=True)
class StrategyResult:
    strategy_id: str
    strategy_version: str
    maturity: StrategyMaturity
    asset_id: str
    horizon: ResearchHorizon
    as_of_date: str
    valid_until: str
    status: StrategyRunStatus
    signal: str
    score: Decimal | None
    confidence: Decimal | None
    input_completeness: Decimal
    matched_conditions: tuple[str, ...]
    failed_conditions: tuple[str, ...]
    vetoed_conditions: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    missing_evidence_types: tuple[str, ...]
    metric_versions: dict[str, str]
    counterevidence: tuple[str, ...]
    limitations: tuple[str, ...]
    input_effective_dates: dict[str, str] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "maturity": self.maturity.value,
            "asset_id": self.asset_id,
            "horizon": self.horizon.value,
            "as_of_date": self.as_of_date,
            "valid_until": self.valid_until,
            "status": self.status.value,
            "signal": self.signal,
            "score": (
                format(self.score, "f")
                if self.score is not None
                else None
            ),
            "confidence": (
                format(self.confidence, "f")
                if self.confidence is not None
                else None
            ),
            "input_completeness": format(
                self.input_completeness,
                "f",
            ),
            "matched_conditions": list(self.matched_conditions),
            "failed_conditions": list(self.failed_conditions),
            "vetoed_conditions": list(
                self.vetoed_conditions
            ),
            "missing_metrics": list(self.missing_metrics),
            "missing_evidence_types": list(
                self.missing_evidence_types
            ),
            "metric_versions": self.metric_versions,
            "input_effective_dates": self.input_effective_dates,
            "counterevidence": list(self.counterevidence),
            "limitations": list(self.limitations),
        }


def condition_to_dict(node: ConditionNode) -> dict[str, Any]:
    if isinstance(node, MetricCondition):
        return {
            "type": "metric",
            "condition_id": node.condition_id,
            "metric_name": node.metric_name,
            "operator": node.operator.value,
            "threshold": format(node.threshold, "f"),
            "upper_threshold": (
                format(node.upper_threshold, "f")
                if node.upper_threshold is not None
                else None
            ),
            "description": node.description,
        }
    return {
        "type": "group",
        "condition_id": node.condition_id,
        "logic": node.logic.value,
        "description": node.description,
        "children": [
            condition_to_dict(child)
            for child in node.children
        ],
    }


def condition_from_dict(payload: dict[str, Any]) -> ConditionNode:
    if payload["type"] == "metric":
        return MetricCondition(
            condition_id=str(payload["condition_id"]),
            metric_name=str(payload["metric_name"]),
            operator=ConditionOperator(payload["operator"]),
            threshold=Decimal(str(payload["threshold"])),
            upper_threshold=(
                Decimal(str(payload["upper_threshold"]))
                if payload.get("upper_threshold") is not None
                else None
            ),
            description=str(payload["description"]),
        )
    if payload["type"] == "group":
        return ConditionGroup(
            condition_id=str(payload["condition_id"]),
            logic=ConditionLogic(payload["logic"]),
            description=str(payload["description"]),
            children=tuple(
                condition_from_dict(child)
                for child in payload["children"]
            ),
        )
    raise ValueError(
        f"Unknown condition type: {payload['type']}"
    )


def _condition_ids(node: ConditionNode) -> tuple[str, ...]:
    if isinstance(node, MetricCondition):
        return (node.condition_id,)
    return (
        node.condition_id,
        *(
            condition_id
            for child in node.children
            for condition_id in _condition_ids(child)
        ),
    )


def _condition_metrics(node: ConditionNode) -> tuple[str, ...]:
    if isinstance(node, MetricCondition):
        return (node.metric_name,)
    return tuple(
        metric_name
        for child in node.children
        for metric_name in _condition_metrics(child)
    )
