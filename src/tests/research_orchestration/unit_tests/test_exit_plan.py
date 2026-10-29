import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.research_contracts import (
    ClaimComparison,
    InvestmentThesis,
    ResearchHorizon,
    ThesisIndicator,
    TriggerKind,
    ViewOrigin,
)
from src.research_ledger import (
    LedgerEntryType,
    PricePoint,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
)
from src.research_orchestration.behavioral_hints import behavioral_hints
from src.research_orchestration.exit_plan import ExitPlanService, _valuation_condition
from src.research_orchestration.exit_plan_models import (
    ExitAction,
    ExitDimension,
    ExitPlan,
    ExitState,
    HoldingFacts,
    exit_plan_lines,
)
from src.research_state.models import HoldingPosition

ASSET = "asset:held"
AS_OF = "2026-08-21"
THESIS = InvestmentThesis(
    thesis_id="thesis:held",
    asset_id=ASSET,
    origin=ViewOrigin.USER,
    user_id="user:a",
    summary="提价带动利润增长",
    as_of_date="2026-05-01",
    expires_on="2027-04-30",
    horizon=ResearchHorizon.MEDIUM_LONG,
    reconstructed=True,
    indicators=(
        ThesisIndicator(
            "growth",
            "recurring_profit_growth",
            "扣非净利润同比（TTM）",
            "2026-12-31",
            expected_low=Decimal("0.10"),
            invalidation_comparison=ClaimComparison.BELOW,
            invalidation_threshold=Decimal("0"),
        ),
    ),
)


class _Thesis:
    def __init__(self, status):
        self.status = status
        self.ledger = SimpleNamespace(get_entry=lambda entry_id, user_id: SimpleNamespace(artifact=THESIS))

    def check(self, asset_id, *, as_of_date, user_id=None):
        if self.status is None:
            return {"theses": []}
        return {
            "theses": [
                {
                    "thesis_entry_id": "ledger-thesis",
                    "as_of_date": THESIS.as_of_date,
                    "status": self.status,
                    "status_label": {"deviating": "偏离", "on_track": "符合"}[self.status],
                    "reconstructed": True,
                    "indicators": [
                        {
                            "indicator_id": "growth",
                            "label": "扣非净利润同比（TTM）",
                            "status": self.status,
                            "note": "最新 2026-06-30 期为 5.00%",
                            "invalidation": "扣非净利润同比（TTM）低于 0.00% 即论点失效",
                        }
                    ],
                }
            ]
        }


class _Valuation:
    def __init__(self, implied, alternatives=None):
        self.implied = implied
        self.alternatives = alternatives or {}

    def run(self, symbol, as_of_date, *, asset_id=None, record=True, allow_remote=True):
        implied = self.implied if asset_id else self.alternatives[symbol]
        return {
            "applicable": True,
            "asset_id": asset_id or f"asset:{symbol}",
            "name": "被持有" if asset_id else f"替代{symbol}",
            "price": "40",
            "model": {
                "method": "reverse_dcf",
                "report_period": "2026-06-30",
                "primary_basis": "free_cash_flow",
                "assumptions": {"risk_free_10y": 0.017, "discount_rate": 0.067, "terminal_growth": 0.02},
                "scenarios": {
                    "optimistic": {"per_share": 60.0, "growth": 0.15},
                    "anchor": {"implied_return": implied},
                },
            },
        }


class _ResearchState:
    def __init__(self, holdings):
        self.holdings = holdings

    def get_holding(self, context, *, asset_id):
        return next((h for h in self.holdings if h.asset_id == asset_id), None)

    def list_holdings(self, context):
        return list(self.holdings)


class _PortfolioRisk:
    def __init__(self, limit):
        self.limit = limit

    def list_snapshots(self, context):
        return []

    def list_constraint_snapshots(self, context):
        if self.limit is None:
            return []
        return [
            SimpleNamespace(
                snapshot_id="constraints:1",
                subject_asset_id=ASSET,
                as_of_date="2026-08-01",
                created_at="",
                max_single_asset_weight=Decimal(self.limit),
            )
        ]


