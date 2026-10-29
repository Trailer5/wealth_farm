import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.portfolio_risk import (
    DiagnosisPosition,
    PreferenceHorizon,
    RiskProfile,
    TradeRecord,
    TradeSide,
    initial_preference,
)
from src.research_contracts import ASSET_CLASS_CASH, ASSET_CLASS_EQUITY
from src.research_orchestration.decision_review import DecisionReviewService

DAYS = [(date(2025, 1, 1) + timedelta(days=index)).isoformat() for index in range(200)]


def _asset_level(index: int) -> float:
    if index <= 50:
        return 100 + 20 * index / 50
    if index <= 100:
        return 120 - 30 * (index - 50) / 50
    return 90 + 10 * (index - 100) / 99


LEVELS = {"asset:a": {day: _asset_level(index) for index, day in enumerate(DAYS)}}
BENCHMARK = {day: 1000 + index for index, day in enumerate(DAYS)}
AS_OF = DAYS[-1]


class _Ledger:
    def __init__(self, entries):
        self.entries = entries

    def track_record(self, *, user_id):
        return {"entries": self.entries}


class _Diagnosis:
    def collect(self, **_):
        return {
            "positions": [
                DiagnosisPosition("asset:a", "甲", "stock", 60_000.0, {ASSET_CLASS_EQUITY: 1.0}),
                DiagnosisPosition("cash", "现金", "cash", 40_000.0, {ASSET_CLASS_CASH: 1.0}),
            ],
            "levels": LEVELS,
            "benchmark": BENCHMARK,
            "gaps": [],
        }


class _PortfolioRisk:
    def __init__(self, trades, preference):
        self.trades = trades
        self.preference = preference
        self.research_state = SimpleNamespace(
            list_holdings=lambda context: [SimpleNamespace(asset_id="asset:a", quantity=600.0, first_bought_on=None)]
        )

    def list_trades(self, context):
        return self.trades

    def latest_preference(self, context):
        return self.preference


def _plan_action(entry_id, action_type, start, end):
    return {
        "entry_id": entry_id,
        "entry_type": "action",
        "scope": "personal",
        "subject_id": "asset:a",
        "source_ref": "portfolio_plan:x",
        "as_of_date": start,
        "effective_until": end,
        "superseded": False,
        "artifact": {
            "action_type": action_type,
            "horizon": "short",
            "trigger": {"kind": "scheduled", "valid_from": start, "valid_until": end},
        },
        "settlements": [],
    }


class DecisionReviewServiceTest(unittest.TestCase):
    def setUp(self):
        self.preference = initial_preference(
            user_id="user:a",
            recorded_on="2025-01-01",
            risk_profile=RiskProfile.CONSERVATIVE,
            horizon=PreferenceHorizon.OVER_3Y,
            user_statement="最多亏 10%",
        )
        self.trades = [
            TradeRecord(user_id="user:a", asset_id="asset:a", side=TradeSide.BUY, trade_date=DAYS[0], quantity=Decimal("600")),
            TradeRecord(user_id="user:a", asset_id="asset:a", side=TradeSide.SELL, trade_date=DAYS[80], quantity=Decimal("100")),
            TradeRecord(user_id="user:a", asset_id="asset:a", side=TradeSide.BUY, trade_date=DAYS[100], quantity=Decimal("100")),
        ]
        self.entries = [
            _plan_action("buy-step", "buy", DAYS[0], DAYS[5]),
            _plan_action("sell-missed", "reduce", DAYS[20], DAYS[30]),
            _plan_action("open", "add", DAYS[195], "2025-12-31"),
        ]

    def _service(self, report_root):
        return DecisionReviewService(
            ledger=_Ledger(self.entries),
            diagnosis=_Diagnosis(),
            portfolio_risk=_PortfolioRisk(self.trades, self.preference),
            master_data=SimpleNamespace(get_asset=lambda asset_id: None),
            report_root=report_root,
        )

    def test_stated_drawdown_below_realized_gives_calibration_hint(self):
        with tempfile.TemporaryDirectory() as root:
            result = self._service(root).run(user_id="user:a", as_of_date=AS_OF)

            calibration = result["calibration"]
            drawdown = calibration["checks"][0]
            self.assertEqual(drawdown["status"], "exceeds")
            self.assertEqual(drawdown["worst_source"], "realized")
            self.assertAlmostEqual(result["calibration"]["realized"]["max_drawdown"]["drawdown"], 0.25, places=2)
            self.assertLess(calibration["replay"]["max_drawdown"]["drawdown"], 0.25)
            self.assertFalse(calibration["consistent"])
            self.assertTrue(any(item["hint_id"] == "sell_in_drawdown" for item in calibration["behaviours"]))

            execution = result["execution"]
            status = {row["entry_id"]: row["status"] for row in execution["actions"]}
            self.assertEqual(status, {"buy-step": "executed", "sell-missed": "not_executed", "open": "open"})
            self.assertEqual(len(execution["unplanned_trades"]), 2)

            text = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("## 偏好校准", text)
            self.assertIn("超过自述的 10.0%", text)
            self.assertIn("计划外交易", text)

    def test_development_samples_are_left_out_unless_asked(self):
        seed = _plan_action("seed", "buy", DAYS[0], DAYS[5])
        seed["source_ref"] = "dev-seed:2026-10-26"
        seed["settlements"] = [
            {"checkpoint_date": DAYS[30], "status": "settled", "claim_result": "met", "asset_return": "0.05"}
        ]
        self.entries.append(seed)
        with tempfile.TemporaryDirectory() as root:
            default = self._service(root).run(user_id="user:a", as_of_date=AS_OF, calibrate=False, write_report=False)
            included = self._service(root).run(
                user_id="user:a", as_of_date=AS_OF, calibrate=False, write_report=False, include_dev_seed=True
            )

        self.assertEqual(default["decisions"]["actions"]["overall"]["settled"], 0)
        self.assertIn("1 条开发样例，未计入", default["summary"][0])
        self.assertEqual(included["decisions"]["actions"]["overall"]["settled"], 1)

    def test_without_trades_or_calibration(self):
        self.trades = []
        with tempfile.TemporaryDirectory() as root:
            result = self._service(root).run(user_id="user:a", as_of_date=AS_OF, calibrate=False, write_report=False)

        self.assertIsNone(result["execution"])
        self.assertNotIn("calibration", result)
        self.assertTrue(any("没有交易流水" in item for item in result["summary"]))


if __name__ == "__main__":
    unittest.main()
