import unittest

from src.research_ledger import due_review_nodes, summarize_decisions, timing_contribution


def _settlement(checkpoint, result, *, asset=None, excess=None, alternative=None, status="settled", note=None):
    return {
        "checkpoint_date": checkpoint,
        "status": status,
        "claim_result": result,
        "asset_return": None if asset is None else str(asset),
        "excess_return": None if excess is None else str(excess),
        "alternative_return": None if alternative is None else str(alternative),
        "note": note,
    }


def _entry(entry_id, kind, artifact, settlements, *, source="final-report:r1", superseded=False, until="2026-06-30"):
    return {
        "entry_id": entry_id,
        "entry_type": kind,
        "scope": "personal",
        "subject_id": "asset:a",
        "source_ref": source,
        "as_of_date": "2026-01-02",
        "effective_until": until,
        "superseded": superseded,
        "artifact": artifact,
        "settlements": settlements,
    }


def _action(action_type, kind="scheduled"):
    return {"action_type": action_type, "horizon": "short", "trigger": {"kind": kind}}


def _view(origin, low=None, high=None):
    return {
        "origin": origin,
        "horizon": "medium_short",
        "proposition": "甲跑赢沪深300",
        "probability": None if low is None else {"low": low, "high": high},
    }


ENTRIES = [
    _entry("a1", "action", _action("buy"), [_settlement("2026-02-01", "met", asset=0.05, excess=0.02)]),
    _entry(
        "a2",
        "action",
        _action("exit"),
        [
            _settlement("2026-02-01", "met", asset=-0.03, excess=-0.01),
            _settlement("2026-04-01", "met", asset=-0.10, excess=-0.04),
        ],
        source="exit_plan:p1",
    ),
    _entry(
        "a3",
        "action",
        _action("add", kind="conditional"),
        [_settlement("2026-03-01", "unknown", status="not_settleable", note="条件在 2026-01-02 至 2026-03-01 内未出现，动作未触发")],
        source="portfolio_plan:x",
    ),
    _entry("v1", "view", _view("system", "0.6", "0.7"), [_settlement("2026-04-01", "met", asset=0.08, excess=0.03)]),
    _entry("v2", "view", _view("user"), [_settlement("2026-04-01", "missed", asset=-0.02, excess=-0.05)], source="request:q"),
    _entry("v3", "view", _view("user"), [_settlement("2026-04-01", "met", asset=0.1, excess=0.1)], superseded=True),
    _entry("v4", "view", _view("system"), []),
    {
        **_entry("p1", "plan", {"review_nodes": [{"review_on": "2026-02-15", "focus": "第 1 步结束"}, {"review_on": "2026-12-31", "focus": "年度"}]}, []),
        "source_ref": "portfolio_plan:x",
    },
]


class DecisionReviewTest(unittest.TestCase):
    def test_hit_rates_timing_and_status_counts(self):
        result = summarize_decisions(ENTRIES, as_of_date="2026-05-01", labels={"asset:a": "甲"})

        actions = result["actions"]["overall"]
        self.assertEqual((actions["directional"], actions["met"]), (2, 2))
        self.assertTrue(actions["small_sample"])
        # Buy: +5% against cash; exit at the latest checkpoint: avoided a 10% fall.
        self.assertAlmostEqual(actions["timing_contribution_mean"], 0.075)
        self.assertEqual(actions["timing_contribution_positive"], 2)
        exit_row = next(row for row in result["action_items"] if row["entry_id"] == "a2")
        self.assertEqual(exit_row["checkpoint_date"], "2026-04-01")
        self.assertEqual(exit_row["checkpoints_settled"], 2)
        self.assertTrue(exit_row["interim"])
        self.assertEqual(
            {row["group"] for row in result["actions"]["by_source"]},
            {"单标的最终报告", "退出计划"},
        )

        views = result["views"]
        self.assertEqual((views["overall"]["directional"], views["overall"]["met"]), (2, 1))
        by_origin = {row["group"]: row for row in views["by_origin"]}
        self.assertEqual(by_origin["系统观点"]["stated_probability_mean"], 0.65)
        self.assertEqual(by_origin["系统观点"]["hit_rate_with_probability"], 1.0)
        self.assertEqual(by_origin["用户观点"]["hit_rate"], 0.0)
        low, high = views["overall"]["hit_rate_interval"]
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)

        self.assertEqual(
            result["status_counts"],
            {"settled_action": 2, "not_triggered": 1, "settled_view": 2, "superseded": 1, "pending": 1},
        )

    def test_timing_contribution_uses_alternative_when_switching(self):
        self.assertAlmostEqual(
            timing_contribution("exit", {"asset_return": "-0.02", "alternative_return": "0.03"}), 0.05
        )
        self.assertIsNone(timing_contribution("hold", {"asset_return": "0.02"}))

    def test_due_and_upcoming_review_nodes(self):
        nodes = due_review_nodes(ENTRIES, as_of_date="2026-05-01")

        self.assertEqual([item["review_on"] for item in nodes["due"]], ["2026-02-15"])
        self.assertEqual([item["review_on"] for item in nodes["upcoming"]], ["2026-12-31"])


if __name__ == "__main__":
    unittest.main()
