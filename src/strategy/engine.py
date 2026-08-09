"""Tri-state strategy condition evaluator and executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import (
    ConditionGroup,
    ConditionLogic,
    ConditionNode,
    ConditionOperator,
    ConditionOutcome,
    MetricCondition,
    StrategyInput,
    StrategyResult,
    StrategyRunStatus,
    StrategySpec,
)


@dataclass(frozen=True)
class ConditionEvaluation:
    outcome: ConditionOutcome
    matched_conditions: tuple[str, ...] = ()
    failed_conditions: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()


class StrategyExecutor:
    def execute(
        self,
        spec: StrategySpec,
        strategy_input: StrategyInput,
    ) -> StrategyResult:
        if strategy_input.profile_id not in spec.supported_profiles:
            raise ValueError(
                f"Strategy {spec.strategy_id} does not support "
                f"profile {strategy_input.profile_id}"
            )
        if strategy_input.horizon not in spec.horizons:
            raise ValueError(
                f"Strategy {spec.strategy_id} does not support "
                f"horizon {strategy_input.horizon.value}"
            )
        missing_metrics = tuple(
            sorted(
                metric_name
                for metric_name in spec.required_metrics
                if metric_name not in strategy_input.metrics
                or not strategy_input.metrics[
                    metric_name
                ].applicable
                or strategy_input.metrics[metric_name].value
                is None
            )
        )
        missing_evidence = tuple(
            sorted(
                set(spec.required_evidence_types)
                - set(strategy_input.evidence_types)
            )
        )
        required_count = (
            len(spec.required_metrics)
            + len(spec.required_evidence_types)
        )
        available_count = (
            required_count
            - len(missing_metrics)
            - len(missing_evidence)
        )
        completeness = (
            Decimal(available_count) / Decimal(required_count)
            if required_count
            else Decimal("1")
        )
        metric_versions = {
            name: metric.calculation_version
            for name, metric in strategy_input.metrics.items()
        }
        if missing_metrics or missing_evidence:
            return self._result(
                spec,
                strategy_input,
                status=StrategyRunStatus.INSUFFICIENT_DATA,
                signal="insufficient_data",
                score=None,
                confidence=None,
                completeness=completeness,
                matched=(),
                failed=(),
                vetoed=(),
                missing_metrics=missing_metrics,
                missing_evidence=missing_evidence,
                metric_versions=metric_versions,
            )

        veto_traces = [
            evaluate_condition(node, strategy_input)
            for node in spec.vetoes
        ]
        hard_traces = [
            evaluate_condition(node, strategy_input)
            for node in spec.hard_filters
        ]
        score_traces = [
            (
                weighted,
                evaluate_condition(
                    weighted.condition,
                    strategy_input,
                ),
            )
            for weighted in spec.score_rules
        ]
        all_traces = (
            veto_traces
            + hard_traces
            + [trace for _, trace in score_traces]
        )
        runtime_missing = tuple(
            sorted(
                {
                    metric
                    for trace in all_traces
                    for metric in trace.missing_metrics
                }
            )
        )
        decision_traces = hard_traces + [
            trace for _, trace in score_traces
        ]
        matched = _merge_ids(
            trace.matched_conditions
            for trace in decision_traces
        )
        failed = _merge_ids(
            trace.failed_conditions
            for trace in decision_traces
        )
        if runtime_missing:
            return self._result(
                spec,
                strategy_input,
                status=StrategyRunStatus.INSUFFICIENT_DATA,
                signal="insufficient_data",
                score=None,
                confidence=None,
                completeness=completeness,
                matched=matched,
                failed=failed,
                vetoed=(),
                missing_metrics=runtime_missing,
                missing_evidence=(),
                metric_versions=metric_versions,
            )

        vetoed = _merge_ids(
            trace.matched_conditions
            for trace in veto_traces
            if trace.outcome is ConditionOutcome.TRUE
        )
        score = _score(score_traces)
        if vetoed:
            status = StrategyRunStatus.VETOED
            signal = "vetoed"
        elif any(
            trace.outcome is ConditionOutcome.FALSE
            for trace in hard_traces
        ):
            status = StrategyRunStatus.FAIL
            signal = spec.signal_on_fail
        elif score >= spec.passing_score:
            status = StrategyRunStatus.PASS
            signal = spec.signal_on_pass
        else:
            status = StrategyRunStatus.FAIL
            signal = spec.signal_on_fail
        return self._result(
            spec,
            strategy_input,
            status=status,
            signal=signal,
            score=score,
            confidence=Decimal("1"),
            completeness=completeness,
            matched=matched,
            failed=failed,
            vetoed=vetoed,
            missing_metrics=(),
            missing_evidence=(),
            metric_versions=metric_versions,
        )

    def _result(
        self,
        spec: StrategySpec,
        strategy_input: StrategyInput,
        *,
        status: StrategyRunStatus,
        signal: str,
        score: Decimal | None,
        confidence: Decimal | None,
        completeness: Decimal,
        matched: tuple[str, ...],
        failed: tuple[str, ...],
        vetoed: tuple[str, ...],
        missing_metrics: tuple[str, ...],
        missing_evidence: tuple[str, ...],
        metric_versions: dict[str, str],
    ) -> StrategyResult:
        valid_until = (
            date.fromisoformat(strategy_input.as_of_date)
            + timedelta(days=spec.valid_for_days)
        ).isoformat()
        limitations = (
            *spec.limitations,
            "confidence reflects required-input completeness only",
        )
        return StrategyResult(
            strategy_id=spec.strategy_id,
            strategy_version=spec.version,
            maturity=spec.maturity,
            asset_id=strategy_input.asset_id,
            horizon=strategy_input.horizon,
            as_of_date=strategy_input.as_of_date,
            valid_until=valid_until,
            status=status,
            signal=signal,
            score=score,
            confidence=confidence,
            input_completeness=completeness,
            matched_conditions=matched,
            failed_conditions=failed,
            vetoed_conditions=vetoed,
            missing_metrics=missing_metrics,
            missing_evidence_types=missing_evidence,
            metric_versions=metric_versions,
            counterevidence=tuple(
                sorted(set(failed) | set(vetoed))
            ),
            limitations=limitations,
            input_effective_dates={
                name: metric.as_of_date
                for name, metric in strategy_input.metrics.items()
            },
        )


def evaluate_condition(
    node: ConditionNode,
    strategy_input: StrategyInput,
) -> ConditionEvaluation:
    if isinstance(node, MetricCondition):
        metric = strategy_input.metrics.get(node.metric_name)
        if (
            metric is None
            or not metric.applicable
            or metric.value is None
        ):
            return ConditionEvaluation(
                outcome=ConditionOutcome.MISSING,
                missing_metrics=(node.metric_name,),
            )
        matched = _compare(metric.value, node)
        return ConditionEvaluation(
            outcome=(
                ConditionOutcome.TRUE
                if matched
                else ConditionOutcome.FALSE
            ),
            matched_conditions=(
                (node.condition_id,) if matched else ()
            ),
            failed_conditions=(
                () if matched else (node.condition_id,)
            ),
        )

    children = [
        evaluate_condition(child, strategy_input)
        for child in node.children
    ]
    if node.logic is ConditionLogic.AND:
        if any(
            child.outcome is ConditionOutcome.FALSE
            for child in children
        ):
            outcome = ConditionOutcome.FALSE
        elif any(
            child.outcome is ConditionOutcome.MISSING
            for child in children
        ):
            outcome = ConditionOutcome.MISSING
        else:
            outcome = ConditionOutcome.TRUE
    else:
        if any(
            child.outcome is ConditionOutcome.TRUE
            for child in children
        ):
            outcome = ConditionOutcome.TRUE
        elif any(
            child.outcome is ConditionOutcome.MISSING
            for child in children
        ):
            outcome = ConditionOutcome.MISSING
        else:
            outcome = ConditionOutcome.FALSE
    matched = _merge_ids(
        child.matched_conditions for child in children
    )
    failed = _merge_ids(
        child.failed_conditions for child in children
    )
    if outcome is ConditionOutcome.TRUE:
        matched = tuple(sorted(set(matched) | {node.condition_id}))
    elif outcome is ConditionOutcome.FALSE:
        failed = tuple(sorted(set(failed) | {node.condition_id}))
    return ConditionEvaluation(
        outcome=outcome,
        matched_conditions=matched,
        failed_conditions=failed,
        missing_metrics=_merge_ids(
            child.missing_metrics for child in children
        ),
    )


def _compare(value: Decimal, condition: MetricCondition) -> bool:
    if condition.operator is ConditionOperator.GT:
        return value > condition.threshold
    if condition.operator is ConditionOperator.GTE:
        return value >= condition.threshold
    if condition.operator is ConditionOperator.LT:
        return value < condition.threshold
    if condition.operator is ConditionOperator.LTE:
        return value <= condition.threshold
    if condition.operator is ConditionOperator.EQ:
        return value == condition.threshold
    return (
        condition.threshold
        <= value
        <= condition.upper_threshold
    )


def _score(score_traces) -> Decimal:
    if not score_traces:
        return Decimal("1")
    total = sum(
        (weighted.weight for weighted, _ in score_traces),
        Decimal("0"),
    )
    matched = sum(
        (
            weighted.weight
            for weighted, trace in score_traces
            if trace.outcome is ConditionOutcome.TRUE
        ),
        Decimal("0"),
    )
    return matched / total


def _merge_ids(values) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item
                for group in values
                for item in group
            }
        )
    )
