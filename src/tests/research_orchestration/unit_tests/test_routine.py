import unittest
from types import SimpleNamespace

from src.research_orchestration.routine import RoutineService
from src.research_orchestration.thesis import DEVIATING, ON_TRACK

AS_OF = "2026-10-29"


class _Settlement:
    def __init__(self, appended=("settlement:1",)):
        self.appended = appended

    def settle_due(self, as_of_date):
        return SimpleNamespace(
            to_dict=lambda: {
                "as_of_date": as_of_date,
                "appended_settlement_ids": list(self.appended),
                "status_counts": {"settled_view": len(self.appended)},
            }
        )


class _Archive:
    def __init__(self, label, fail=False):
        self.label = label
        self.fail = fail
        self.calls = []

    def run(self, as_of_date, *, allow_remote=True):
        self.calls.append((as_of_date, allow_remote))
        if self.fail:
            raise RuntimeError("source down")
        return {
            "assessment": {"label": self.label, "confidence": "medium", "change_signals": ["x"]},
            "ledger_entry_id": f"ledger:{self.label}",
            "gaps": [],
        }


class _Ledger:
    def list_entries(self, **kwargs):
        return [
            SimpleNamespace(artifact=SimpleNamespace(asset_id="asset:a")),
            SimpleNamespace(artifact=SimpleNamespace(asset_id="asset:b")),
        ]


class _Thesis:
    STATUS = {"asset:a": (ON_TRACK, "按预期"), "asset:b": (DEVIATING, "偏离")}

    def check(self, asset_id, *, as_of_date, user_id):
        status, label = self.STATUS[asset_id]
        return {
            "theses": [
                {"thesis_entry_id": f"thesis:{asset_id}", "summary": "s", "status": status, "status_label": label}
            ]
        }


class _Review:
    def __init__(self, due=()):
        self.due = list(due)
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return {"review_nodes": {"due": self.due, "upcoming": []}}


class _Master:
    def get_asset(self, asset_id):
        return SimpleNamespace(display_name={"asset:a": "甲", "asset:b": "乙"}[asset_id])


def _service(market=None, macro=None, review=None, settlement=None, cooldowns=None):
    return RoutineService(
        settlement=settlement or _Settlement(),
        market=market or _Archive("震荡"),
        macro=macro or _Archive("温和"),
        ledger=_Ledger(),
        thesis=_Thesis(),
        decision_review=review or _Review(),
        master_data=_Master(),
        cooldowns=cooldowns or (lambda: {}),
    )


class RoutineServiceTest(unittest.TestCase):
    def test_runs_all_steps_and_points_to_follow_ups(self):
        review = _Review(due=[{"plan_entry_id": "plan:1", "review_on": "2026-10-01", "focus": "f"}])
        payload = _service(review=review).run(user_id="user:u", as_of_date=AS_OF)
        self.assertEqual(payload["gaps"], [])
        self.assertEqual(payload["steps"]["market"]["label"], "震荡")
        self.assertEqual([row["status"] for row in payload["steps"]["theses"]], [ON_TRACK, DEVIATING])
        self.assertFalse(review.kwargs["calibrate"])
        self.assertFalse(review.kwargs["write_report"])
        commands = [item["command"] for item in payload["next_actions"]]
        self.assertTrue(any("exit-plan" in item and "asset:b" in item for item in commands))
        self.assertTrue(any("decision-review" in item for item in commands))
        self.assertTrue(any("乙偏离" in line for line in payload["summary"]))

    def test_failed_step_is_isolated(self):
        payload = _service(market=_Archive("x", fail=True)).run(user_id="user:u", as_of_date=AS_OF)
        self.assertIsNone(payload["steps"]["market"])
        self.assertEqual(payload["steps"]["macro"]["label"], "温和")
        self.assertEqual(len(payload["gaps"]), 1)
        self.assertIn("市场档案失败", payload["gaps"][0])
        self.assertIsNotNone(payload["steps"]["theses"])

    def test_source_cooldowns_are_reported(self):
        cooldowns = {"baostock": {"until": "2026-10-29 20:00:00", "reason": "黑名单"}}
        payload = _service(cooldowns=lambda: cooldowns).run(user_id="user:u", as_of_date=AS_OF)
        self.assertEqual(payload["source_cooldowns"], cooldowns)
        self.assertTrue(any("baostock" in gap for gap in payload["gaps"]))

    def test_skip_archives_and_no_remote(self):
        market = _Archive("震荡")
        payload = _service(market=market).run(user_id="user:u", as_of_date=AS_OF, archives=False, allow_remote=False)
        self.assertNotIn("market", payload["steps"])
        self.assertEqual(market.calls, [])

    def test_new_settlements_suggest_review_when_nothing_else_is_due(self):
        class _QuietThesis(_Thesis):
            STATUS = {"asset:a": (ON_TRACK, "按预期"), "asset:b": (ON_TRACK, "按预期")}

        service = _service()
        service.thesis = _QuietThesis()
        payload = service.run(user_id="user:u", as_of_date=AS_OF)
        self.assertEqual(len(payload["next_actions"]), 1)
        self.assertIn("decision-review", payload["next_actions"][0]["command"])


if __name__ == "__main__":
    unittest.main()
