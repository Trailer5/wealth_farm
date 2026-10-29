import copy
import unittest

from src.research_visualization import check_plan_html, render_plan_html


def _payload():
    plan = {
        "plan_id": "plan:user:a:2026-10-28:abc",
        "user_id": "user:a",
        "as_of_date": "2026-10-28",
        "version": 1,
        "actions": [{"action_id": "act-1"}, {"action_id": "act-2"}],
        "review_nodes": [{"review_on": "2026-11-11", "focus": "第 1 步结束"}],
        "targets": [
            {
                "subject_id": "asset:a",
                "metric_key": "portfolio_weight",
                "low": "0.12",
                "high": "0.14",
                "window_start": "2026-12-09",
                "window_end": "2027-03-09",
                "basis": "目标权重",
                "probability": None,
            }
        ],
        "conflicts": ["先卖后买"],
        "excluded_actions": ["<script>alert(1)</script> 被排除"],
    }
    node = {
        "date": "2026-10-29",
        "valid_until": "2026-11-11",
        "kind": "scheduled",
        "label": "茅台",
        "action": "减仓",
        "quantity": "约 1 元",
        "condition": None,
        "source": "本计划",
    }
    return {
        "plan": plan,
        "labels": {"asset:a": "茅台"},
        "nodes": [
            {**node, "action_id": "act-1"},
            {**node, "action_id": "act-2", "kind": "conditional", "date": "2026-11-12", "valid_until": "2026-11-25", "action": "加仓"},
        ],
        "recorded": False,
        "before": {"equity": 0.6, "volatility": 0.13, "max_stock": {"weight": 0.34}, "max_industry": None,
                   "asset_classes": [{"label": "权益", "weight": 0.6}], "stress": [{"label": "2015", "portfolio_return": -0.2}]},
        "after": {"equity": 0.6, "volatility": 0.13, "max_stock": {"weight": 0.15}, "max_industry": None,
                  "asset_classes": [{"label": "权益", "weight": 0.6}], "stress": [{"label": "2015", "portfolio_return": -0.25}]},
    }


class PlanHtmlTest(unittest.TestCase):
    def test_page_is_offline_escaped_and_matches_its_plan(self):
        payload = _payload()
        page = render_plan_html(payload)

        self.assertEqual(check_plan_html(page, payload["plan"]), [])
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertIn("12.0%–14.0%", page)

    def test_changed_plan_is_detected(self):
        payload = _payload()
        page = render_plan_html(payload)
        changed = copy.deepcopy(payload["plan"])
        changed["actions"].append({"action_id": "act-3"})
        changed["review_nodes"].append({"review_on": "2027-01-27", "focus": "季度复盘"})

        problems = check_plan_html(page, changed)

        self.assertTrue(any("内嵌计划" in item for item in problems))
        self.assertTrue(any("节点表" in item for item in problems))
        self.assertTrue(any("2027-01-27" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