class _Prices:
    def series(self, asset_id, start, end):
        return (PricePoint(end, Decimal({ASSET: "40", "asset:other": "10"}[asset_id])),)


class ExitPlanServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = ResearchLedgerService(SQLiteResearchLedgerRepository(Path(self.temp_dir.name) / "db.sqlite3"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _service(self, *, thesis="deviating", implied=0.012, limit="0.2", alternatives=None):
        holdings = [
            HoldingPosition("user:a", ASSET, 1000, 50.0, first_bought_on="2025-01-02"),
            HoldingPosition("user:a", "asset:other", 1000, 10.0),
        ]
        return ExitPlanService(
            research_state=_ResearchState(holdings),
            portfolio_risk=_PortfolioRisk(limit),
            thesis=_Thesis(thesis),
            valuation=_Valuation(implied, alternatives),
            prices=_Prices(),
            master_data=SimpleNamespace(get_asset=lambda asset_id: SimpleNamespace(display_name="被持有", standard_symbol="600000")),
            ledger=self.ledger,
        )

    def _by_dimension(self, plan):
        result = {}
        for item in plan.conditions:
            result.setdefault(item.dimension, []).append(item)
        return result

    def test_dimensions_are_listed_side_by_side_and_recorded(self):
        service = self._service()
        plan = service.assemble(user_id="user:a", asset_id=ASSET, as_of_date=AS_OF)
        dims = self._by_dimension(plan)

        triggered, armed = dims[ExitDimension.THESIS]
        self.assertEqual((triggered.state, triggered.action), (ExitState.TRIGGERED, ExitAction.REDUCE))
        self.assertEqual(armed.observability_key, f"metric_below:0:fin:{ASSET}:recurring_profit_growth")
        (valuation,) = dims[ExitDimension.VALUATION]
        self.assertEqual((valuation.state, valuation.observability_key), (ExitState.ARMED, "price_above:60.00"))
        (risk,) = dims[ExitDimension.RISK_BUDGET]
        self.assertEqual(risk.state, ExitState.TRIGGERED)
        self.assertIn("80.0%", risk.summary)
        self.assertTrue(any("现算" in gap for gap in plan.gaps))
        (opportunity,) = dims[ExitDimension.OPPORTUNITY_COST]
        self.assertEqual((opportunity.state, opportunity.action), (ExitState.TRIGGERED, ExitAction.SWITCH))
        (time_stop,) = dims[ExitDimension.TIME_STOP]
        self.assertEqual(time_stop.observability_key, "thesis_status:ledger-thesis")
        self.assertEqual(plan.holding.unrealized_return, Decimal("-0.2"))
        self.assertTrue(plan.has_exit_condition)
        self.assertEqual(ExitPlan.from_dict(plan.to_dict()), plan)
        self.assertTrue(any("不是目标价" in line for line in exit_plan_lines(plan)))

        entries = service.record(plan)
        self.assertEqual(len(entries), 6)
        actions = [e.artifact for e in self.ledger.list_entries(user_id="user:a", entry_type=LedgerEntryType.ACTION)]
        kinds = sorted(action.trigger.kind.value for action in actions)
        self.assertEqual(kinds.count(TriggerKind.SCHEDULED.value), 3)
        self.assertEqual(service.record(plan), entries)

    def test_opportunity_cost_against_alternatives(self):
        better = self._service(implied=0.05, alternatives={"000002": 0.09}).assemble(
            user_id="user:a", asset_id=ASSET, as_of_date=AS_OF, alternatives=("000002",)
        )
        (item,) = self._by_dimension(better)[ExitDimension.OPPORTUNITY_COST]
        self.assertEqual((item.state, item.alternative_asset_id), (ExitState.TRIGGERED, "asset:000002"))
        close = self._service(implied=0.05, alternatives={"000002": 0.06}).assemble(
            user_id="user:a", asset_id=ASSET, as_of_date=AS_OF, alternatives=("000002",)
        )
        (item,) = self._by_dimension(close)[ExitDimension.OPPORTUNITY_COST]
        self.assertEqual(item.state, ExitState.CLEAR)

    def test_missing_thesis_and_constraints_are_insufficient(self):
        plan = self._service(thesis=None, implied=0.05, limit=None).assemble(
            user_id="user:a", asset_id=ASSET, as_of_date=AS_OF
        )
        dims = self._by_dimension(plan)
        for dimension in (ExitDimension.THESIS, ExitDimension.RISK_BUDGET, ExitDimension.TIME_STOP):
            self.assertEqual(dims[dimension][0].state, ExitState.INSUFFICIENT)
        self.assertTrue(plan.has_exit_condition)
        with self.assertRaisesRegex(ValueError, "record-holding"):
            self._service().assemble(user_id="user:a", asset_id="asset:none", as_of_date=AS_OF)

    def test_unprofitable_valuation_compares_implied_revenue_growth_with_history(self):
        def model(implied, cagr):
            return {
                "method": "revenue_reverse_dcf",
                "report_period": "2026-06-30",
                "assumptions": {"explicit_years": 10},
                "implied_revenue_growth_by_terminal_margin": {"5%": 0.6, "10%": 0.5, "15%": implied},
                "historical_growth": {"revenue_cagr": cagr},
            }

        stretched = _valuation_condition(model(0.45, 0.20), Decimal("40"))
        self.assertEqual((stretched.state, stretched.action), (ExitState.TRIGGERED, ExitAction.REDUCE))
        self.assertIn("15%", stretched.summary)
        self.assertIsNone(stretched.observability_key)
        fair = _valuation_condition(model(0.25, 0.20), Decimal("40"))
        self.assertEqual((fair.state, fair.action), (ExitState.CLEAR, ExitAction.HOLD))
        self.assertEqual(_valuation_condition(model(None, 0.2), Decimal("40")).state, ExitState.INSUFFICIENT)
        self.assertEqual(_valuation_condition(model(0.3, None), Decimal("40")).state, ExitState.INSUFFICIENT)


class BehavioralHintsTest(unittest.TestCase):
    def _facts(self, pnl):
        return HoldingFacts(
            quantity=Decimal("100"),
            price=Decimal("10"),
            price_date=AS_OF,
            average_cost=Decimal("10"),
            unrealized_return=None if pnl is None else Decimal(pnl),
            first_bought_on="2026-01-01",
        )

    def _codes(self, pnl, status, intended, conditions=()):
        return {
            item.code: item.severity
            for item in behavioral_hints(
                self._facts(pnl),
                thesis_status=status,
                reconstructed=False,
                conditions=conditions,
                intended_action=intended,
                as_of_date=AS_OF,
            )
        }

    def test_rules(self):
        self.assertEqual(self._codes("-0.25", "on_track", None)["cost_anchor"], "caution")
        codes = self._codes("-0.25", "deviating", "add")
        self.assertEqual(codes["averaging_down"], "warning")
        self.assertEqual(codes["thesis_broken_hold"], "warning")
        self.assertEqual(self._codes("-0.05", "on_track", "add")["averaging_down"], "caution")
        self.assertIn("premature_profit_taking", self._codes("0.3", "on_track", "exit"))
        self.assertNotIn("premature_profit_taking", self._codes("0.3", "deviating", "exit"))
        self.assertIn("cost_unknown", self._codes(None, None, None))
        self.assertIn("holding_period", self._codes("0", None, None))
        with self.assertRaises(ValueError):
            self._codes("0", None, "sell_all")


if __name__ == "__main__":
    unittest.main()
