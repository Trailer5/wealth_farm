import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from src.portfolio_risk import PreferenceHorizon, RiskProfile, initial_preference, rolling_return_band
from src.research_contracts import (
    ActionTrigger,
    ActionType,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    TriggerKind,
)
from src.research_ledger import LedgerEntryType, LedgerScope
from src.research_orchestration.portfolio_plan import PlanBlockedError, PortfolioPlanService, assemble_plan

AS_OF = "2026-10-28"


def _proposal(shortfall: float = 0.0) -> dict:
    def window(index):
        start = date.fromisoformat(AS_OF) + timedelta(days=index * 14 + 1)
        return [start.isoformat(), (start + timedelta(days=13)).isoformat()]

    return {
        "targets": [
            {"asset_id": "stock:a", "label": "A", "asset_type": "stock", "current": 0.33, "target": 0.13, "low": 0.11, "high": 0.15},
            {"asset_id": "etf:b", "label": "B", "asset_type": "exchange_fund", "current": 0.2, "target": 0.3, "low": 0.28, "high": 0.32},
            {"asset_id": "etf:c", "label": "C", "asset_type": "exchange_fund", "current": 0.0, "target": 0.1, "low": 0.08, "high": 0.12},
        ],
        "migration": {
            "steps": [
                {
                    "step": index + 1,
                    "window_start": window(index)[0],
                    "window_end": window(index)[1],
                    "legs": [
                        *(
                            [{"asset_id": "stock:a", "label": "A", "action": "sell", "amount": -10000, "cost": 7.5, "condition": None, "note": None, "window": window(index)}]
                            if index < 2
                            else []
                        ),
                        {"asset_id": "etf:b", "label": "B", "action": "buy", "amount": 4000, "cost": 1, "condition": "估值条件", "note": None, "window": window(index)},
                        {"asset_id": "etf:c", "label": "C", "action": "buy", "amount": 2000, "cost": 0.5, "condition": "估值条件", "note": None, "window": window(index)},
                    ],
                }
                for index in range(3)
            ],
            "cash_shortfall": shortfall,
        },
        "buy_valuation": {"series_id": "csi300_pe_ttm", "threshold_value": 13.2, "met": True},
        "return_band": {
            "low": -0.2, "high": 0.3, "coverage": 0.8, "probability_low": 0.55, "probability_high": 0.93,
            "windows": 2500, "independent_years": 10, "start": "2015-01-05", "end": AS_OF,
        },
    }


def _existing(asset_id: str, action_type: ActionType) -> SettleableAction:
    return SettleableAction(
        action_id=f"existing:{asset_id}:{action_type.value}",
        asset_id=asset_id,
        action_type=action_type,
        horizon=ResearchHorizon.MEDIUM_LONG,
        trigger=ActionTrigger(kind=TriggerKind.SCHEDULED, valid_from=AS_OF, valid_until="2027-04-28", scheduled_on=AS_OF),
        quantity_rule=QuantityRule(kind=QuantityRuleKind.USER_DEFINED, description="按仓位计划"),
        settlement_rule=SettlementRule(method=SettlementMethod.PRICE_RELATIVE, observed_id=asset_id),
    )


def _preference():
    return initial_preference(
        user_id="user:a", recorded_on=AS_OF, risk_profile=RiskProfile.GROWTH,
        horizon=PreferenceHorizon.OVER_3Y, user_statement="成长",
    )


