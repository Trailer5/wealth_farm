import math
import unittest
from datetime import date, timedelta
from decimal import Decimal

from src.portfolio_risk import (
    DiagnosisPosition,
    PreferenceHorizon,
    RiskProfile,
    StressScenario,
    exit_line_args,
    exposure_summary,
    fund_lockup_days,
    initial_preference,
    preference_check,
    risk_summary,
    stress_summary,
    weights_of,
)

AS_OF = "2026-10-28"


def _levels(start: str, days: int, shock) -> dict[str, float]:
    level, result = 100.0, {}
    day = date.fromisoformat(start)
    for index in range(days):
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        level *= 1 + shock(index)
        result[day.isoformat()] = level
    return result


def _market(index: int) -> float:
    return 0.012 * math.sin(index * 1.7) + 0.004 * math.cos(index * 0.31)


def _idiosyncratic(index: int) -> float:
    return 0.009 * math.sin(index * 2.9 + 1.0)


class ExposureTest(unittest.TestCase):
    def test_fund_look_through_overlaps_with_direct_stock(self):
        positions = [
            DiagnosisPosition(
                "stock:a", "A（600000）", "stock", 50.0, {"equity": 1.0},
                industries={"银行": 1.0}, stocks={"600000": ("A", 1.0)},
            ),
            DiagnosisPosition(
                "fund:f", "F（000001）", "open_fund", 50.0, {"equity": 0.8, "bond": 0.2},
                industries={"银行": 0.1, "未穿透": 0.7},
                stocks={"600000": ("A", 0.1), "600001": ("B", 0.05)},
                cross_check={"金融业": 0.3, "制造业": 0.5},
            ),
        ]

        summary = exposure_summary(positions)

        self.assertEqual(summary["csrc_industries"][0], {"industry": "制造业", "weight": 0.25})

        classes = {item["class"]: item["weight"] for item in summary["asset_classes"]}
        self.assertEqual(classes, {"equity": 0.9, "bond": 0.1})
        industries = {item["industry"]: item["weight"] for item in summary["industries"]}
        self.assertAlmostEqual(industries["银行"], 0.55)
        self.assertEqual(summary["uncovered_equity"], 0.35)
        (overlap,) = summary["overlaps"]
        self.assertEqual(overlap["name"], "A")
        self.assertAlmostEqual(overlap["weight"], 0.55)


