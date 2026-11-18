import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.research_orchestration.cli import main
from src.research_orchestration.container import create_default_container
from src.user_space.plan_registry import PlanRegistryService, SQLitePlanRegistryRepository
from src.tests.user_space.unit_tests.test_plan_registry import sample_plan


class ExecutionPlanCLITest(unittest.TestCase):
    def test_offline_cli_round_trip_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            container = create_default_container(db_path=database, report_root=Path(directory) / "report")
            service = container.execution_plans
            registry = service.registry
            registry.clock = lambda: datetime.fromisoformat("2026-11-18T10:00:00+00:00")

            def invoke(command, *args):
                output, errors = io.StringIO(), io.StringIO()
                code = main([command, "--user-id", "user:default", *args],
                    container_factory=lambda **kwargs: container, stdout=output, stderr=errors)
                self.assertEqual(code, 0, errors.getvalue())
                return json.loads(output.getvalue())

            source = Path(directory) / "plan.json"
            source.write_text(json.dumps(sample_plan()), encoding="utf-8")
            added = invoke("plan-register", "--key", "capital", "--json", str(source))
            repeated = invoke("plan-register", "--key", "capital", "--json", str(source))
            self.assertFalse(repeated["changed"])
            self.assertEqual(added["plan_id"], repeated["plan_id"])
            calendar = invoke("calendar", "--at", "2026-11-18")
            self.assertEqual(calendar["counts"]["due"], 1)
            self.assertTrue(calendar["next_actions"])
            shown = invoke("plan-show", "--plan-id", added["plan_id"])
            self.assertEqual(shown["revision"], 1)

            handling = Path(directory) / "handling.json"
            handling.write_text(json.dumps({
                "plan_id": added["plan_id"], "task_id": "check", "occurrence": "2026-11-18",
                "expected_revision": 1, "request_id": "checked", "outcome": "completed",
                "note": "Verified", "evidence_refs": ["evidence:test"],
            }), encoding="utf-8")
            invoke("task-resolve", "--json", str(handling))
            self.assertEqual(invoke("calendar", "--at", "2026-11-19")["counts"]["overdue"], 0)
            review = invoke("plan-review", "--plan-id", added["plan_id"], "--at", "2026-11-19")
            self.assertEqual(review["handling_counts"]["completed"], 1)
            reloaded = PlanRegistryService(SQLitePlanRegistryRepository(database), clock=registry.clock)
            self.assertEqual(len(reloaded.show(user_id="user:default", plan_id=added["plan_id"])["records"]), 1)
            with self.assertRaises(KeyError):
                service.calendar(user_id="unknown", at="2026-11-18")


if __name__ == "__main__":
    unittest.main()