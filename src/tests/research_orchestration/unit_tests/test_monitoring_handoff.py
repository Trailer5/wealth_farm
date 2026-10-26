import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.alerts import AlertService, SQLiteAlertRepository
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_orchestration import (
    MonitoringCondition,
    MonitoringConditionDisposition,
    MonitoringConditionKind,
    MonitoringHandoffService,
    MonitoringHandoffStatus,
    MonitoringPlan,
    MonitoringPlanStatus,
    MonitoringRuleBinding,
    MonitoringRuleType,
    FinalDecisionPublicationAttestation,
    PublishedJudgements,
    ResearchRunStatus,
    ResearchWorkflowCoordinator,
    ResearchWorkflowRun,
    ReviewResult,
    default_capability_registry,
)
from src.research_orchestration.final_decision_models import (
    monitoring_plan_id,
)
from src.research_orchestration.sqlite_repository import (
    SQLiteResearchWorkflowRepository,
)
from src.research_contracts import (
    PROFILE_ORDINARY_COMPANY,
    Confidence,
    DecisionLane,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
    SubjectType,
    View,
    ViewOrigin,
)
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class MonitoringHandoffServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitoring.sqlite3"
        self.users = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        self.context = self.users.default_context()
        self.master = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master.register_asset(
            Asset(
                asset_id="stock:monitoring-test",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="监测测试标的",
                market="cn",
                exchange="sh",
            ),
            source="unit_test",
            raw_symbol="600519",
        )
        self.workflow_repository = SQLiteResearchWorkflowRepository(
            self.db_path
        )
        self.workflow = ResearchWorkflowCoordinator(
            master_data=self.master,
            capability_registry=default_capability_registry(),
            repository=self.workflow_repository,
        )
        self.task = ResearchTaskSpec(
            task_id="research:monitoring-test",
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-24",
        )
        self.workflow_repository.save_workflow_run(
            ResearchWorkflowRun(
                workflow_run_id="workflow:monitoring-test",
                task=self.task,
                decision_brief_id="brief:monitoring-test",
                decision_lanes=tuple(DecisionLane),
                baseline_status=ResearchRunStatus.COMPLETE,
                baseline_report_path="baseline.md",
                handoff_id="handoff:monitoring-test",
                blocking_gap_codes=(),
                blocking_gaps=(),
                exploration_session_id="session:monitoring-test",
                created_at="2026-08-24T01:00:00+00:00",
                updated_at="2026-08-24T01:00:00+00:00",
                user_id=self.context.user_id,
            )
        )
        self.alerts = AlertService(
            SQLiteAlertRepository(self.db_path),
            self.users,
            self.master,
            rule_authorizer=(
                self.workflow.is_alert_rule_authorized
            ),
        )
        self.service = MonitoringHandoffService(
            alerts=self.alerts,
            workflow=self.workflow,
            user_space=self.users,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prepare_preserves_manual_and_unsupported_conditions(self):
        price = _condition(
            "condition:price",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="price",
        )
        unsupported = _condition(
            "condition:unsupported",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="valuation_and_operations",
        )
        manual = _condition(
            "condition:manual",
            MonitoringConditionKind.MANUAL_REVIEW,
        )
        publication = self._publication(
            _plan(
                (price, unsupported, manual),
                review_date="2026-09-30",
            )
        )

        handoff = self.service.prepare(
            self.context,
            publication,
            bindings=(
                MonitoringRuleBinding(
                    condition_id=price.condition_id,
                    rule_type=MonitoringRuleType.PRICE_BELOW,
                    threshold=1200.0,
                ),
            ),
        )

        self.assertEqual(
            handoff.status,
            MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED,
        )
        self.assertEqual(len(handoff.rule_candidates), 2)
        self.assertEqual(self.alerts.list_rules(self.context), [])
        dispositions = {
            item.condition_id: item.disposition
            for item in handoff.conditions
        }
        self.assertEqual(
            dispositions[manual.condition_id],
            MonitoringConditionDisposition.MANUAL_REVIEW,
        )
        self.assertEqual(
            dispositions[unsupported.condition_id],
            MonitoringConditionDisposition.UNSUPPORTED,
        )
        self.assertIn(
            "monitoring_adapter_missing",
            handoff.gap_codes,
        )
        workflow = self.workflow.get_workflow_run(
            publication.report.workflow_run_id
        )
        self.assertEqual(
            workflow.monitoring_handoff_id,
            handoff.handoff_id,
        )
        self.assertEqual(
            workflow.final_report_id,
            publication.report.report_id,
        )

        restarted_workflow = ResearchWorkflowCoordinator(
            master_data=self.master,
            capability_registry=default_capability_registry(),
            repository=SQLiteResearchWorkflowRepository(
                self.db_path
            ),
        )
        restarted = MonitoringHandoffService(
            alerts=AlertService(
                SQLiteAlertRepository(self.db_path),
                self.users,
                self.master,
                rule_authorizer=(
                    restarted_workflow.is_alert_rule_authorized
                ),
            ),
            workflow=restarted_workflow,
            user_space=self.users,
        )
        self.assertEqual(
            restarted.get(self.context, handoff.handoff_id),
            handoff,
        )

    def test_only_confirmed_candidates_are_armed_and_traceable(self):
        price = _condition(
            "condition:price",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="price",
        )
        publication = self._publication(
            _plan((price,), review_date="2026-09-30")
        )
        proposed = self.service.prepare(
            self.context,
            publication,
            bindings=(
                MonitoringRuleBinding(
                    condition_id=price.condition_id,
                    rule_type=MonitoringRuleType.PRICE_BELOW,
                    threshold=1200.0,
                ),
            ),
        )
        price_candidate = next(
            item
            for item in proposed.rule_candidates
            if item.condition_id == price.condition_id
        )
        date_candidate = next(
            item
            for item in proposed.rule_candidates
            if item.rule_type is MonitoringRuleType.REVIEW_DATE
        )

        partial = self.service.confirm_and_activate(
            self.context,
            proposed.handoff_id,
            candidate_ids=(price_candidate.candidate_id,),
        )

        self.assertEqual(
            partial.status,
            MonitoringHandoffStatus.PARTIALLY_ARMED,
        )
        self.assertEqual(len(self.alerts.list_rules(self.context)), 1)
        price_alerts = self.alerts.evaluate_price(
            self.context,
            asset_id=self.asset.asset_id,
            price=1100.0,
            observed_at="2026-08-25",
        )
        provenance = price_alerts[0].payload[
            "research_provenance"
        ]
        self.assertEqual(
            provenance["workflow_run_id"],
            proposed.workflow_run_id,
        )
        self.assertEqual(
            provenance["final_report_id"],
            proposed.final_report_id,
        )
        self.assertEqual(
            provenance["monitoring_condition_id"],
            price.condition_id,
        )

        armed = self.service.confirm_and_activate(
            self.context,
            proposed.handoff_id,
            candidate_ids=(date_candidate.candidate_id,),
        )
        repeated = self.service.confirm_and_activate(
            self.context,
            proposed.handoff_id,
            candidate_ids=(date_candidate.candidate_id,),
        )

        self.assertEqual(
            armed.status,
            MonitoringHandoffStatus.ARMED,
        )
        self.assertEqual(repeated.alert_rule_refs, armed.alert_rule_refs)
        self.assertEqual(len(self.alerts.list_rules(self.context)), 2)
        date_alerts = self.alerts.evaluate_review_date(
            self.context,
            observed_on="2026-09-30",
        )
        self.assertEqual(len(date_alerts), 1)
        self.assertEqual(
            date_alerts[0].payload["research_provenance"][
                "monitoring_plan_id"
            ],
            proposed.monitoring_plan_id,
        )
        workflow = self.workflow.get_workflow_run(
            proposed.workflow_run_id
        )
        self.assertEqual(
            workflow.monitoring_status,
            MonitoringHandoffStatus.ARMED,
        )
        self.assertEqual(
            workflow.alert_rule_refs,
            armed.alert_rule_refs,
        )

    def test_manual_only_plan_does_not_create_rule_candidates(self):
        manual = _condition(
            "condition:manual",
            MonitoringConditionKind.MANUAL_REVIEW,
        )

        handoff = self.service.prepare(
            self.context,
            self._publication(_plan((manual,), review_date=None)),
        )

        self.assertEqual(
            handoff.status,
            MonitoringHandoffStatus.MANUAL_REVIEW_ONLY,
        )
        self.assertEqual(handoff.rule_candidates, ())
        self.assertEqual(self.alerts.list_rules(self.context), [])

    def test_registration_failure_is_a_gap_and_does_not_mutate_report(self):
        price = _condition(
            "condition:price",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="price",
        )
        publication = self._publication(
            _plan((price,), review_date=None)
        )
        report_plan_before = publication.report.monitoring_plan.to_dict()
        proposed = self.service.prepare(
            self.context,
            publication,
            bindings=(
                MonitoringRuleBinding(
                    condition_id=price.condition_id,
                    rule_type=MonitoringRuleType.PRICE_ABOVE,
                    threshold=1800.0,
                ),
            ),
        )

        with patch.object(
            self.alerts,
            "_create_monitoring_rule",
            side_effect=RuntimeError("local database unavailable"),
        ):
            failed = self.service.confirm_and_activate(
                self.context,
                proposed.handoff_id,
                candidate_ids=(
                    proposed.rule_candidates[0].candidate_id,
                ),
            )

        self.assertEqual(
            failed.status,
            MonitoringHandoffStatus.REGISTRATION_FAILED,
        )
        self.assertIn(
            "alert_rule_registration_failed",
            failed.gap_codes,
        )
        self.assertEqual(
            publication.report.monitoring_plan.to_dict(),
            report_plan_before,
        )
        self.assertEqual(self.alerts.list_rules(self.context), [])

    def test_rule_stays_inert_if_final_workflow_update_fails(self):
        price = _condition(
            "condition:price",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="price",
        )
        proposed = self.service.prepare(
            self.context,
            self._publication(_plan((price,), review_date=None)),
            bindings=(
                MonitoringRuleBinding(
                    condition_id=price.condition_id,
                    rule_type=MonitoringRuleType.PRICE_BELOW,
                    threshold=1200.0,
                ),
            ),
        )
        original_record = self.workflow.record_monitoring_handoff

        def fail_after_rule_registration(handoff):
            if (
                handoff.status
                is not MonitoringHandoffStatus.ACTIVATION_PENDING
            ):
                raise RuntimeError("workflow persistence unavailable")
            return original_record(handoff)

        with patch.object(
            self.workflow,
            "record_monitoring_handoff",
            side_effect=fail_after_rule_registration,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "workflow persistence",
            ):
                self.service.confirm_and_activate(
                    self.context,
                    proposed.handoff_id,
                    candidate_ids=(
                        proposed.rule_candidates[0].candidate_id,
                    ),
                )

        self.assertEqual(len(self.alerts.list_rules(self.context)), 1)
        self.assertEqual(
            self.alerts.evaluate_price(
                self.context,
                asset_id=self.asset.asset_id,
                price=1100.0,
                observed_at="2026-08-25",
            ),
            [],
        )
        persisted = self.service.get(
            self.context,
            proposed.handoff_id,
        )
        self.assertEqual(
            persisted.status,
            MonitoringHandoffStatus.ACTIVATION_PENDING,
        )
        self.assertEqual(persisted.alert_rule_refs, ())

    def test_retry_reconciles_split_handoff_and_workflow_write(self):
        price = _condition(
            "condition:price",
            MonitoringConditionKind.MACHINE_OBSERVABLE,
            observability_key="price",
        )
        proposed = self.service.prepare(
            self.context,
            self._publication(_plan((price,), review_date=None)),
            bindings=(
                MonitoringRuleBinding(
                    condition_id=price.condition_id,
                    rule_type=MonitoringRuleType.PRICE_BELOW,
                    threshold=1200.0,
                ),
            ),
        )
        original_save = self.workflow_repository.save_workflow_run
        calls = 0

        def fail_second_workflow_write(workflow_run):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("workflow write failed")
            return original_save(workflow_run)

        with patch.object(
            self.workflow_repository,
            "save_workflow_run",
            side_effect=fail_second_workflow_write,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "workflow write failed",
            ):
                self.service.confirm_and_activate(
                    self.context,
                    proposed.handoff_id,
                    candidate_ids=(
                        proposed.rule_candidates[0].candidate_id,
                    ),
                )

        split_handoff = self.workflow.get_monitoring_handoff(
            proposed.handoff_id
        )
        split_workflow = self.workflow.get_workflow_run(
            proposed.workflow_run_id
        )
        self.assertEqual(
            split_handoff.status,
            MonitoringHandoffStatus.ARMED,
        )
        self.assertEqual(
            split_workflow.monitoring_status,
            MonitoringHandoffStatus.ACTIVATION_PENDING,
        )
        self.assertEqual(
            self.alerts.evaluate_price(
                self.context,
                asset_id=self.asset.asset_id,
                price=1100.0,
                observed_at="2026-08-25",
            ),
            [],
        )

        recovered = self.service.confirm_and_activate(
            self.context,
            proposed.handoff_id,
            candidate_ids=(proposed.rule_candidates[0].candidate_id,),
        )

        self.assertEqual(
            self.workflow.get_workflow_run(
                proposed.workflow_run_id
            ).monitoring_status,
            MonitoringHandoffStatus.ARMED,
        )
        self.assertEqual(
            recovered.status,
            MonitoringHandoffStatus.ARMED,
        )
        self.assertEqual(
            len(
                self.alerts.evaluate_price(
                    self.context,
                    asset_id=self.asset.asset_id,
                    price=1100.0,
                    observed_at="2026-08-25",
                )
            ),
            1,
        )

    def test_unpublished_and_cross_user_reports_are_rejected(self):
        publication = self._publication(
            _plan(
                (
                    _condition(
                        "condition:manual",
                        MonitoringConditionKind.MANUAL_REVIEW,
                    ),
                ),
                review_date=None,
            )
        )
        unpublished = SimpleNamespace(
            report=publication.report,
            review=publication.review,
            published=None,
        )
        other = self.users.create_user("Other")
        other_context = self.users.context_for_user(other.user_id)

        with self.assertRaisesRegex(ValueError, "published"):
            self.service.prepare(self.context, unpublished)
        with self.assertRaisesRegex(ValueError, "another user"):
            self.service.prepare(other_context, publication)

    def test_tampered_published_report_is_rejected(self):
        publication = self._publication(
            _plan(
                (
                    _condition(
                        "condition:manual",
                        MonitoringConditionKind.MANUAL_REVIEW,
                    ),
                ),
                review_date=None,
            )
        )
        publication.published.report_path.write_text(
            "tampered",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "digest"):
            self.service.prepare(self.context, publication)

    def test_forged_plan_with_attested_id_is_rejected(self):
        publication = self._publication(
            _plan(
                (
                    _condition(
                        "condition:manual",
                        MonitoringConditionKind.MANUAL_REVIEW,
                    ),
                ),
                review_date=None,
            )
        )
        forged_report = SimpleNamespace(
            **{
                **publication.report.__dict__,
                "monitoring_plan": SimpleNamespace(
                    plan_id=(
                        publication.report.monitoring_plan.plan_id
                    ),
                    conditions=(),
                    review_date=None,
                ),
            }
        )
        forged_publication = SimpleNamespace(
            report=forged_report,
            review=publication.review,
            published=publication.published,
        )

        with self.assertRaisesRegex(ValueError, "canonical"):
            self.service.prepare(
                self.context,
                forged_publication,
            )

    def _publication(self, plan):
        report = SimpleNamespace(
            report_id="final-report:monitoring-test",
            user_id=self.context.user_id,
            workflow_run_id="workflow:monitoring-test",
            decision_brief_id="brief:monitoring-test",
            session_id="session:monitoring-test",
            handoff_id="handoff:monitoring-test",
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            as_of_date="2026-08-24",
            monitoring_plan=plan,
        )
        report_path = Path(self.temp_dir.name) / "final-report.md"
        report_path.write_text(
            f"reviewed final report: {plan.plan_id}\n",
            encoding="utf-8",
        )
        self.workflow.record_final_publication(
            FinalDecisionPublicationAttestation(
                report_id=report.report_id,
                user_id=report.user_id,
                workflow_run_id=report.workflow_run_id,
                session_id=report.session_id,
                decision_brief_id=report.decision_brief_id,
                baseline_handoff_id=report.handoff_id,
                monitoring_plan_id=plan.plan_id,
                asset_id=report.asset_id,
                profile_id=report.profile_id,
                as_of_date=report.as_of_date,
                report_path=str(report_path.resolve()),
                markdown_digest=hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
                monitoring_plan_payload_json=json.dumps(
                    plan.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                judgements_payload_json=json.dumps(
                    PublishedJudgements(
                        views=(
                            View(
                                view_id="view:monitoring-test",
                                subject_type=SubjectType.ASSET,
                                subject_id=report.asset_id,
                                origin=ViewOrigin.SYSTEM,
                                proposition="监测交接测试观点",
                                horizon=ResearchHorizon.LONG,
                                as_of_date=report.as_of_date,
                                expires_on="2029-08-23",
                                confidence=Confidence.LOW,
                            ),
                        ),
                        actions=(),
                    ).to_dict()
                ),
                published_at="2026-08-24T02:00:00+00:00",
            )
        )
        return SimpleNamespace(
            report=report,
            review=ReviewResult(()),
            published=SimpleNamespace(
                report_path=report_path
            ),
        )


def _condition(
    condition_id,
    kind,
    *,
    observability_key=None,
):
    return MonitoringCondition(
        condition_id=condition_id,
        text="该条件成立时重新评估当前结论。",
        kind=kind,
        source="核心论文失效条件",
        observability_key=observability_key,
        review_by="2026-09-30",
        evidence_refs=("evidence:monitoring",),
        limitations=("仅用于研究复查，不触发交易。",),
    )


def _plan(conditions, *, review_date):
    status = (
        MonitoringPlanStatus.USER_CONFIRMATION_REQUIRED
        if conditions
        else MonitoringPlanStatus.UNSUPPORTED
    )
    payload = {
        "plan_id": "",
        "status": status.value,
        "review_date": review_date,
        "conditions": [item.to_dict() for item in conditions],
        "alert_rule_refs": [],
        "limitations": [
            "未经用户确认不会启用任何本地提醒规则。"
        ],
    }
    payload["plan_id"] = monitoring_plan_id(payload)
    return MonitoringPlan.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
