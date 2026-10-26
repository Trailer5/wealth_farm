import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.research_contracts import (
    ActionTrigger,
    ActionType,
    ClaimComparison,
    Confidence,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    TriggerKind,
    View,
    ViewClaim,
    ViewOrigin,
)
from src.research_ledger import (
    ClaimResult,
    LedgerScope,
    LedgerSettlementService,
    PriceDataUnavailable,
    PricePoint,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
    SettlementStatus,
)


AS_OF = "2026-01-02"
CHECKPOINT = "2026-02-01"


class LedgerSettlementTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = SQLiteResearchLedgerRepository(
            Path(self.temp_dir.name) / "ledger.sqlite3"
        )
        self.ledger = ResearchLedgerService(repository)
        self.prices = _FakePrices()
        self.settlement = LedgerSettlementService(
            repository=repository,
            prices=self.prices,
            clock=lambda: datetime(2026, 2, 2, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _record(self, artifact):
        (entry,) = self.ledger.record(
            (artifact,),
            scope=LedgerScope.PERSONAL,
            user_id="user:a",
            source_ref="test",
        )
        return entry

    def _settle(self, artifact, **kwargs):
        entry = self._record(artifact)
        self.settlement.settle_due(CHECKPOINT, **kwargs)
        return self.ledger.list_settlements(entry.entry_id, user_id="user:a")

    def test_buy_uses_first_price_after_publication_and_benchmark(self):
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("0.1")))
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0.5")))

        (settled,) = self._settle(_action(ActionType.BUY))

        self.assertEqual(settled.status, SettlementStatus.SETTLED)
        self.assertEqual(settled.reference_date, "2026-01-05")
        self.assertEqual(settled.observed_date, "2026-01-30")
        self.assertEqual(settled.reference_value, Decimal("10.1"))
        self.assertGreater(settled.excess_return, 0)
        self.assertEqual(settled.claim_result, ClaimResult.MET)
        self.assertIsNone(settled.note)

    def test_price_basis_is_declared_when_benchmark_is_price_index(self):
        self.prices.set(
            "stock:a",
            [
                replace(item, basis="total_return")
                for item in _daily(Decimal("10"), Decimal("0.1"))
            ],
        )
        self.prices.set(
            "index:b",
            [
                replace(item, basis="price")
                for item in _daily(Decimal("100"), Decimal("0.5"))
            ],
        )

        (settled,) = self._settle(_action(ActionType.BUY))

        self.assertIn("复权总回报", settled.note)
        self.assertIn("基准为价格指数", settled.note)

    def test_wait_view_records_period_performance(self):
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("-0.1")))
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))

        (settled,) = self._settle(_view(claim=None))

        self.assertEqual(settled.claim_result, ClaimResult.NOT_DIRECTIONAL)
        self.assertLess(settled.asset_return, 0)

    def test_avoid_view_is_met_when_asset_lags_and_tracks_adverse_rise(self):
        series = _daily(Decimal("10"), Decimal("0.1"))
        series[5] = PricePoint(series[5].date, Decimal("12"))
        self.prices.set("stock:a", series)
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("10")))

        (settled,) = self._settle(
            _view(
                claim=ViewClaim("total_return", ClaimComparison.UNDERPERFORM)
            )
        )

        self.assertEqual(settled.claim_result, ClaimResult.MET)
        self.assertEqual(
            settled.max_adverse,
            (Decimal("12") / Decimal("10.1") - 1).quantize(
                Decimal("0.00000001")
            ),
        )

    def test_suspension_and_delisting_use_last_price_with_note(self):
        self.prices.set(
            "stock:a",
            [
                item
                for item in _daily(Decimal("10"), Decimal("0.1"))
                if item.date <= "2026-01-09"
            ],
        )
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))

        (settled,) = self._settle(_view(claim=None))

        self.assertEqual(settled.status, SettlementStatus.SETTLED)
        self.assertEqual(settled.observed_date, "2026-01-09")
        self.assertIn("停牌、退市", settled.note)

    def test_leading_price_gap_is_data_insufficient(self):
        self.prices.set(
            "stock:a",
            [
                item
                for item in _daily(Decimal("10"), Decimal("0.1"))
                if item.date >= "2026-01-26"
            ],
        )
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))

        (settled,) = self._settle(_view(claim=None))

        self.assertEqual(settled.status, SettlementStatus.DATA_INSUFFICIENT)
        self.assertIn("2026-01-26", settled.note)

    def test_track_record_keeps_latest_settlement_per_checkpoint(self):
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("0.1")))
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))
        self._record(_view(claim=None))
        self.settlement.settle_due(CHECKPOINT)
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("-0.1")))
        self.settlement.clock = lambda: datetime(
            2026,
            2,
            3,
            tzinfo=timezone.utc,
        )
        self.settlement.settle_due(CHECKPOINT, recompute=True)

        record = self.ledger.track_record(user_id="user:a")

        (item,) = record["entries"]
        (settlement,) = item["settlements"]
        self.assertLess(Decimal(str(settlement["asset_return"])), 0)
        self.assertEqual(record["checkpoint_counts"], {"not_directional": 1})
        self.assertEqual(self.ledger.track_record(user_id="user:b")["entries"], [])

    def test_open_fund_uses_confirmed_nav_after_publication(self):
        self.prices.set(
            "fund:a",
            [
                PricePoint("2026-01-02", Decimal("1.00")),
                PricePoint("2026-01-05", Decimal("1.01")),
                PricePoint("2026-01-30", Decimal("1.05")),
                PricePoint("2026-02-02", Decimal("2.00")),
            ],
        )
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))

        (settled,) = self._settle(_view(claim=None, asset_id="fund:a"))

        self.assertEqual(settled.reference_date, "2026-01-05")
        self.assertEqual(settled.observed_value, Decimal("1.05"))

    def test_remote_failure_is_retryable_and_not_zero_return(self):
        self.prices.fail("stock:a")
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))
        entry = self._record(_view(claim=None))

        self.settlement.settle_due(CHECKPOINT)
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("0.1")))
        self.settlement.settle_due(CHECKPOINT)
        rerun = self.settlement.settle_due(CHECKPOINT)

        by_status = {
            item.status: item
            for item in self.ledger.list_settlements(
                entry.entry_id,
                user_id="user:a",
            )
        }
        self.assertIsNone(
            by_status[SettlementStatus.DATA_INSUFFICIENT].asset_return
        )
        self.assertIn(SettlementStatus.SETTLED, by_status)
        self.assertEqual(rerun.appended, ())

    def test_revised_data_appends_without_overwriting(self):
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("0.1")))
        self.prices.set("index:b", _daily(Decimal("100"), Decimal("0")))
        entry = self._record(_view(claim=None))
        self.settlement.settle_due(CHECKPOINT)
        self.prices.set("stock:a", _daily(Decimal("10"), Decimal("0.2")))

        unchanged = self.settlement.settle_due(CHECKPOINT)
        revised = self.settlement.settle_due(CHECKPOINT, recompute=True)

        self.assertEqual(unchanged.appended, ())
        self.assertEqual(len(revised.appended), 1)
        self.assertEqual(
            len(
                self.ledger.list_settlements(
                    entry.entry_id,
                    user_id="user:a",
                )
            ),
            2,
        )

    def test_unsupported_judgements_are_marked_not_settleable(self):
        metric = _view(
            claim=ViewClaim(
                "cpi_yoy",
                ClaimComparison.ABOVE,
                Decimal("0.03"),
            ),
            subject_type=SubjectType.MACRO,
            asset_id="macro:cn",
            settlement_rule=SettlementRule(
                SettlementMethod.METRIC_OBSERVATION,
                "cpi_yoy",
            ),
        )
        conditional = replace(
            _action(ActionType.BUY),
            action_id="action:conditional",
            trigger=ActionTrigger(
                TriggerKind.CONDITIONAL,
                valid_from=AS_OF,
                valid_until="2026-03-31",
                observability_key="pe_ttm",
                condition="pe_ttm <= 20",
            ),
        )

        for artifact in (metric, conditional):
            with self.subTest(artifact=type(artifact).__name__):
                (settled,) = self._settle(artifact)
                self.assertEqual(
                    settled.status,
                    SettlementStatus.NOT_SETTLEABLE,
                )
                self.assertTrue(settled.note)
        self.assertEqual(self.prices.requests, [])


