"""Forward settlement of ledger entries against later price or metric observations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from src.research_contracts import (
    ActionType,
    ClaimComparison,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
    View,
    disclosure_deadline,
    settlement_checkpoints,
)

from .models import (
    Artifact,
    ClaimResult,
    Settlement,
    SettlementStatus,
    new_settlement,
)

RETURN_QUANTUM = Decimal("0.00000001")
STALE_OBSERVATION_DAYS = 15
# Longer than any exchange holiday; a later first price means a gap in the series.
_MAX_REFERENCE_LAG_DAYS = 15
_BULLISH_ACTIONS = frozenset({ActionType.BUY, ActionType.ADD})
_BEARISH_ACTIONS = frozenset({ActionType.REDUCE, ActionType.EXIT})
# What a metric claim compares with its threshold, measured over the periods
# published after the view: latest level, latest minus the reference, or extremes.
METRIC_CLAIM_KEYS = ("level", "change", "min_level", "max_level")
# "level_at:<period_end>" compares the value of one named period.
LEVEL_AT_PREFIX = "level_at:"


def _level_at_period(metric_key: str) -> str | None:
    if not metric_key.startswith(LEVEL_AT_PREFIX):
        return None
    period = metric_key[len(LEVEL_AT_PREFIX):]
    try:
        date.fromisoformat(period)
    except ValueError:
        return None
    return period


def _report_deadline(metric_id: str, period: str | None) -> str | None:
    """Statutory disclosure deadline of a company report period; None for macro series."""

    if period is None or not metric_id.startswith("fin:"):
        return None
    try:
        return disclosure_deadline(period)
    except ValueError:
        return None


@dataclass(frozen=True)
class PricePoint:
    date: str
    value: Decimal
    # "total_return" or "price"; None when the source does not declare it.
    basis: str | None = None


@dataclass(frozen=True)
class MetricPoint:
    period_end: str
    available_at: str
    value: Decimal


class PriceDataUnavailable(RuntimeError):
    """Prices or metric values could not be obtained; the checkpoint stays retryable."""


class PriceSeriesSource(Protocol):
    def series(
        self,
        asset_id: str,
        start: str,
        end: str,
    ) -> tuple[PricePoint, ...]: ...


class MetricSeriesSource(Protocol):
    def series(
        self,
        metric_id: str,
        start: str,
        end: str,
    ) -> tuple[MetricPoint, ...]: ...


@dataclass(frozen=True)
class TriggerCondition:
    """Parsed ``observability_key``: price_above/price_below:<price> or metric_above/metric_below:<value>:<metric_id>."""

    kind: str
    threshold: Decimal
    observed_id: str

    @property
    def on_price(self) -> bool:
        return self.kind.startswith("price_")

    def met(self, value: Decimal) -> bool:
        return value > self.threshold if self.kind.endswith("_above") else value < self.threshold


_CONDITION_KINDS = ("price_above", "price_below", "metric_above", "metric_below")


def parse_trigger_condition(key: str | None, asset_id: str) -> TriggerCondition | None:
    if not key:
        return None
    kind, _, rest = key.partition(":")
    if kind not in _CONDITION_KINDS:
        return None
    value, _, metric_id = rest.partition(":")
    try:
        threshold = Decimal(value)
    except InvalidOperation:
        return None
    if not threshold.is_finite():
        return None
    if kind.startswith("price_"):
        return None if metric_id else TriggerCondition(kind, threshold, asset_id)
    return TriggerCondition(kind, threshold, metric_id) if metric_id else None


def trigger_date(
    condition: TriggerCondition,
    series: tuple[PricePoint, ...] | tuple[MetricPoint, ...],
    valid_from: str,
    valid_until: str,
) -> str | None:
    """First date after ``valid_from`` on which the condition is observed: a close, or a report's publication."""

    observed = sorted(
        (
            (item.date, item.value)
            if condition.on_price
            else (item.available_at, item.value)
            for item in series
        ),
    )
    return next(
        (day for day, value in observed if valid_from < day <= valid_until and condition.met(value)),
        None,
    )


@dataclass(frozen=True)
class SettlementSchedule:
    start: str
    checkpoints: tuple[str, ...]
    rule: SettlementRule | None
    unsettleable_reason: str | None = None
    # Set for conditional actions with a machine-checkable trigger; checkpoints follow the trigger date.
    condition: TriggerCondition | None = None
    valid_until: str | None = None


