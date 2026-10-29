"""First-batch screening strategies on cross-sectional features (decision 8)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .features import MemberFeatures

STRATEGY_VERSION = "funnel_strategies_v2"


@dataclass(frozen=True)
class ScopeContext:
    median_pe: float | None
    median_pe_percentile: float | None


@dataclass(frozen=True)
class Condition:
    label: str
    value: Callable[[MemberFeatures, ScopeContext], float | None]
    test: Callable[[float, ScopeContext], bool]
    describe: Callable[[float, ScopeContext], str]


@dataclass(frozen=True)
class StrategyResult:
    strategy_id: str
    passed: bool
    reasons: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    missing: tuple[str, ...] = ()
    counter_evidence: tuple[str, ...] = ()
    sort_key: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "failed": [text for _, text in self.failed],
            "missing": list(self.missing),
            "counter_evidence": list(self.counter_evidence),
        }


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    name: str
    description: str
    needs_history: bool
    conditions: tuple[Condition, ...]
    sort_key: Callable[[MemberFeatures, ScopeContext], float]
    counters: Callable[[MemberFeatures, ScopeContext], list[str]] = field(default=lambda f, c: [])

    def evaluate(self, features: MemberFeatures, context: ScopeContext) -> StrategyResult:
        reasons, failed, missing = [], [], []
        for condition in self.conditions:
            value = condition.value(features, context)
            if value is None:
                missing.append(condition.label)
            elif condition.test(value, context):
                reasons.append(condition.describe(value, context))
            else:
                failed.append((condition.label, condition.describe(value, context)))
        passed = not failed and not missing
        return StrategyResult(
            strategy_id=self.strategy_id,
            passed=passed,
            reasons=tuple(reasons),
            failed=tuple(failed),
            missing=tuple(missing),
            counter_evidence=tuple(self.counters(features, context)) if passed else (),
            sort_key=self.sort_key(features, context) if passed else None,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "needs_history": self.needs_history,
            "conditions": [item.label for item in self.conditions],
        }


def _field(name: str) -> Callable[[MemberFeatures, ScopeContext], float | None]:
    return lambda features, context: getattr(features, name)


def _roe(threshold: float) -> Condition:
    return Condition(
        f"ROE(TTM) ≥ {threshold:.0f}%",
        _field("roe"),
        lambda v, c: v >= threshold,
        lambda v, c: f"ROE(TTM) {v:.1f}%（门槛 {threshold:.0f}%）",
    )


def _common_counters(features: MemberFeatures, context: ScopeContext) -> list[str]:
    notes = []
    if features.net_profit_yoy is not None and features.net_profit_yoy < 0:
        notes.append(f"累计净利润同比 {features.net_profit_yoy:+.1f}%")
    if features.net_profit_yoy is not None and 100 < features.net_profit_yoy <= 300:
        notes.append(f"累计净利润同比 {features.net_profit_yoy:+.0f}%：低基数或一次性损益，需核对扣非利润")
    if features.net_profit_quarter_yoy is not None and features.net_profit_quarter_yoy < 0:
        notes.append(f"最近单季净利润同比 {features.net_profit_quarter_yoy:+.1f}%")
    if features.revenue_yoy is not None and features.revenue_yoy < 0:
        notes.append(f"累计营收同比 {features.revenue_yoy:+.1f}%")
    if features.operating_cash_flow is not None and features.operating_cash_flow <= 0:
        notes.append("经营现金流（TTM）为负")
    if features.debt_ratio is not None and features.debt_ratio > 60:
        notes.append(f"资产负债率 {features.debt_ratio:.0f}%")
    return notes


def _pe_vs_scope(features: MemberFeatures, context: ScopeContext) -> list[str]:
    if features.pe_ttm is None or context.median_pe is None:
        return []
    if features.pe_ttm <= 0:
        return ["PE(TTM) 为负：最近四个季度亏损"]
    if features.pe_ttm > context.median_pe:
        return [f"PE(TTM) {features.pe_ttm:.1f} 倍高于范围中位数 {context.median_pe:.1f} 倍"]
    return []


QUALITY_VALUE = Strategy(
    "quality_value",
    "质量—估值",
    "盈利能力达标、负债与现金流健康、估值不高于范围中位数；按“ROE(TTM) ÷ PE”排序",
    False,
    (
        _roe(10),
        Condition(
            "资产负债率 ≤ 70%",
            _field("debt_ratio"),
            lambda v, c: v <= 70,
            lambda v, c: f"资产负债率 {v:.0f}%（上限 70%）",
        ),
        Condition(
            "经营现金流（TTM）为正",
            _field("operating_cash_flow"),
            lambda v, c: v > 0,
            lambda v, c: "经营现金流（TTM）为正" if v > 0 else "经营现金流（TTM）为负",
        ),
        Condition(
            "PE 不高于范围中位数",
            lambda f, c: f.pe_ttm if c.median_pe is not None else None,
            lambda v, c: 0 < v <= c.median_pe,  # type: ignore[operator]
            lambda v, c: f"PE(TTM) {v:.1f} 倍（范围中位数 {c.median_pe:.1f} 倍）",
        ),
    ),
    lambda f, c: (f.roe or 0) / (f.pe_ttm or 1),
    _common_counters,
)

QUALITY_DRAWDOWN = Strategy(
    "quality_drawdown",
    "高质量深度回撤",
    "盈利能力高且利润没有大幅下滑，但总市值较近三年高点回撤 30% 以上；按回撤深度与 ROE 排序",
    True,
    (
        _roe(12),
        Condition(
            "累计净利润同比 ≥ -10%",
            _field("net_profit_yoy"),
            lambda v, c: v >= -10,
            lambda v, c: f"累计净利润同比 {v:+.1f}%（下限 -10%）",
        ),
        Condition(
            "较近三年高点回撤 ≥ 30%",
            _field("drawdown_3y"),
            lambda v, c: v <= -0.30,
            lambda v, c: f"总市值较近三年高点 {v:+.0%}",
        ),
    ),
    lambda f, c: -(f.drawdown_3y or 0) * min(f.roe or 0, 30) / 30,
    lambda f, c: [*_common_counters(f, c), "深度回撤常有原因：先核对行业与公司事件"],
)


def _roe_change(features: MemberFeatures, context: ScopeContext) -> float | None:
    if features.roe is None or features.roe_prev_year is None:
        return None
    return features.roe - features.roe_prev_year


def _valuation_low_counters(features: MemberFeatures, context: ScopeContext) -> list[str]:
    notes = _common_counters(features, context)
    if context.median_pe_percentile is not None and context.median_pe_percentile <= 0.3:
        notes.append(
            f"范围内估值分位中位数 {context.median_pe_percentile:.0%}：低位可能是整体估值下移"
        )
    return notes


VALUATION_LOW = Strategy(
    "valuation_low",
    "估值自身历史低位且质量未恶化",
    "PE(TTM) 处于自身 2018 年以来月末分位 20% 以内，ROE(TTM) 不低于 5% 且较一年前下降不超过 3 个百分点；按分位排序",
    True,
    (
        Condition(
            "PE 自身分位 ≤ 20%",
            _field("pe_percentile"),
            lambda v, c: v <= 0.20,
            lambda v, c: f"PE(TTM) 自身历史分位 {v:.0%}",
        ),
        _roe(5),
        Condition(
            "ROE 较一年前降幅 ≤ 3 个百分点",
            _roe_change,
            lambda v, c: v >= -3,
            lambda v, c: f"ROE(TTM) 较一年前 {v:+.1f} 个百分点",
        ),
        Condition(
            "累计净利润同比 ≥ -20%",
            _field("net_profit_yoy"),
            lambda v, c: v >= -20,
            lambda v, c: f"累计净利润同比 {v:+.1f}%（下限 -20%）",
        ),
    ),
    lambda f, c: 1 - (f.pe_percentile or 0),
    _valuation_low_counters,
)


def _inflection_counters(features: MemberFeatures, context: ScopeContext) -> list[str]:
    notes = _common_counters(features, context) + _pe_vs_scope(features, context)
    if features.net_profit_quarter_yoy is not None and features.net_profit_quarter_yoy > 100:
        notes.append(f"单季净利润同比 {features.net_profit_quarter_yoy:+.0f}%：去年同季基数低")
    return notes


EARNINGS_INFLECTION = Strategy(
    "earnings_inflection",
    "盈利拐点",
    "单季净利润同比由上一季度的不增长转为增长，单季营收同比为正且单季盈利；按单季增速改善幅度排序（去年同季亏损的不计算）",
    False,
    (
        Condition(
            "单季盈利",
            _field("net_profit_quarter"),
            lambda v, c: v > 0,
            lambda v, c: "单季净利润为正" if v > 0 else "单季亏损",
        ),
        Condition(
            "单季净利润同比 > 0",
            _field("net_profit_quarter_yoy"),
            lambda v, c: v > 0,
            lambda v, c: f"单季净利润同比 {v:+.1f}%",
        ),
        Condition(
            "上一季度单季净利润同比 ≤ 0",
            _field("net_profit_quarter_yoy_prev"),
            lambda v, c: v <= 0,
            lambda v, c: f"上一季度单季净利润同比 {v:+.1f}%",
        ),
        Condition(
            "单季营收同比 > 0",
            _field("revenue_quarter_yoy"),
            lambda v, c: v > 0,
            lambda v, c: f"单季营收同比 {v:+.1f}%",
        ),
    ),
    lambda f, c: min((f.net_profit_quarter_yoy or 0) - (f.net_profit_quarter_yoy_prev or 0), 200.0),
    _inflection_counters,
)

STRATEGIES: tuple[Strategy, ...] = (
    QUALITY_VALUE,
    QUALITY_DRAWDOWN,
    VALUATION_LOW,
    EARNINGS_INFLECTION,
)


def strategy(strategy_id: str) -> Strategy:
    for item in STRATEGIES:
        if item.strategy_id == strategy_id:
            return item
    raise KeyError(f"Unknown strategy: {strategy_id}")
