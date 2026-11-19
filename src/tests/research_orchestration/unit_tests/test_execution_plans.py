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
from src.research_orchestration.research_rules import rule_record


class ExecutionPlanCLITest(unittest.TestCase):
    def test_compact_calendar_deduplicates_context_and_supports_details_and_paging(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_default_container(db_path=Path(directory) / "compact.sqlite3")
            service = container.execution_plans
            plan = sample_plan()
            plan["constraints"] = ["A long shared constraint " * 100]
            plan["tasks"] = [dict(plan["tasks"][0], task_id=f"check-{number}") for number in range(20)]
            added = service.register(user_id="user:default", registration_key="compact", plan=plan)
            compact = service.calendar(user_id="user:default", at="2026-11-18")
            details = service.calendar(user_id="user:default", at="2026-11-18", details=True)
            self.assertLess(len(json.dumps(compact)), len(json.dumps(details)) // 2)
            self.assertEqual(len(compact["plans"]), 1)
            self.assertNotIn("plan_constraints", compact["due"][0])
            page = service.calendar(user_id="user:default", at="2026-11-18", limit=3, offset=3, fields=["title"])
            self.assertEqual(len(page["due"]), 3)
            self.assertEqual(page["page"]["next_offset"], 6)
            self.assertEqual(page["next_actions"][0]["operation"], "plan-show")
            node = service.show(user_id="user:default", plan_id=added["plan_id"], task_id="check-3", history=False)
            self.assertEqual(len(node["plan"]["tasks"]), 1)
            self.assertNotIn("revisions", node)

    def test_linked_rule_changes_block_old_instructions_and_expose_missing_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_default_container(db_path=Path(directory) / "rules.sqlite3")
            service = container.execution_plans
            service.registry.clock = lambda: datetime.fromisoformat("2026-11-18T10:00:00+00:00")
            rules = [rule_record("debate:test:s1", "debate:test", "signpost", {"trigger": "exact condition", "action": "review"}),
                     rule_record("debate:test:s2", "debate:test", "signpost", {"trigger": "other condition", "action": "hold"})]
            service.rule_source = lambda user: rules
            plan = sample_plan()
            plan["source_refs"].append("debate:test")
            plan["tasks"][0]["rule_refs"] = [{key: rules[0][key] for key in ("rule_id", "content_hash")}]
            added = service.register(user_id="user:default", registration_key="linked", plan=plan)
            shown = service.show(user_id="user:default", plan_id=added["plan_id"])
            self.assertEqual(shown["rule_audit"]["uncovered_rules"][0]["rule_id"], "debate:test:s2")
            rules[0] = rule_record("debate:test:s1", "debate:test", "signpost", {"trigger": "new condition", "action": "review"})
            calendar = service.calendar(user_id="user:default", at="2026-11-18")
            self.assertEqual(calendar["blocked"][0]["rule_state"], "stale")
            self.assertEqual(calendar["counts"]["due"], 0)
            with self.assertRaises(ValueError):
                service.resolve(user_id="user:default", plan_id=added["plan_id"], task_id="check",
                    occurrence="2026-11-18", expected_revision=1, request_id="bad", outcome="completed",
                    note="cannot use old rule", evidence_refs=["evidence:test"])

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