def settlement_schedule(artifact: Artifact) -> SettlementSchedule | None:
    """When and how an artifact is settled; None if it is never settled."""

    if isinstance(artifact, View):
        reason = None
        rule = artifact.settlement_rule
        if rule is None:
            reason = "观点没有结算规则，只作记录"
        elif rule.method is SettlementMethod.METRIC_OBSERVATION:
            if artifact.claim is not None and (
                artifact.claim.metric_key not in METRIC_CLAIM_KEYS
                and _level_at_period(artifact.claim.metric_key) is None
            ):
                reason = (
                    "指标结算的 claim.metric_key 只支持 "
                    + "、".join(METRIC_CLAIM_KEYS)
                    + f" 和 {LEVEL_AT_PREFIX}<期末日 YYYY-MM-DD>"
                )
        elif artifact.claim is not None and (
            artifact.claim.metric_key != "total_return"
        ):
            reason = "价格结算只支持 total_return 判定"
        return SettlementSchedule(
            start=artifact.as_of_date,
            checkpoints=settlement_checkpoints(
                artifact.horizon,
                artifact.as_of_date,
                artifact.expires_on,
            ),
            rule=artifact.settlement_rule,
            unsettleable_reason=reason,
        )
    if isinstance(artifact, SettleableAction):
        trigger = artifact.trigger
        condition = None
        if trigger.kind is TriggerKind.SCHEDULED:
            start = trigger.scheduled_on
            reason = None
        else:
            start = trigger.valid_from
            condition = parse_trigger_condition(trigger.observability_key, artifact.asset_id)
            reason = (
                None
                if condition is not None
                else f"条件触发动作的观察键不可机器判定（{trigger.observability_key}），只记录不结算"
            )
        if artifact.settlement_rule.method is SettlementMethod.METRIC_OBSERVATION:
            reason = "动作只按价格结算"
        return SettlementSchedule(
            start=start,
            checkpoints=(
                (trigger.valid_until,)
                if condition is not None
                else settlement_checkpoints(artifact.horizon, start)
            ),
            rule=artifact.settlement_rule,
            unsettleable_reason=reason,
            condition=condition,
            valid_until=trigger.valid_until,
        )
    return None


def settle_checkpoint(
    *,
    entry_id: str,
    artifact: Artifact,
    schedule: SettlementSchedule,
    checkpoint: str,
    prices: dict[
        str,
        tuple[PricePoint, ...] | tuple[MetricPoint, ...] | PriceDataUnavailable,
    ],
    computed_at: str,
    note: str | None = None,
) -> Settlement:
    """Settle one checkpoint using only observations dated on or before it."""

    def unsettled(status: SettlementStatus, note: str) -> Settlement:
        return new_settlement(
            entry_id=entry_id,
            checkpoint_date=checkpoint,
            status=status,
            claim_result=ClaimResult.UNKNOWN,
            computed_at=computed_at,
            note=note,
        )

    if schedule.unsettleable_reason is not None:
        return unsettled(
            SettlementStatus.NOT_SETTLEABLE,
            schedule.unsettleable_reason,
        )
    rule = schedule.rule
    assert rule is not None
    if rule.method is SettlementMethod.METRIC_OBSERVATION:
        return _settle_metric(
            entry_id=entry_id,
            artifact=artifact,
            schedule=schedule,
            checkpoint=checkpoint,
            series=prices[rule.observed_id],
            computed_at=computed_at,
            unsettled=unsettled,
        )
    legs = {}
    for role, asset_id in (
        ("observed", rule.observed_id),
        ("benchmark", rule.benchmark_id),
        ("alternative", rule.alternative_id),
    ):
        if asset_id is None:
            continue
        series = prices[asset_id]
        if isinstance(series, PriceDataUnavailable):
            return unsettled(
                SettlementStatus.DATA_INSUFFICIENT,
                f"{asset_id} 价格获取失败：{series}",
            )
        visible = tuple(item for item in series if item.date <= checkpoint)
        legs[role] = visible
    observed = legs["observed"]
    reference = next(
        (item for item in observed if item.date > schedule.start),
        None,
    )
    if reference is None:
        return unsettled(
            SettlementStatus.DATA_INSUFFICIENT,
            f"{rule.observed_id} 在 {schedule.start} 之后到检查点前没有价格",
        )
    lag = (
        date.fromisoformat(reference.date)
        - date.fromisoformat(schedule.start)
    ).days
    if lag > _MAX_REFERENCE_LAG_DAYS:
        return unsettled(
            SettlementStatus.DATA_INSUFFICIENT,
            f"{rule.observed_id} 在 {schedule.start} 之后 {lag} 天才有首个价格"
            f"（{reference.date}），价格序列可能缺失",
        )
    path = tuple(item for item in observed if item.date >= reference.date)
    observation = path[-1]
    asset_return = _return(reference.value, observation.value)
    relative = {}
    for role in ("benchmark", "alternative"):
        if role not in legs:
            relative[role] = None
            continue
        leg_return = _leg_return(legs[role], reference.date)
        if leg_return is None:
            return unsettled(
                SettlementStatus.DATA_INSUFFICIENT,
                f"{role} 在参考日 {reference.date} 后到检查点前没有价格",
            )
        relative[role] = leg_return
    excess = (
        None
        if relative["benchmark"] is None
        else asset_return - relative["benchmark"]
    )
    direction = _direction(artifact)
    moves = [_return(reference.value, item.value) for item in path]
    adverse, favorable = (
        (max(moves), min(moves)) if direction < 0 else (min(moves), max(moves))
    )
    return new_settlement(
        entry_id=entry_id,
        checkpoint_date=checkpoint,
        status=SettlementStatus.SETTLED,
        claim_result=_claim_result(
            artifact,
            asset_return,
            # A switch (exit with an alternative) is right when the alternative did better.
            excess
            if excess is not None or relative["alternative"] is None or isinstance(artifact, View)
            else asset_return - relative["alternative"],
        ),
        computed_at=computed_at,
        reference_date=reference.date,
        reference_value=reference.value,
        observed_date=observation.date,
        observed_value=observation.value,
        asset_return=asset_return,
        benchmark_return=relative["benchmark"],
        excess_return=excess,
        alternative_return=relative["alternative"],
        max_adverse=adverse,
        max_favorable=favorable,
        note=_join_notes(
            note,
            _basis_note(legs),
            _stale_note(observation.date, checkpoint),
        ),
    )