class AssemblePlanTest(unittest.TestCase):
    def test_nodes_targets_and_reviews(self):
        result = assemble_plan(
            _proposal(),
            user_id="user:a",
            as_of_date=AS_OF,
            preference=_preference(),
            existing=[],
            review_sources=[("2027-04-30", "论点检查"), ("2030-01-01", "太远")],
            previous_version=0,
        )
        plan = result["plan"]

        kinds = {(item.asset_id, item.trigger.kind) for item in plan.actions}
        self.assertIn(("stock:a", TriggerKind.SCHEDULED), kinds)
        conditional = [item for item in plan.actions if item.trigger.kind is TriggerKind.CONDITIONAL]
        self.assertEqual(len(conditional), 4)
        self.assertEqual(conditional[0].trigger.observability_key, "metric_below:13.2:macro:csi300_pe_ttm")
        self.assertTrue(all(item.action_type is ActionType.BUY for item in plan.actions if item.asset_id == "etf:c"))
        self.assertTrue(all(item.action_type is ActionType.REDUCE for item in plan.actions if item.asset_id == "stock:a"))
        focuses = [item.focus for item in plan.review_nodes]
        self.assertIn("论点检查", focuses)
        self.assertNotIn("太远", focuses)
        band = next(item for item in plan.targets if item.metric_key == "return_1y")
        self.assertEqual(str(band.probability.low), "0.55")
        self.assertEqual(plan.version, 1)
        self.assertEqual(result["nodes"][0]["date"], plan.actions[0].trigger.valid_from)

    def test_conflicts_drop_buys_against_recorded_exits_and_buys_against_reductions(self):
        result = assemble_plan(
            _proposal(shortfall=500),
            user_id="user:a",
            as_of_date=AS_OF,
            preference=_preference(),
            existing=[("e1", _existing("etf:c", ActionType.EXIT)), ("e2", _existing("stock:a", ActionType.ADD))],
            review_sources=[],
            previous_version=2,
        )
        plan = result["plan"]

        self.assertFalse(any(item.asset_id == "etf:c" and item.action_type is ActionType.BUY for item in plan.actions))
        self.assertIn("existing:etf:c:exit", [item.action_id for item in plan.actions])
        self.assertNotIn("existing:stock:a:add", [item.action_id for item in plan.actions])
        self.assertEqual(len(plan.excluded_actions), 4)
        self.assertTrue(any("还差约 500 元" in item for item in plan.conflicts))
        self.assertEqual(plan.version, 3)


class ReturnBandTest(unittest.TestCase):
    def test_band_and_probability_interval(self):
        start = date(2014, 1, 1)
        levels, value = {}, 100.0
        for index in range(4000):
            day = start + timedelta(days=index)
            if day.weekday() >= 5:
                continue
            value *= 1 + 0.01 * ((index % 7) - 3) / 3
            levels[day.isoformat()] = value

        band = rolling_return_band({"a": 0.6, "cash": 0.4}, {"a": levels}, as_of_date="2024-12-31",
                                   benchmark_levels=levels, zero_return=frozenset({"cash"}))

        self.assertLess(band["low"], band["high"])
        self.assertLess(band["probability_low"], 0.8)
        self.assertGreater(band["probability_high"], 0.8)
        self.assertGreaterEqual(band["independent_years"], 10)


class _Ledger:
    def __init__(self, entries):
        self.entries = entries

    def list_entries(self, *, user_id, entry_type, active_on=None):
        return [item for item in self.entries if item.entry_type is entry_type]


def _view_entry(subject_id, *, claim=True, scope=LedgerScope.PERSONAL, subject_type=SubjectType.ASSET):
    return SimpleNamespace(
        entry_type=LedgerEntryType.VIEW,
        scope=scope,
        artifact=SimpleNamespace(
            subject_id=subject_id,
            subject_type=subject_type,
            claim=object() if claim else None,
            settlement_rule=None,
            expires_on="2027-01-31",
            proposition=f"观点 {subject_id}",
        ),
    )


class PlanServiceTest(unittest.TestCase):
    def test_allocation_conflict_blocks_the_plan(self):
        adjustment = SimpleNamespace(run=lambda **_: {"status": "conflict", "summary": ["冲突", "选项一", "选项二"]})
        service = PortfolioPlanService(adjustment=adjustment, ledger=_Ledger([]), report_root=".")

        with self.assertRaises(PlanBlockedError) as raised:
            service.run(user_id="user:a", as_of_date=AS_OF)
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertIn("选项二", str(raised.exception))

    def test_review_sources_keep_directional_views_on_plan_assets_and_markets(self):
        ledger = _Ledger(
            [
                _view_entry("stock:a"),
                _view_entry("stock:a", claim=False),
                _view_entry("stock:other"),
                _view_entry("stock:a", scope=LedgerScope.OBJECTIVE),
                _view_entry("macro:cn", subject_type=SubjectType.MACRO),
            ]
        )
        service = PortfolioPlanService(adjustment=None, ledger=ledger, report_root=".")

        sources = service._review_sources("user:a", AS_OF, {"stock:a", "etf:b"})

        self.assertEqual([focus for _, focus in sources], ["观点结算：观点 stock:a", "观点结算：观点 macro:cn"])


if __name__ == "__main__":
    unittest.main()
