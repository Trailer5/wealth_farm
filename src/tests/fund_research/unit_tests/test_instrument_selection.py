import unittest
from dataclasses import replace
from decimal import Decimal

from src.fund_research import (
    ETFInstrumentProfile,
    ETFInstrumentSelectionRequest,
    ETFInstrumentSelectionService,
    ETFSelectionObservation,
    official_etf_expense_schedule,
)


class ETFInstrumentSelectionServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = ETFInstrumentSelectionService()
        self.index_id = "index:cn_a:sh:000001"
        self.profiles = (
            _profile(
                "510210",
                "富国上证综指ETF",
                "富国基金",
                "2011-01-30",
                self.index_id,
            ),
            _profile(
                "510760",
                "国泰上证综合ETF",
                "国泰基金",
                "2020-08-21",
                self.index_id,
            ),
            _profile(
                "510300",
                "沪深300ETF",
                "华泰柏瑞基金",
                "2012-05-04",
                "index:cn_a:sh:000300",
            ),
        )
        self.observations = (
            _observation(
                "510210",
                amount="1616000000",
                aum="18394000000",
                fee="0.0060",
                tracking_error="0.012",
                history=3800,
            ),
            _observation(
                "510760",
                amount="465000000",
                aum="5002000000",
                fee="0.0021",
                tracking_error="0.010",
                history=1400,
            ),
            _observation(
                "510300",
                amount="5000000000",
                aum="100000000000",
                fee="0.0020",
                tracking_error="0.008",
                history=3000,
            ),
        )

    def test_short_execution_prefers_liquidity_long_holding_prefers_fee(self):
        short = self.service.select(
            _request(self.index_id, holding_days=30),
            self.profiles,
            self.observations,
        )
        long = self.service.select(
            _request(self.index_id, holding_days=365),
            self.profiles,
            self.observations,
        )

        self.assertEqual(short.selected_symbol, "510210")
        self.assertEqual(long.selected_symbol, "510760")
        csi300 = next(
            item
            for item in long.candidates
            if item.profile.standard_symbol == "510300"
        )
        self.assertFalse(csi300.eligible)
        self.assertIn(
            "underlying_index_mismatch",
            csi300.exclusion_reasons,
        )

    def test_capacity_and_point_in_time_gates_are_explicit(self):
        request = replace(
            _request(self.index_id, holding_days=30),
            order_notional=Decimal("30000000"),
        )
        stale = replace(
            self.observations[1],
            history_days=100,
            data_coverage=Decimal("0.8"),
        )
        decision = self.service.select(
            request,
            self.profiles,
            (
                self.observations[0],
                stale,
                self.observations[2],
            ),
        )

        self.assertEqual(decision.selected_symbol, "510210")
        candidate = next(
            item
            for item in decision.candidates
            if item.profile.standard_symbol == "510760"
        )
        self.assertIn(
            "history_insufficient",
            candidate.exclusion_reasons,
        )
        self.assertIn(
            "data_coverage_insufficient",
            candidate.exclusion_reasons,
        )
        self.assertIn(
            "order_capacity_insufficient",
            candidate.exclusion_reasons,
        )

    def test_long_holding_prefers_nav_tracking_error_when_available(self):
        observations = (
            replace(
                self.observations[0],
                annual_expense_rate=Decimal("0.0020"),
                price_tracking_error=Decimal("0.001"),
                nav_tracking_error=Decimal("0.020"),
            ),
            replace(
                self.observations[1],
                annual_expense_rate=Decimal("0.0020"),
                price_tracking_error=Decimal("0.020"),
                nav_tracking_error=Decimal("0.002"),
            ),
            self.observations[2],
        )

        decision = self.service.select(
            _request(self.index_id, holding_days=365),
            self.profiles,
            observations,
        )

        self.assertEqual(decision.selected_symbol, "510760")
        self.assertIn("nav_tracking_error", decision.selection_basis)

    def test_official_expense_catalog_covers_sse_composite_candidates(self):
        fuguo = official_etf_expense_schedule(
            asset_id="asset:etf:510210",
            standard_symbol="510210",
        )
        guotai = official_etf_expense_schedule(
            asset_id="asset:etf:510760",
            standard_symbol="510760",
        )

        self.assertEqual(
            fuguo.comprehensive_expense_ratio,
            Decimal("0.0060"),
        )
        self.assertEqual(
            guotai.comprehensive_expense_ratio,
            Decimal("0.0021"),
        )


def _profile(symbol, name, manager, inception, index_id):
    return ETFInstrumentProfile(
        asset_id=f"asset:etf:{symbol}",
        standard_symbol=symbol,
        legal_name=name,
        exchange="sh",
        manager=manager,
        underlying_index_asset_id=index_id,
        inception_date=inception,
    )


def _observation(
    symbol,
    *,
    amount,
    aum,
    fee,
    tracking_error,
    history,
):
    return ETFSelectionObservation(
        asset_id=f"asset:etf:{symbol}",
        as_of_date="2026-08-06",
        available_at="2026-08-06",
        history_days=history,
        median_amount_60d=Decimal(amount),
        aum=Decimal(aum),
        annual_expense_rate=Decimal(fee),
        price_tracking_error=Decimal(tracking_error),
        data_coverage=Decimal("0.99"),
        source_refs=("unit-test",),
    )


def _request(index_id, *, holding_days):
    return ETFInstrumentSelectionRequest(
        benchmark_asset_id=index_id,
        as_of_date="2026-08-07",
        expected_holding_days=holding_days,
        order_notional=Decimal("10000000"),
    )


if __name__ == "__main__":
    unittest.main()