class _FakePrices:
    def __init__(self):
        self.data = {}
        self.requests = []

    def set(self, asset_id, points):
        self.data[asset_id] = tuple(points)

    def fail(self, asset_id):
        self.data[asset_id] = PriceDataUnavailable("remote timeout")

    def series(self, asset_id, start, end):
        self.requests.append((asset_id, start, end))
        value = self.data[asset_id]
        if isinstance(value, PriceDataUnavailable):
            raise value
        return tuple(item for item in value if start <= item.date)


def _daily(start_value, step):
    """Weekday prices from AS_OF to beyond the checkpoint."""

    points = []
    day = date.fromisoformat(AS_OF)
    value = start_value
    while day <= date(2026, 2, 10):
        if day.weekday() < 5:
            points.append(PricePoint(day.isoformat(), value))
            value += step
        day += timedelta(days=1)
    return points


def _rule(asset_id="stock:a"):
    return SettlementRule(
        SettlementMethod.PRICE_RELATIVE,
        asset_id,
        benchmark_id="index:b",
    )


def _view(
    *,
    claim,
    asset_id="stock:a",
    subject_type=SubjectType.ASSET,
    settlement_rule=None,
):
    return View(
        view_id=f"view:{asset_id}:{claim.comparison.value if claim else 'none'}",
        subject_type=subject_type,
        subject_id=asset_id,
        origin=ViewOrigin.SYSTEM,
        proposition="测试观点",
        horizon=ResearchHorizon.SHORT,
        as_of_date=AS_OF,
        expires_on=CHECKPOINT,
        confidence=Confidence.MEDIUM,
        claim=claim,
        falsifiers=("测试证伪条件",),
        settlement_rule=settlement_rule or _rule(asset_id),
    )


def _action(action_type):
    return SettleableAction(
        action_id=f"action:{action_type.value}",
        asset_id="stock:a",
        action_type=action_type,
        horizon=ResearchHorizon.SHORT,
        trigger=ActionTrigger(
            TriggerKind.SCHEDULED,
            valid_from=AS_OF,
            valid_until=AS_OF,
            scheduled_on=AS_OF,
        ),
        quantity_rule=QuantityRule(
            QuantityRuleKind.UNAVAILABLE,
            description="缺少组合约束",
        ),
        settlement_rule=_rule(),
    )


if __name__ == "__main__":
    unittest.main()
