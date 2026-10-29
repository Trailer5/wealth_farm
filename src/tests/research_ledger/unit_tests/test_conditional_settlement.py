import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.research_contracts import (
    ActionTrigger,
    ActionType,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
)
from src.research_ledger import (
    ClaimResult,
    LedgerScope,
    LedgerSettlementService,
    MetricPoint,
    PricePoint,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
    parse_trigger_condition,
    settlement_schedule,
)

ASSET = "asset:held"
METRIC = f"fin:{ASSET}:gross_margin"
PRICES = {
    ASSET: (
        ("2026-01-05", "100"),
        ("2026-03-02", "112"),
        ("2026-03-03", "111"),
        ("2026-04-01", "100"),
        ("2026-05-04", "98"),
    ),
    "asset:alt": (
        ("2026-03-02", "50"),
        ("2026-03-03", "50"),
        ("2026-04-01", "55"),
        ("2026-05-04", "56"),
    ),
}


class _Prices:
    def series(self, asset_id, start, end):
        return tuple(
            PricePoint(day, Decimal(value), "total_return")
            for day, value in PRICES[asset_id]
            if start <= day <= end
        )


class _Metrics:
    def series(self, metric_id, start, end):
        assert metric_id == METRIC
        return (
            MetricPoint("2025-12-31", "2026-03-20", Decimal("0.90")),
            MetricPoint("2026-03-31", "2026-04-20", Decimal("0.80")),
        )


def _action(key, *, action_id="exit:1", alternative=None, until="2026-12-31"):
    return SettleableAction(
        action_id=action_id,
        asset_id=ASSET,
        action_type=ActionType.REDUCE if alternative is None else ActionType.EXIT,
        horizon=ResearchHorizon.SHORT,
        trigger=ActionTrigger(
            TriggerKind.CONDITIONAL,
            "2026-01-01",
            until,
            observability_key=key,
            condition="条件出现时减持",
        ),
        quantity_rule=QuantityRule(QuantityRuleKind.USER_DEFINED, description="减持三分之一"),
        settlement_rule=SettlementRule(SettlementMethod.PRICE_RELATIVE, ASSET, alternative_id=alternative),
    )


class ConditionalSettlementTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = SQLiteResearchLedgerRepository(Path(self.temp_dir.name) / "db.sqlite3")
        self.ledger = ResearchLedgerService(repository)
        self.service = LedgerSettlementService(
            repository=repository,
            prices=_Prices(),
            metrics=_Metrics(),
            clock=lambda: datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _settle(self, action, as_of):
        (entry,) = self.ledger.record((action,), scope=LedgerScope.PERSONAL, user_id="user:a", source_ref="test")
        self.service.settle_due(as_of)
        return self.ledger.list_settlements(entry.entry_id, user_id="user:a")

    def test_parse_keys(self):
        self.assertEqual(parse_trigger_condition("price_above:110", ASSET).observed_id, ASSET)
        self.assertEqual(parse_trigger_condition(f"metric_below:0.85:{METRIC}", ASSET).observed_id, METRIC)
        for key in ("price_below", "max_weight_above:0.2", "metric_below:0.8", "price_above:x", None):
            self.assertIsNone(parse_trigger_condition(key, ASSET))
        schedule = settlement_schedule(_action("max_weight_above:0.2"))
        self.assertIn("不可机器判定", schedule.unsettleable_reason)

    def test_price_trigger_starts_settlement_on_the_trigger_date(self):
        (settled,) = self._settle(_action("price_above:110"), "2026-04-15")
        self.assertEqual(settled.checkpoint_date, "2026-04-01")
        self.assertEqual(settled.status, SettlementStatus.SETTLED)
        self.assertEqual(settled.reference_date, "2026-03-03")
        self.assertEqual(settled.claim_result, ClaimResult.MET)
        self.assertIn("2026-03-02 首次出现", settled.note)

    def test_condition_not_met_is_final_only_after_the_window(self):
        self.assertEqual(self._settle(_action("price_above:200", until="2026-04-30"), "2026-04-15"), ())
        self.service.settle_due("2026-05-10")
        (entry,) = self.ledger.list_entries(user_id="user:a")
        (settled,) = self.ledger.list_settlements(entry.entry_id, user_id="user:a")
        self.assertEqual(settled.status, SettlementStatus.NOT_SETTLEABLE)
        self.assertEqual(settled.checkpoint_date, "2026-04-30")
        self.assertIn("未出现", settled.note)

    def test_metric_trigger_uses_the_publication_date(self):
        (settled,) = self._settle(_action(f"metric_below:0.85:{METRIC}"), "2026-05-25")
        self.assertEqual(settled.checkpoint_date, "2026-05-20")
        self.assertIn("2026-04-20 首次出现", settled.note)
        self.assertEqual(settled.observed_date, "2026-05-04")

    def test_switch_is_judged_against_the_alternative(self):
        (settled,) = self._settle(_action("price_above:110", alternative="asset:alt"), "2026-04-15")
        self.assertEqual(settled.claim_result, ClaimResult.MET)
        self.assertEqual(settled.alternative_return, Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()