def _settle_metric(
    *,
    entry_id: str,
    artifact: Artifact,
    schedule: SettlementSchedule,
    checkpoint: str,
    series: tuple[MetricPoint, ...] | PriceDataUnavailable,
    computed_at: str,
    unsettled: Callable[[SettlementStatus, str], Settlement],
) -> Settlement:
    """Reference is the value known at the view date; only later releases count."""

    metric_id = schedule.rule.observed_id
    if isinstance(series, PriceDataUnavailable):
        return unsettled(
            SettlementStatus.DATA_INSUFFICIENT,
            f"{metric_id} 指标获取失败：{series}",
        )
    known = [item for item in series if item.available_at <= schedule.start]
    if not known:
        return unsettled(
            SettlementStatus.DATA_INSUFFICIENT,
            f"{metric_id} 在 {schedule.start} 前没有已发布的值，无法确定参考值",
        )
    reference = max(known, key=lambda item: item.period_end)
    path = sorted(
        (
            item
            for item in series
            if item.period_end > reference.period_end
            and item.available_at <= checkpoint
            and item.period_end <= checkpoint
        ),
        key=lambda item: item.period_end,
    )
    claim = artifact.claim
    key = claim.metric_key if claim is not None else "level"
    target_period = _level_at_period(key)
    deadline = _report_deadline(metric_id, target_period)
    if (
        deadline is not None
        and target_period > reference.period_end
        and checkpoint < deadline
        and not any(item.period_end == target_period for item in path)
    ):
        return unsettled(
            SettlementStatus.NOT_SETTLEABLE,
            f"检查点早于目标期 {target_period} 的法定披露截止日 {deadline}，由之后的检查点结算",
        )
    if not path:
        return unsettled(
            SettlementStatus.DATA_INSUFFICIENT,
            f"{metric_id} 在 {schedule.start} 之后到检查点前没有发布新一期数据",
        )
    latest = path[-1]
    if target_period is not None:
        if target_period <= reference.period_end:
            return unsettled(
                SettlementStatus.NOT_SETTLEABLE,
                f"目标期 {target_period} 在观点日已发布，不能作为前瞻判断结算",
            )
        observed = next(
            (item for item in path if item.period_end == target_period),
            None,
        )
        if observed is None:
            return unsettled(
                SettlementStatus.DATA_INSUFFICIENT,
                f"{metric_id} 目标期 {target_period} 在检查点前尚未发布",
            )
        measured = observed.value
    elif key == "min_level":
        observed = min(path, key=lambda item: item.value)
        measured = observed.value
    elif key == "max_level":
        observed = max(path, key=lambda item: item.value)
        measured = observed.value
    else:
        observed = latest
        measured = (
            latest.value - reference.value if key == "change" else latest.value
        )
    if claim is None:
        result = ClaimResult.NOT_DIRECTIONAL
    else:
        threshold = claim.threshold or Decimal(0)
        met = (
            measured < threshold
            if claim.comparison is ClaimComparison.BELOW
            else measured > threshold
        )
        result = ClaimResult.MET if met else ClaimResult.MISSED
    return new_settlement(
        entry_id=entry_id,
        checkpoint_date=checkpoint,
        status=SettlementStatus.SETTLED,
        claim_result=result,
        computed_at=computed_at,
        reference_date=reference.period_end,
        reference_value=reference.value,
        observed_date=observed.period_end,
        observed_value=observed.value,
        note=(
            f"指标观察：参考值为观点日已发布的 {reference.period_end} 期"
            f"（{reference.available_at} 发布）；检查点前共发布 {len(path)} 期新数据，"
            f"最新 {latest.period_end} 期（{latest.available_at} 发布）为 {latest.value}；"
            f"判定量 {key} = {measured}"
        ),
    )


