"""Reviewed final-report monitoring handoff and activation service."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.alerts import (
    AlertRule,
    AlertRuleProvenance,
    AlertService,
)
from src.user_space import UserContext, UserSpaceService

from .final_decision_models import (
    MonitoringCondition,
    MonitoringConditionKind,
    MonitoringPlan,
)
from .final_decision_pipeline import FinalDecisionPublishResult
from .monitoring_models import (
    MonitoringConditionDisposition,
    MonitoringConditionHandoff,
    MonitoringGap,
    MonitoringHandoff,
    MonitoringHandoffStatus,
    MonitoringRuleBinding,
    MonitoringRuleCandidate,
    MonitoringRuleRegistration,
    MonitoringRuleType,
    monitoring_handoff_id,
    monitoring_rule_candidate_id,
    monitoring_rule_matches_observability,
)
from .workflow import ResearchWorkflowCoordinator


class MonitoringHandoffService:
    """Prepare traceable candidates and arm only confirmed local rules."""

    def __init__(
        self,
        *,
        alerts: AlertService,
        workflow: ResearchWorkflowCoordinator,
        user_space: UserSpaceService,
    ) -> None:
        self.alerts = alerts
        self.workflow = workflow
        self.user_space = user_space

    def prepare(
        self,
        context: UserContext,
        publication: FinalDecisionPublishResult,
        *,
        bindings: tuple[MonitoringRuleBinding, ...] = (),
    ) -> MonitoringHandoff:
        """Persist a proposal without creating or enabling alert rules."""

        self._require_user(context)
        report = publication.report
        if publication.published is None or not publication.review.passed:
            raise ValueError(
                "monitoring requires a reviewed and published final report"
            )
        if report.user_id != context.user_id:
            raise ValueError("final report belongs to another user")
        workflow_run = self.workflow.get_workflow_run(
            report.workflow_run_id
        )
        if workflow_run is None:
            raise KeyError(
                f"Unknown workflow_run_id: {report.workflow_run_id}"
            )
        _validate_report_workflow(report, workflow_run)
        attestation = self.workflow.get_final_publication(
            report.report_id
        )
        _validate_publication_attestation(
            publication,
            report,
            attestation,
        )
        plan = attestation.monitoring_plan
        handoff_id = monitoring_handoff_id(
            report.report_id,
            plan.plan_id,
        )
        existing = self.workflow.get_monitoring_handoff(handoff_id)
        if existing is not None:
            return existing

        binding_map = _binding_map(
            plan.conditions,
            bindings,
        )
        conditions: list[MonitoringConditionHandoff] = []
        candidates: list[MonitoringRuleCandidate] = []
        gaps: list[MonitoringGap] = []
        for condition in plan.conditions:
            if (
                condition.kind
                is MonitoringConditionKind.MANUAL_REVIEW
            ):
                conditions.append(
                    _manual_condition_handoff(condition)
                )
                continue
            binding = binding_map.get(condition.condition_id)
            if binding is None:
                gap = MonitoringGap(
                    code="monitoring_adapter_missing",
                    message=(
                        "可观察条件尚未绑定价格、事件、日期或组合权重规则。"
                    ),
                    retryable=False,
                    condition_id=condition.condition_id,
                )
                gaps.append(gap)
                conditions.append(
                    _unsupported_condition_handoff(
                        condition,
                        gap.code,
                        gap.message,
                    )
                )
                continue
            if not _binding_matches_key(
                binding.rule_type,
                condition.observability_key,
            ):
                gap = MonitoringGap(
                    code="monitoring_observability_key_unsupported",
                    message=(
                        "条件的观察键不能由所选公共提醒规则可靠观测。"
                    ),
                    retryable=False,
                    condition_id=condition.condition_id,
                )
                gaps.append(gap)
                conditions.append(
                    _unsupported_condition_handoff(
                        condition,
                        gap.code,
                        gap.message,
                    )
                )
                continue
            candidate = _candidate_from_binding(
                report.asset_id,
                condition,
                binding,
            )
            candidates.append(candidate)
            conditions.append(
                _candidate_condition_handoff(
                    condition,
                    candidate,
                )
            )

        if plan.review_date is not None:
            review_condition, review_candidate = (
                _review_date_candidate(
                    asset_id=report.asset_id,
                    plan_id=plan.plan_id,
                    review_date=plan.review_date,
                )
            )
            conditions.append(review_condition)
            candidates.append(review_candidate)

        if candidates:
            status = (
                MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED
            )
        elif any(
            item.disposition
            is MonitoringConditionDisposition.MANUAL_REVIEW
            for item in conditions
        ):
            status = MonitoringHandoffStatus.MANUAL_REVIEW_ONLY
        else:
            status = MonitoringHandoffStatus.UNSUPPORTED
            if not gaps:
                gaps.append(
                    MonitoringGap(
                        code="monitoring_plan_unsupported",
                        message=(
                            "当前监测计划没有可注册规则或人工复查项。"
                        ),
                        retryable=False,
                    )
                )

        now = _now()
        handoff = MonitoringHandoff(
            handoff_id=handoff_id,
            user_id=context.user_id,
            workflow_run_id=report.workflow_run_id,
            final_report_id=report.report_id,
            monitoring_plan_id=plan.plan_id,
            asset_id=report.asset_id,
            status=status,
            conditions=tuple(conditions),
            rule_candidates=tuple(candidates),
            confirmed_candidate_ids=(),
            registrations=(),
            gaps=tuple(gaps),
            created_at=now,
            updated_at=now,
        )
        self.workflow.record_monitoring_handoff(handoff)
        return handoff

    def confirm_and_activate(
        self,
        context: UserContext,
        handoff_id: str,
        *,
        candidate_ids: tuple[str, ...],
    ) -> MonitoringHandoff:
        """Register only the explicitly confirmed rule candidates."""

        self._require_user(context)
        if not candidate_ids or len(candidate_ids) != len(
            set(candidate_ids)
        ):
            raise ValueError(
                "candidate_ids must be a non-empty unique selection"
            )
        handoff = self.workflow.get_monitoring_handoff(handoff_id)
        if handoff is None:
            raise KeyError(f"Unknown monitoring handoff: {handoff_id}")
        if handoff.user_id != context.user_id:
            raise ValueError("monitoring handoff belongs to another user")
        candidate_map = {
            item.candidate_id: item
            for item in handoff.rule_candidates
        }
        unknown = set(candidate_ids).difference(candidate_map)
        if unknown:
            raise ValueError("confirmation contains unknown candidates")

        now = _now()
        registrations = {
            item.candidate_id: item
            for item in handoff.registrations
        }
        if all(item in registrations for item in candidate_ids):
            self.workflow.record_monitoring_handoff(handoff)
            return handoff
        gaps = [
            item
            for item in handoff.gaps
            if not (
                item.code == "alert_rule_registration_failed"
                and item.condition_id
                in {
                    candidate_map[item].condition_id
                    for item in candidate_ids
                }
            )
        ]
        confirmed = tuple(
            dict.fromkeys(
                (*handoff.confirmed_candidate_ids, *candidate_ids)
            )
        )
        pending = replace(
            handoff,
            status=MonitoringHandoffStatus.ACTIVATION_PENDING,
            confirmed_candidate_ids=confirmed,
            gaps=tuple(gaps),
            confirmed_at=handoff.confirmed_at or now,
            updated_at=now,
        )
        self.workflow.record_monitoring_handoff(pending)
        failures = 0
        for candidate_id in candidate_ids:
            if candidate_id in registrations:
                continue
            candidate = candidate_map[candidate_id]
            try:
                rule = self._register_rule(
                    context,
                    pending,
                    candidate,
                )
            except Exception as exc:
                failures += 1
                gaps.append(
                    MonitoringGap(
                        code="alert_rule_registration_failed",
                        message=(
                            "本地提醒规则注册失败；已发布报告结论保持不变。"
                            f"（{type(exc).__name__}）"
                        ),
                        retryable=True,
                        condition_id=candidate.condition_id,
                    )
                )
                continue
            registrations[candidate_id] = (
                MonitoringRuleRegistration(
                    candidate_id=candidate_id,
                    rule_id=rule.rule_id,
                    activated_at=now,
                )
            )

        all_candidate_ids = {
            item.candidate_id for item in pending.rule_candidates
        }
        registered_ids = set(registrations)
        if registered_ids == all_candidate_ids:
            status = MonitoringHandoffStatus.ARMED
        elif registered_ids:
            status = MonitoringHandoffStatus.PARTIALLY_ARMED
        elif failures:
            status = MonitoringHandoffStatus.REGISTRATION_FAILED
        else:
            status = (
                MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED
            )
        updated = replace(
            pending,
            status=status,
            registrations=tuple(
                registrations[item.candidate_id]
                for item in pending.rule_candidates
                if item.candidate_id in registrations
            ),
            gaps=tuple(gaps),
            updated_at=now,
        )
        self.workflow.record_monitoring_handoff(updated)
        return updated

    def get(
        self,
        context: UserContext,
        handoff_id: str,
    ) -> MonitoringHandoff | None:
        self._require_user(context)
        handoff = self.workflow.get_monitoring_handoff(handoff_id)
        if handoff is None:
            return None
        if handoff.user_id != context.user_id:
            raise ValueError("monitoring handoff belongs to another user")
        return handoff

    def _register_rule(
        self,
        context: UserContext,
        handoff: MonitoringHandoff,
        candidate: MonitoringRuleCandidate,
    ) -> AlertRule:
        provenance = AlertRuleProvenance(
            workflow_run_id=handoff.workflow_run_id,
            final_report_id=handoff.final_report_id,
            monitoring_plan_id=handoff.monitoring_plan_id,
            monitoring_condition_id=candidate.condition_id,
            condition_source=candidate.condition_source,
            evidence_refs=candidate.evidence_refs,
        )
        config: dict[str, object]
        if candidate.rule_type in {
            MonitoringRuleType.PRICE_ABOVE,
            MonitoringRuleType.PRICE_BELOW,
            MonitoringRuleType.MAX_WEIGHT_ABOVE,
        }:
            config = {"threshold": float(candidate.threshold)}
        elif candidate.rule_type is MonitoringRuleType.EVENT_TYPE:
            config = {"event_type": str(candidate.event_type)}
        else:
            config = {"review_date": str(candidate.review_date)}
        return self.alerts._create_monitoring_rule(
            context,
            asset_id=candidate.alert_asset_id,
            rule_type=candidate.rule_type.value,
            config=config,
            provenance=provenance,
        )

    def _require_user(self, context: UserContext) -> None:
        if self.user_space.get_user(context.user_id) is None:
            raise KeyError(f"Unknown user_id: {context.user_id}")


def _validate_report_workflow(report: object, workflow: object) -> None:
    expected = (
        (
            getattr(workflow, "decision_brief_id"),
            getattr(report, "decision_brief_id"),
            "decision brief",
        ),
        (
            getattr(workflow, "exploration_session_id"),
            getattr(report, "session_id"),
            "exploration session",
        ),
        (
            getattr(workflow, "handoff_id"),
            getattr(report, "handoff_id"),
            "baseline handoff",
        ),
        (
            getattr(workflow, "task").asset_id,
            getattr(report, "asset_id"),
            "asset",
        ),
        (
            getattr(workflow, "task").profile_id,
            getattr(report, "profile_id"),
            "profile",
        ),
        (
            getattr(workflow, "task").as_of_date,
            getattr(report, "as_of_date"),
            "cutoff date",
        ),
        (
            getattr(workflow, "user_id"),
            getattr(report, "user_id"),
            "user",
        ),
    )
    for actual, report_value, label in expected:
        if actual != report_value:
            raise ValueError(
                f"final report {label} does not match workflow"
            )
    final_report_id = getattr(workflow, "final_report_id")
    if (
        final_report_id is not None
        and final_report_id != getattr(report, "report_id")
    ):
        raise ValueError(
            "workflow is already linked to another final report"
        )


def _validate_publication_attestation(
    publication: FinalDecisionPublishResult,
    report: object,
    attestation: object | None,
) -> None:
    if attestation is None:
        raise ValueError(
            "monitoring requires a persisted publication attestation"
        )
    expected = (
        ("report_id", "report_id"),
        ("user_id", "user_id"),
        ("workflow_run_id", "workflow_run_id"),
        ("session_id", "session_id"),
        ("decision_brief_id", "decision_brief_id"),
        ("baseline_handoff_id", "handoff_id"),
        ("asset_id", "asset_id"),
        ("profile_id", "profile_id"),
        ("as_of_date", "as_of_date"),
    )
    for attestation_field, report_field in expected:
        if getattr(attestation, attestation_field) != getattr(
            report,
            report_field,
        ):
            raise ValueError(
                "final publication attestation does not match report"
            )
    if (
        getattr(attestation, "monitoring_plan_id")
        != getattr(report, "monitoring_plan").plan_id
    ):
        raise ValueError(
            "publication monitoring plan does not match report"
        )
    report_plan = getattr(report, "monitoring_plan")
    if (
        not isinstance(report_plan, MonitoringPlan)
        or report_plan != getattr(attestation, "monitoring_plan")
    ):
        raise ValueError(
            "report monitoring plan differs from canonical publication"
        )
    published_path = Path(
        publication.published.report_path
    ).resolve()
    attested_path = Path(
        getattr(attestation, "report_path")
    ).resolve()
    if published_path != attested_path or not attested_path.is_file():
        raise ValueError(
            "published final report path does not match attestation"
        )
    if (
        hashlib.sha256(attested_path.read_bytes()).hexdigest()
        != getattr(attestation, "markdown_digest")
    ):
        raise ValueError(
            "published final report digest does not match attestation"
        )


def _binding_map(
    conditions: tuple[MonitoringCondition, ...],
    bindings: tuple[MonitoringRuleBinding, ...],
) -> dict[str, MonitoringRuleBinding]:
    condition_map = {item.condition_id: item for item in conditions}
    output: dict[str, MonitoringRuleBinding] = {}
    for binding in bindings:
        condition = condition_map.get(binding.condition_id)
        if condition is None:
            raise ValueError(
                "monitoring binding references an unknown condition"
            )
        if (
            condition.kind
            is not MonitoringConditionKind.MACHINE_OBSERVABLE
        ):
            raise ValueError(
                "manual-review condition cannot bind an alert rule"
            )
        if binding.condition_id in output:
            raise ValueError(
                "monitoring condition has duplicate rule bindings"
            )
        output[binding.condition_id] = binding
    return output


def _binding_matches_key(
    rule_type: MonitoringRuleType,
    observability_key: str | None,
) -> bool:
    return monitoring_rule_matches_observability(
        rule_type,
        observability_key,
    )


def _candidate_from_binding(
    asset_id: str,
    condition: MonitoringCondition,
    binding: MonitoringRuleBinding,
) -> MonitoringRuleCandidate:
    alert_asset_id = (
        None
        if binding.rule_type
        is MonitoringRuleType.MAX_WEIGHT_ABOVE
        else asset_id
    )
    payload = {
        "candidate_id": "",
        "condition_id": condition.condition_id,
        "rule_type": binding.rule_type.value,
        "subject_asset_id": asset_id,
        "alert_asset_id": alert_asset_id,
        "condition_source": condition.source,
        "evidence_refs": list(condition.evidence_refs),
        "limitations": list(condition.limitations),
        "threshold": binding.threshold,
        "event_type": binding.event_type,
        "review_date": binding.review_date,
    }
    payload["candidate_id"] = monitoring_rule_candidate_id(payload)
    return MonitoringRuleCandidate.from_dict(payload)


def _review_date_candidate(
    *,
    asset_id: str,
    plan_id: str,
    review_date: str,
) -> tuple[MonitoringConditionHandoff, MonitoringRuleCandidate]:
    condition_id = "monitor-review-date:" + hashlib.sha256(
        f"{plan_id}\0{review_date}".encode("utf-8")
    ).hexdigest()[:20]
    payload = {
        "candidate_id": "",
        "condition_id": condition_id,
        "rule_type": MonitoringRuleType.REVIEW_DATE.value,
        "subject_asset_id": asset_id,
        "alert_asset_id": asset_id,
        "condition_source": "最终报告复查日期",
        "evidence_refs": [],
        "limitations": ["仅在显式执行本地日期评估时触发。"],
        "threshold": None,
        "event_type": None,
        "review_date": review_date,
    }
    payload["candidate_id"] = monitoring_rule_candidate_id(payload)
    candidate = MonitoringRuleCandidate.from_dict(payload)
    condition = MonitoringConditionHandoff(
        condition_id=condition_id,
        text=f"在 {review_date} 复查当前研究结论。",
        source="最终报告复查日期",
        disposition=MonitoringConditionDisposition.RULE_CANDIDATE,
        review_by=review_date,
        evidence_refs=(),
        limitations=candidate.limitations,
        candidate_id=candidate.candidate_id,
    )
    return condition, candidate


def _candidate_condition_handoff(
    condition: MonitoringCondition,
    candidate: MonitoringRuleCandidate,
) -> MonitoringConditionHandoff:
    return MonitoringConditionHandoff(
        condition_id=condition.condition_id,
        text=condition.text,
        source=condition.source,
        disposition=MonitoringConditionDisposition.RULE_CANDIDATE,
        review_by=condition.review_by,
        evidence_refs=condition.evidence_refs,
        limitations=condition.limitations,
        candidate_id=candidate.candidate_id,
    )


def _manual_condition_handoff(
    condition: MonitoringCondition,
) -> MonitoringConditionHandoff:
    return MonitoringConditionHandoff(
        condition_id=condition.condition_id,
        text=condition.text,
        source=condition.source,
        disposition=MonitoringConditionDisposition.MANUAL_REVIEW,
        review_by=condition.review_by,
        evidence_refs=condition.evidence_refs,
        limitations=condition.limitations,
    )


def _unsupported_condition_handoff(
    condition: MonitoringCondition,
    gap_code: str,
    message: str,
) -> MonitoringConditionHandoff:
    return MonitoringConditionHandoff(
        condition_id=condition.condition_id,
        text=condition.text,
        source=condition.source,
        disposition=MonitoringConditionDisposition.UNSUPPORTED,
        review_by=condition.review_by,
        evidence_refs=condition.evidence_refs,
        limitations=(*condition.limitations, message),
        gap_code=gap_code,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