class RiskAndStressTest(unittest.TestCase):
    def setUp(self):
        self.market = _levels("2014-01-01", 4700, _market)
        self.levels = {
            "a": self.market,
            "b": _levels("2014-01-01", 4700, lambda i: _market(i) * 0.5 + _idiosyncratic(i)),
            "young": _levels("2026-07-01", 119, _market),
            "cash": {},
        }
        self.weights = {"a": 0.4, "b": 0.3, "young": 0.1, "cash": 0.2}

    def test_risk_shares_sum_to_one_and_proxy_short_history(self):
        risk = risk_summary(
            self.weights,
            self.levels,
            as_of_date=AS_OF,
            proxy_levels=self.market,
            proxy_eligible=frozenset({"a", "b", "young"}),
            zero_variance=frozenset({"cash"}),
        )

        shares = {item["asset_id"]: item["risk_share"] for item in risk["risk_contributions"]}
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=3)
        self.assertEqual(shares["cash"], 0.0)
        self.assertEqual(risk["proxied"], ["young"])
        self.assertEqual(risk["excluded"], [])
        self.assertIn({"pair": ["a", "young"], "correlation": 1.0}, risk["high_correlations"])
        self.assertGreater(risk["portfolio_volatility"], 0)

    def test_stress_replays_history_and_proxies_with_beta(self):
        scenario = StressScenario("s", "测试", "2015-06-08", "2015-08-26")
        levels = {**self.levels, "young": _levels("2025-06-01", 500, _market)}

        (result,) = stress_summary(
            self.weights,
            levels,
            as_of_date=AS_OF,
            benchmark_levels=self.market,
            zero_return=frozenset({"cash"}),
            scenarios=(scenario,),
        )

        rows = {item["label"]: item for item in result["positions"]}
        self.assertEqual(rows["a"]["method"], "history")
        self.assertAlmostEqual(rows["a"]["return"], result["benchmark_return"], places=4)
        self.assertEqual(rows["young"]["method"], "beta_proxy")
        self.assertEqual(rows["cash"]["return"], 0.0)
        self.assertEqual(result["uncovered_weight"], 0.0)

    def test_exit_line_sells_risk_assets_at_the_first_close_past_it(self):
        scenario = StressScenario("s", "测试", "2015-06-08", "2015-06-12")
        falling = {"2015-06-08": 100.0, "2015-06-09": 95.0, "2015-06-10": 88.0, "2015-06-11": 70.0, "2015-06-12": 60.0}
        bond = {"2015-06-08": 100.0, "2015-06-12": 101.0}
        weights = {"stock": 0.5, "bond": 0.3, "cash": 0.2}

        (result,) = stress_summary(
            weights,
            {"stock": falling, "bond": bond, "cash": {}},
            as_of_date=AS_OF,
            benchmark_levels=falling,
            zero_return=frozenset({"cash"}),
            scenarios=(scenario,),
            exit_drawdown=0.10,
            risk_assets=frozenset({"stock"}),
        )

        self.assertAlmostEqual(result["portfolio_return"], 0.5 * -0.40 + 0.3 * 0.01, places=4)
        self.assertEqual(result["exit_day"], "2015-06-10")
        self.assertAlmostEqual(result["managed_return"], 0.5 * -0.12 + 0.3 * 0.01, places=4)

        preference = initial_preference(
            user_id="user:a",
            recorded_on=AS_OF,
            risk_profile=RiskProfile.CONSERVATIVE,
            horizon=PreferenceHorizon.ONE_TO_3Y,
            user_statement="最大回撤 10%，会及早收手",
            overrides={"exit_drawdown": "0.10"},
        )
        positions = [
            DiagnosisPosition("stock", "S", "stock", 50.0, {"equity": 1.0}),
            DiagnosisPosition("bond", "B", "open_fund", 30.0, {"bond": 1.0}),
            DiagnosisPosition("cash", "现金", "cash", 20.0, {"cash": 1.0}),
        ]
        checks = {
            item["check_id"]: item
            for item in preference_check(exposure_summary(positions), {"portfolio_volatility": 0.05}, [result], preference)
        }
        self.assertAlmostEqual(checks["stress_loss"]["value"], 0.057, places=3)
        self.assertEqual(checks["stress_loss"]["status"], "ok")
        self.assertIn("收手", checks["stress_loss"]["detail"])
        self.assertEqual(exit_line_args(preference, positions)["risk_assets"], frozenset({"stock"}))

    def test_lockup_from_fund_names_against_the_preference(self):
        self.assertEqual(fund_lockup_days("鹏华年年红一年持有期债券A"), 365)
        self.assertEqual(fund_lockup_days("长信30天滚动持有债券C"), 30)
        self.assertEqual(fund_lockup_days("某某三年定开债券"), 1095)
        self.assertEqual(fund_lockup_days("某某18个月封闭运作混合"), 540)
        self.assertIsNone(fund_lockup_days("易方达安悦超短债A"))
        preference = initial_preference(
            user_id="user:a",
            recorded_on=AS_OF,
            risk_profile=RiskProfile.CONSERVATIVE,
            horizon=PreferenceHorizon.ONE_TO_3Y,
            user_statement="不接受 1 年以上的封闭期",
            overrides={"max_lockup_days": 365},
        )
        positions = [
            DiagnosisPosition("a", "鹏华年年红一年持有期债券A", "open_fund", 50.0, {"bond": 1.0}),
            DiagnosisPosition("b", "某某三年定开债券", "open_fund", 50.0, {"bond": 1.0}),
        ]
        checks = {
            item["check_id"]: item
            for item in preference_check(exposure_summary(positions), {"portfolio_volatility": 0.01}, [], preference, positions)
        }
        self.assertEqual(checks["lockup"]["status"], "above")
        self.assertIn("三年定开", checks["lockup"]["detail"])
        self.assertNotIn("年年红", checks["lockup"]["detail"])

    def test_preference_check_flags_equity_above_range(self):
        positions = [
            DiagnosisPosition("a", "A", "stock", 90.0, {"equity": 1.0}, industries={"银行": 1.0}, stocks={"600000": ("A", 1.0)}),
            DiagnosisPosition("cash", "现金", "cash", 10.0, {"cash": 1.0}),
        ]
        exposures = exposure_summary(positions)
        preference = initial_preference(
            user_id="user:a",
            recorded_on=AS_OF,
            risk_profile=RiskProfile.BALANCED,
            horizon=PreferenceHorizon.ONE_TO_3Y,
            user_statement="平衡",
        )

        checks = {
            item["check_id"]: item
            for item in preference_check(
                exposures, {"portfolio_volatility": 0.08}, [], preference
            )
        }

        self.assertEqual(checks["equity_weight"]["status"], "above")
        self.assertEqual(checks["single_stock"]["status"], "above")
        self.assertEqual(checks["cash_weight"]["status"], "ok")
        self.assertEqual(checks["volatility"]["status"], "ok")
        self.assertEqual(checks["stress_loss"]["status"], "unknown")
        self.assertEqual(weights_of(positions)["a"], 0.9)
        self.assertEqual(preference.max_single_weight, Decimal("0.15"))


if __name__ == "__main__":
    unittest.main()