_ROLE_LABELS = {"observed": "标的", "benchmark": "基准", "alternative": "备选"}


def _basis_note(legs: dict[str, tuple[PricePoint, ...]]) -> str | None:
    bases = {
        role: series[-1].basis
        for role, series in legs.items()
        if series and series[-1].basis is not None
    }
    if not bases:
        return None
    price_roles = [
        _ROLE_LABELS[role] for role, basis in bases.items() if basis == "price"
    ]
    if not price_roles:
        return "收益按复权总回报计算（含分红再投资）"
    text = "、".join(price_roles)
    if bases.get("observed") == "price":
        return f"{text}为价格指数，收益不含分红"
    return (
        f"标的收益按复权总回报计算；{text}为价格指数，不含分红，"
        "超额收益偏高约一个区间股息率"
    )


def _join_notes(*notes: str | None) -> str | None:
    present = [item for item in notes if item]
    return "；".join(present) if present else None


def _leg_return(
    series: tuple[PricePoint, ...],
    reference_date: str,
) -> Decimal | None:
    path = [item for item in series if item.date >= reference_date]
    if not path:
        return None
    return _return(path[0].value, path[-1].value)


def _return(start: Decimal, end: Decimal) -> Decimal:
    return ((end / start) - 1).quantize(RETURN_QUANTUM)


def _direction(artifact: Artifact) -> int:
    if isinstance(artifact, View):
        if artifact.claim is None:
            return 0
        return (
            -1
            if artifact.claim.comparison
            in (ClaimComparison.UNDERPERFORM, ClaimComparison.BELOW)
            else 1
        )
    if artifact.action_type in _BULLISH_ACTIONS:
        return 1
    if artifact.action_type in _BEARISH_ACTIONS:
        return -1
    return 0


def _claim_result(
    artifact: Artifact,
    asset_return: Decimal,
    excess: Decimal | None,
) -> ClaimResult:
    if isinstance(artifact, View):
        claim = artifact.claim
        if claim is None:
            return ClaimResult.NOT_DIRECTIONAL
        threshold = claim.threshold or Decimal(0)
        met = {
            ClaimComparison.OUTPERFORM: lambda: excess > threshold,
            ClaimComparison.UNDERPERFORM: lambda: excess < -threshold,
            ClaimComparison.ABOVE: lambda: asset_return > threshold,
            ClaimComparison.BELOW: lambda: asset_return < threshold,
        }[claim.comparison]()
        return ClaimResult.MET if met else ClaimResult.MISSED
    direction = _direction(artifact)
    if direction == 0:
        return ClaimResult.NOT_DIRECTIONAL
    measure = excess if excess is not None else asset_return
    return ClaimResult.MET if measure * direction > 0 else ClaimResult.MISSED


def _stale_note(observed_date: str, checkpoint: str) -> str | None:
    gap = (date.fromisoformat(checkpoint) - date.fromisoformat(observed_date)).days
    if gap <= STALE_OBSERVATION_DAYS:
        return None
    return (
        f"检查点前 {gap} 天没有新价格（停牌、退市或数据未更新），"
        f"使用 {observed_date} 的价格"
    )
