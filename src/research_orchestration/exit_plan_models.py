"""Exit plan for a held stock: sell-side conditions by dimension, plus behavioral hints.

Design notes: docs/modules/6-编排/研究编排模块.md, “退出计划与行为偏差提示”.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from src.research_contracts import from_payload, to_payload


class ExitDimension(str, Enum):
    THESIS = "thesis"
    VALUATION = "valuation"
    RISK_BUDGET = "risk_budget"
    OPPORTUNITY_COST = "opportunity_cost"
    TIME_STOP = "time_stop"


class ExitState(str, Enum):
    TRIGGERED = "triggered"
    ARMED = "armed"
    CLEAR = "clear"
    INSUFFICIENT = "insufficient"


class ExitAction(str, Enum):
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    SWITCH = "switch"
    INSUFFICIENT = "insufficient"


SELL_ACTIONS = frozenset({ExitAction.REDUCE, ExitAction.EXIT, ExitAction.SWITCH})


@dataclass(frozen=True)
class ExitCondition:
    """One sell-side condition; ``observability_key`` is machine-checkable when present."""

    condition_id: str
    dimension: ExitDimension
    state: ExitState
    action: ExitAction
    summary: str
    trigger_text: str | None = None
    observability_key: str | None = None
    valid_until: str | None = None
    alternative_asset_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BehavioralHint:
    code: str
    severity: str
    title: str
    evidence: str
    guidance: str


@dataclass(frozen=True)
class HoldingFacts:
    quantity: Decimal
    price: Decimal
    price_date: str
    average_cost: Decimal | None = None
    unrealized_return: Decimal | None = None
    weight: Decimal | None = None
    weight_as_of: str | None = None
    first_bought_on: str | None = None


@dataclass(frozen=True)
class ExitPlan:
    plan_id: str
    user_id: str
    asset_id: str
    asset_name: str
    as_of_date: str
    holding: HoldingFacts
    conditions: tuple[ExitCondition, ...]
    behavioral_hints: tuple[BehavioralHint, ...]
    gaps: tuple[str, ...] = ()
    intended_action: str | None = None

    @property
    def has_exit_condition(self) -> bool:
        """A hold conclusion is reviewable only with a sell-side condition that is set or met."""

        return any(
            item.action in SELL_ACTIONS and item.state in (ExitState.TRIGGERED, ExitState.ARMED)
            for item in self.conditions
        )

    @property
    def triggered(self) -> tuple[ExitCondition, ...]:
        return tuple(item for item in self.conditions if item.state is ExitState.TRIGGERED)

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExitPlan":
        return from_payload(cls, payload)


DIMENSION_LABELS = {
    ExitDimension.THESIS: "论点",
    ExitDimension.VALUATION: "估值透支",
    ExitDimension.RISK_BUDGET: "风险预算",
    ExitDimension.OPPORTUNITY_COST: "机会成本",
    ExitDimension.TIME_STOP: "时间止损",
}
STATE_LABELS = {
    ExitState.TRIGGERED: "已触发",
    ExitState.ARMED: "已设定",
    ExitState.CLEAR: "未触发",
    ExitState.INSUFFICIENT: "证据不足",
}
ACTION_LABELS = {
    ExitAction.HOLD: "继续持有",
    ExitAction.REDUCE: "分批减持",
    ExitAction.EXIT: "退出",
    ExitAction.SWITCH: "换仓",
    ExitAction.INSUFFICIENT: "证据不足",
}


def exit_plan_lines(plan: ExitPlan) -> list[str]:
    """Readable condition lines; conflicting actions stay side by side."""

    lines = []
    for item in plan.conditions:
        text = (
            f"{DIMENSION_LABELS[item.dimension]}｜{STATE_LABELS[item.state]}｜"
            f"{ACTION_LABELS[item.action]}：{item.summary}"
        )
        if item.trigger_text and item.state is ExitState.ARMED:
            text += f"（触发条件：{item.trigger_text}）"
        if item.notes:
            text += "。说明：" + "；".join(item.notes)
        lines.append(text)
    return lines


SEVERITY_LABELS = {"warning": "警示", "caution": "提醒", "info": "信息"}
