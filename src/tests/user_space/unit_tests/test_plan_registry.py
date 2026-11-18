import unittest
from datetime import datetime
from unittest.mock import patch

from src.user_space.plan_registry import PlanRegistryService, SQLitePlanRegistryRepository
from src.user_space.plan_schedule import normalize_schedule, occurrences


class PlanScheduleTest(unittest.TestCase):
    def test_window_keeps_original_deadline(self):
        schedule = normalize_schedule({"start": "2026-11-18", "deadline": "2026-11-30"})
        self.assertEqual(occurrences(schedule, "2026-11-17"), [])
        rows = occurrences(schedule, "2026-12-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["occurrence"], "2026-11-18")
        self.assertEqual(rows[0]["deadline_at"], "2026-11-30T15:59:59.999999+00:00")

    def test_each_repetition_has_a_stable_independent_identity(self):
        schedule = normalize_schedule({
            "start": "2026-11-18", "repeat_days": 7, "until": "2026-12-02",
        })
        self.assertEqual([row["occurrence"] for row in occurrences(schedule, "2027-01-01")], [
            "2026-11-18", "2026-11-25", "2026-12-02",
        ])

    def test_exact_time_and_dst_preserve_market_clock(self):
        schedule = normalize_schedule({
            "start": "2026-10-30T16:00:00-04:00", "timezone": "America/New_York",
            "repeat_days": 7, "until": "2026-11-06",
        })
        self.assertEqual(occurrences(schedule, "2026-10-30T19:59:00Z"), [])
        rows = occurrences(schedule, "2026-11-07")
        self.assertEqual(rows[0]["ready_at"], "2026-10-30T20:00:00+00:00")
        self.assertEqual(rows[1]["ready_at"], "2026-11-06T21:00:00+00:00")

    def test_invalid_or_unbounded_schedules_are_rejected(self):
        for schedule in (
            {"start": "2026-11-18T16:00:00"},
            {"start": "2026-11-18", "deadline": "2026-11-17"},
            {"start": "2026-11-18", "repeat_days": 0, "until": "2027-01-01"},
            {"start": "2026-11-18", "repeat_days": 1},
            {"start": "2026-11-18T16:00:00-04:00", "timezone": "America/New_York"},
        ):
            with self.subTest(schedule=schedule), self.assertRaises((ValueError, KeyError)):
                normalize_schedule(schedule)

    def test_dst_hazards_are_rejected_at_registration_not_calendar_time(self):
        for start, until in (("2026-10-25T01:30:00-04:00", "2026-11-02"),
                             ("2027-03-07T02:30:00-05:00", "2027-03-15")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                normalize_schedule({"start": start, "timezone": "America/New_York",
                                    "repeat_days": 7, "until": until})
        schedule = normalize_schedule({"start": "9999-12-30", "repeat_days": 7, "until": "9999-12-31"})
        self.assertEqual(len(occurrences(schedule, "9999-12-31")), 1)


def sample_plan():
    return {
        "title": "Capital plan", "objective": "Check before acting", "status": "active",
        "source_refs": ["report:test"], "reason": "User confirmed",
        "tasks": [{"task_id": "check", "title": "Check", "instruction": "Read evidence",
                   "completion": "Record a conclusion", "schedule": {"start": "2026-11-18"}}],
    }


class PlanRegistryTest(unittest.TestCase):
    def setUp(self):
        self.now = "2026-11-18T10:00:00+00:00"
        self.service = PlanRegistryService(SQLitePlanRegistryRepository(":memory:"),
                                           clock=lambda: datetime.fromisoformat(self.now))
        self.plan = sample_plan()
        self.registered = self.service.register(user_id="u", registration_key="capital", plan=self.plan)
        self.plan_id = self.registered["plan_id"]

    def resolve(self, **overrides):
        args = dict(user_id="u", plan_id=self.plan_id, task_id="check", occurrence="2026-11-18",
                    expected_revision=1, request_id="r1", outcome="completed", note="Checked",
                    evidence_refs=["evidence:1"])
        return self.service.resolve(**(args | overrides))

    def test_registration_handling_and_retries_are_idempotent(self):
        repeated = self.service.register(user_id="u", registration_key="capital", plan=self.plan)
        self.assertEqual(repeated["plan_id"], self.plan_id)
        self.assertFalse(repeated["changed"])
        self.assertEqual(len(self.service.calendar(user_id="u", at="2026-11-18")["due"]), 1)
        self.resolve()
        self.assertFalse(self.resolve()["changed"])
        self.assertEqual(self.service.calendar(user_id="u", at="2026-11-20")["counts"]["overdue"], 0)
        self.assertEqual(len(self.service.show(user_id="u", plan_id=self.plan_id)["records"]), 1)
        with self.assertRaises(ValueError):
            self.resolve(note="Changed retry")

    def test_overdue_and_deferred_keep_original_deadline(self):
        self.now = "2026-11-20T10:00:00+00:00"
        self.assertEqual(self.service.calendar(user_id="u", at=self.now)["counts"]["overdue"], 1)
        self.resolve(outcome="deferred", next_check="2026-11-23T09:00:00+08:00")
        self.assertEqual(self.service.calendar(user_id="u", at=self.now)["counts"]["overdue"], 0)
        later = self.service.calendar(user_id="u", at=self.now, through="2026-11-24")["upcoming"][0]
        self.assertTrue(later["overdue"])
        self.assertIn("2026-11-18", later["deadline_at"])

    def test_periods_are_independent_and_future_completion_is_rejected(self):
        self.plan["tasks"][0]["schedule"].update(repeat_days=7, until="2026-12-02")
        self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)
        self.resolve(expected_revision=2)
        with self.assertRaises(ValueError):
            self.resolve(expected_revision=2, occurrence="2026-11-25", request_id="future")
        rows = self.service.calendar(user_id="u", at=self.now, through="2026-11-25")["upcoming"]
        self.assertEqual([row["occurrence"] for row in rows], ["2026-11-25"])

    def test_revisions_history_and_user_isolation(self):
        self.now = "2026-11-19T10:00:00+00:00"
        self.plan["status"] = "cancelled"
        with self.assertRaises(ValueError):
            self.service.register(user_id="u", registration_key="capital", plan=self.plan)
        self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)
        old = self.service.calendar(user_id="u", at="2026-11-18", known_at="2026-11-18T12:00:00Z")
        self.assertEqual(old["counts"]["due"], 1)
        self.assertEqual(self.service.calendar(user_id="u", at=self.now)["counts"]["overdue"], 0)
        self.assertEqual(self.service.calendar(user_id="other", at=self.now)["counts"]["overdue"], 0)
        with self.assertRaises(ValueError):
            self.service.show(user_id="other", plan_id=self.plan_id)

    def test_handled_tasks_are_immutable_but_history_survives_removal(self):
        self.resolve()
        self.plan["tasks"][0]["instruction"] = "A different commitment"
        with self.assertRaises(ValueError):
            self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)
        self.plan["tasks"] = []
        self.plan["status"] = "completed"
        self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)
        review = self.service.review(user_id="u", plan_id=self.plan_id, at=self.now)
        self.assertEqual(review["handling_counts"]["completed"], 1)
        self.assertEqual(len(review["revisions"]), 2)

    def test_actions_need_execution_confirmation_and_dependencies(self):
        action = dict(self.plan["tasks"][0], task_id="buy", kind="action", depends_on=["check"])
        self.plan["tasks"].append(action)
        self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)
        calendar = self.service.calendar(user_id="u", at=self.now)
        self.assertEqual(calendar["blocked"][0]["blocked_by"], ["check"])
        with self.assertRaises(ValueError):
            self.resolve(task_id="buy", expected_revision=2, outcome="executed", execution_ref="user:confirmed")
        self.resolve(expected_revision=2)
        with self.assertRaises(ValueError):
            self.resolve(task_id="buy", expected_revision=2, request_id="buy")
        self.resolve(task_id="buy", expected_revision=2, request_id="buy", outcome="executed", execution_ref="trade:1")
        self.assertEqual(self.service.calendar(user_id="u", at=self.now)["counts"]["due"], 0)

    def test_unavailable_evidence_requires_a_next_check_not_a_false_result(self):
        with self.assertRaises(ValueError):
            self.resolve(outcome="blocked")
        self.resolve(outcome="blocked", next_check="2026-11-19T10:00:00Z")
        self.assertEqual(self.service.calendar(user_id="u", at="2026-11-20")["counts"]["blocked"], 1)

    def test_review_distinguishes_delays_from_judgement_results(self):
        self.resolve(outcome="deferred", next_check="2026-11-19T10:00:00Z")
        self.now = "2026-11-20T10:00:00+00:00"
        self.resolve(request_id="finished")
        review = self.service.review(user_id="u", plan_id=self.plan_id, at=self.now)
        self.assertEqual(review["execution_summary"], {"deferrals": 1, "blocked_checks": 0, "late_closures": 1})

    def test_invalid_queries_and_cyclic_dependencies_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.calendar(user_id="empty", at="not-a-date")
        self.plan["tasks"][0]["depends_on"] = ["check"]
        with self.assertRaises(ValueError):
            self.service.register(user_id="u", registration_key="cycle", plan=self.plan)

    def test_calendar_keeps_plan_constraints_and_isolates_bad_schedules(self):
        self.plan["constraints"] = ["All equities including employer stock must remain below the cap"]
        self.plan["uncertainties"] = ["Pending decision must not be inferred from old text"]
        self.plan["tasks"][0]["source_excerpt"] = "No trailing stop: stop adding, do not sell."
        second = dict(self.plan["tasks"][0], task_id="valid", schedule={"start": "2026-11-17"})
        self.plan["tasks"].append(second)
        self.service.register(user_id="u", registration_key="capital", plan=self.plan, expected_revision=1)

        def expand(schedule, through):
            if schedule["start"] == "2026-11-18":
                raise ValueError("a stored schedule cannot be expanded")
            return occurrences(schedule, through)

        with patch("src.user_space.plan_registry.occurrences", side_effect=expand):
            calendar = self.service.calendar(user_id="u", at=self.now)
            review = self.service.review(user_id="u", plan_id=self.plan_id, at=self.now)
        self.assertEqual(len(calendar["gaps"]), 1)
        self.assertEqual(calendar["overdue"][0]["task_id"], "valid")
        self.assertEqual(calendar["overdue"][0]["plan_constraints"], self.plan["constraints"])
        self.assertEqual(calendar["overdue"][0]["plan_uncertainties"], self.plan["uncertainties"])
        self.assertTrue(review["gaps"])
        shown = self.service.show(user_id="u", plan_id=self.plan_id)
        self.assertEqual(shown["plan"]["tasks"][0]["source_excerpt"],
                 self.plan["tasks"][0]["source_excerpt"])


if __name__ == "__main__":
    unittest.main()