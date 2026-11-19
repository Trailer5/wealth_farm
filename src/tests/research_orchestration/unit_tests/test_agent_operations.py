import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.research_orchestration.container import create_default_container
from src.research_orchestration.cli import main
from src.research_orchestration.agent_protocol import failure
from src.user_space.plan_registry import PlanRevisionConflict
from src.tests.user_space.unit_tests.test_plan_registry import sample_plan


class AgentOperationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.container = create_default_container(db_path=Path(self.directory.name) / "operations.sqlite3")

    def request(self):
        return {"request_id": "business:1", "steps": [
            {"step_id": "first", "kind": "plan_revision", "arguments": {"registration_key": "first", "plan": sample_plan()}},
            {"step_id": "second", "kind": "plan_revision", "arguments": {"registration_key": "second", "plan": sample_plan()}},
        ]}

    def test_preview_has_no_writes_and_completed_requests_are_idempotent(self):
        service = self.container.operations
        preview = service.run(user_id="user:default", request=self.request())
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.container.execution_plans.registry.list_plans(user_id="user:default"), [])
        result = service.run(user_id="user:default", request=self.request(), apply=True)
        self.assertEqual(result["state"], "completed")
        retry = service.run(user_id="user:default", request=self.request(), apply=True)
        self.assertEqual(retry["results"], result["results"])
        changed = self.request()
        changed["steps"][0]["arguments"]["plan"]["title"] = "Different request"
        with self.assertRaises(ValueError):
            service.run(user_id="user:default", request=changed, apply=True)

    def test_partial_failure_resumes_only_the_unfinished_safe_step(self):
        service = self.container.operations
        execute = service._execute

        def sometimes_fail(user_id, step):
            if step["step_id"] == "second":
                raise OSError("temporary storage failure")
            return execute(user_id, step)

        with patch.object(service, "_execute", side_effect=sometimes_fail):
            failed = service.run(user_id="user:default", request=self.request(), apply=True)
        self.assertEqual(failed["state"], "retryable_failure")
        self.assertEqual(len(failed["results"]), 1)
        resumed = service.run(user_id="user:default", request=self.request(), apply=True)
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(resumed["results"][0], failed["results"][0])
        self.assertEqual(len(self.container.execution_plans.registry.list_plans(user_id="user:default")), 2)

    def test_batch_keeps_successes_and_only_retries_failed_items(self):
        seen = []

        def partial(key):
            seen.append(key)
            if key == "bad":
                raise OSError("remote unavailable")
            return {"symbol": key}

        first = self.container.batch.run(items=["good", "bad"], parameters={"end_date": "2026-01-01"},
                                          execute=partial, request_id="prices:1")
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["retry_items"], ["bad"])
        seen.clear()

        def restored(key):
            seen.append(key)
            return {"symbol": key}

        resumed = self.container.batch.run(items=["good", "bad"], parameters={"end_date": "2026-01-01"},
                                           execute=restored, request_id="prices:1", retry_failed=True)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(seen, ["bad"])
        with self.assertRaises(ValueError):
            self.container.batch.run(items=["different"], parameters={}, execute=restored, request_id="prices:1")

    def test_cli_protocol_supports_describe_structured_errors_and_read_only_default(self):
        output, errors = io.StringIO(), io.StringIO()
        code = main(["--describe", "calendar"], stdout=output, stderr=errors,
                    container_factory=lambda **kwargs: self.fail("describe must not initialize services"))
        self.assertEqual(code, 0)
        self.assertIn("limit", [item["name"] for item in json.loads(output.getvalue())["parameters"]])
        output = io.StringIO()
        self.container.routine.settlement.settle_due = lambda day: self.fail("default routine must not write")
        code = main(["routine", "--user-id", "user:default", "--envelope"], stdout=output, stderr=errors,
                    container_factory=lambda **kwargs: self.container)
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["writes"])
        output = io.StringIO()
        code = main(["calendar", "--user-id", "not-found", "--envelope"], stdout=output, stderr=errors,
                    container_factory=lambda **kwargs: self.container)
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "not_found")
        conflict = failure(PlanRevisionConflict("reload"), operation="plan-register")
        self.assertEqual(conflict["error"]["code"], "revision_conflict")
        self.assertFalse(conflict["error"]["retryable"])

    def test_ambiguous_non_idempotent_write_requires_evidence_before_resuming(self):
        debates = self.container.debates.debates
        debate = debates.start(user_id="user:default", topic="test", trigger="test",
                               as_of_date="2026-01-01", significance="routine")
        debates.conclude(debate.debate_id, conclusion="No investment action")
        request = {"request_id": "amend:1", "steps": [{"step_id": "amend", "kind": "debate_amendment", "arguments": {
            "debate_id": debate.debate_id, "reason": "Confirmed", "as_of_date": "2026-01-01",
            "decisions": [{"subject": "test", "action": "avoid", "timing": "now"}],
        }}]}
        service = self.container.operations
        execute = service._execute
        observed = {}

        def lost_reply(user, step):
            observed.update(execute(user, step))
            raise OSError("reply lost after commit")

        with patch.object(service, "_execute", side_effect=lost_reply):
            failed = service.run(user_id="user:default", request=request, apply=True)
        self.assertEqual(failed["state"], "needs_reconciliation")
        retry = service.run(user_id="user:default", request=request, apply=True)
        self.assertEqual(retry["state"], "needs_reconciliation")
        self.assertEqual(len(debates.show(debate.debate_id)["debate"]["amendments"]), 1)
        acknowledged = service.reconcile(user_id="user:default", request_id="amend:1", confirmed_applied=True,
                                         evidence_ref="checked:amendment:1", result=observed)
        self.assertEqual(acknowledged["state"], "completed")
        self.assertEqual(len(acknowledged["reconciliations"]), 1)

    def test_scoped_plan_patch_preserves_other_nodes_and_retries_safely(self):
        plan = sample_plan()
        plan["tasks"].append(dict(plan["tasks"][0], task_id="untouched"))
        added = self.container.execution_plans.register(user_id="user:default", registration_key="patch", plan=plan)
        request = {"request_id": "patch:1", "steps": [{"step_id": "confirm", "kind": "plan_patch", "arguments": {
            "plan_id": added["plan_id"], "expected_revision": 1,
            "changes": {"reason": "User confirmed", "constraints": ["Confirmed scoped exception"]},
            "task_changes": {"check": {"instruction": "Use the confirmed rule"}},
        }}]}
        preview = self.container.operations.run(user_id="user:default", request=request)
        self.assertEqual(len(preview["steps"][0]["after"]["tasks"]), 2)
        self.container.operations.run(user_id="user:default", request=request, apply=True)
        current = self.container.execution_plans.show(user_id="user:default", plan_id=added["plan_id"])
        self.assertEqual(current["plan"]["tasks"][1], preview["steps"][0]["before"]["tasks"][1])
        retry = self.container.operations.run(user_id="user:default", request=request, apply=True)
        self.assertEqual(retry["state"], "completed")
        self.assertEqual(current["revision"], 2)


if __name__ == "__main__":
    unittest.main()