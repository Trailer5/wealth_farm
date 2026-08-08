import unittest

from src.factor_engineering.market_event import (
    CAPITAL_FLOW_VERSION,
    MARKET_EVENT_VERSION,
    CapitalFlowDisclosureRecord,
    CapitalFlowFactorCalculator,
    MarketActivityRecord,
    MarketEventFactorCalculator,
)


class MarketActivityRecordTest(unittest.TestCase):
    def test_dates_types_and_numeric_domains_are_validated(self):
        signed = _market_record(
            net_buy_amount=-20.0,
            institution_net_buy_amount=-3.0,
            broker_branch_net_buy_amount=-17.0,
        )

        self.assertEqual(signed.net_buy_amount, -20.0)
        with self.assertRaisesRegex(ValueError, "ISO date"):
            _market_record(trade_date="2026/01/02")
        with self.assertRaisesRegex(ValueError, "non-negative"):
            _market_record(amount=-1.0)
        with self.assertRaisesRegex(ValueError, "activity_type"):
            _market_record(activity_type="rumored_fund")


class MarketEventFactorCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = MarketEventFactorCalculator()

    def test_future_disclosure_is_excluded(self):
        records = [
            _market_record(
                activity_type="buyback",
                trade_date="2026-01-09",
                published_date="2026-01-09",
            ),
            _market_record(
                activity_type="holding_change",
                trade_date="2026-01-09",
                published_date="2026-01-11",
            ),
        ]

        factors = _by_name(
            self.calculator.calculate(
                "asset:test",
                records,
                "2026-01-10",
                5,
            )
        )

        self.assertEqual(
            set(factors),
            {"disclosure_buyback_flag"},
        )
        self.assertEqual(
            factors["disclosure_buyback_flag"].calculation_version,
            MARKET_EVENT_VERSION,
        )

    def test_event_window_is_calendar_based_and_not_forward_filled(self):
        records = [
            _market_record(
                trade_date="2026-01-01",
                published_date="2026-01-01",
            ),
            _market_record(
                trade_date="2026-01-10",
                published_date="2026-01-10",
            ),
        ]

        current = _by_name(
            self.calculator.calculate(
                "asset:test",
                records,
                "2026-01-10",
                3,
            )
        )
        expired = self.calculator.calculate(
            "asset:test",
            records,
            "2026-01-14",
            3,
        )

        self.assertEqual(current["lhb_listing_count"].value, 1.0)
        self.assertEqual(
            current["lhb_listing_count"].input_start_date,
            "2026-01-10",
        )
        self.assertEqual(expired, [])

    def test_seat_amounts_are_never_inferred_from_total_net_buy(self):
        records = [
            _market_record(
                net_buy_amount=30.0,
                turnover_amount=100.0,
                metadata={"branch_label": "well-known-seat"},
            )
        ]

        factors = _by_name(
            self.calculator.calculate(
                "asset:test",
                records,
                "2026-01-02",
                5,
            )
        )

        self.assertAlmostEqual(
            factors["lhb_net_buy_ratio"].value,
            0.3,
        )
        self.assertNotIn(
            "institution_seat_net_buy_ratio",
            factors,
        )
        self.assertNotIn("broker_branch_net_buy_ratio", factors)
        for factor in factors.values():
            self.assertIs(
                factor.parameters["identity_inference"],
                False,
            )

    def test_block_trade_discount_has_documented_weighting(self):
        records = [
            _market_record(
                activity_type="block_trade",
                amount=100.0,
                turnover_amount=100.0,
                trade_price=9.0,
                reference_close=10.0,
                float_market_cap=1_000.0,
            ),
            _market_record(
                activity_type="block_trade",
                amount=300.0,
                turnover_amount=300.0,
                trade_price=10.0,
                reference_close=10.0,
                float_market_cap=1_000.0,
            ),
        ]

        factors = _by_name(
            self.calculator.calculate(
                "asset:test",
                records,
                "2026-01-02",
                5,
            )
        )

        self.assertAlmostEqual(
            factors["block_trade_discount"].value,
            0.025,
        )
        self.assertEqual(
            factors["block_trade_discount"].parameters["weight"],
            "turnover_amount_then_amount_then_equal",
        )
        self.assertAlmostEqual(
            factors["block_trade_float_cap_ratio"].value,
            0.4,
        )


class CapitalFlowFactorCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = CapitalFlowFactorCalculator()

    def test_northbound_break_rejects_post_break_net_flow(self):
        record = _flow_record(
            scope="northbound",
            observation_date="2024-08-19",
            published_date="2024-08-19",
            regime="daily_turnover_post_20240819",
            net_flow=1.0,
            turnover=100.0,
        )

        with self.assertRaisesRegex(
            ValueError,
            "northbound net_flow is unavailable",
        ):
            self.calculator.calculate(
                "connect:northbound",
                [record],
                "2024-08-19",
            )

    def test_northbound_regimes_and_quarterly_publication_date(self):
        records = [
            _flow_record(
                scope="northbound",
                observation_date="2024-08-16",
                published_date="2024-08-16",
                regime="daily_net_flow_pre_20240819",
                net_flow=-12.0,
            ),
            _flow_record(
                scope="northbound",
                observation_date="2024-08-19",
                published_date="2024-08-19",
                regime="daily_turnover_post_20240819",
                turnover=200.0,
                top10_turnover=50.0,
            ),
            _flow_record(
                scope="northbound",
                observation_date="2024-09-30",
                published_date="2024-10-31",
                regime="quarterly_holding",
                holding_ratio=0.04,
            ),
        ]

        before_publication = _by_name(
            self.calculator.calculate(
                "connect:northbound",
                records,
                "2024-10-30",
            )
        )
        after_publication = _by_name(
            self.calculator.calculate(
                "connect:northbound",
                records,
                "2024-10-31",
            )
        )

        self.assertEqual(
            before_publication[
                "northbound_net_flow_pre_20240819"
            ].value,
            -12.0,
        )
        self.assertEqual(
            before_publication["northbound_turnover"].value,
            200.0,
        )
        self.assertAlmostEqual(
            before_publication[
                "northbound_top10_concentration"
            ].value,
            0.25,
        )
        self.assertNotIn(
            "northbound_quarterly_holding_ratio",
            before_publication,
        )
        quarterly = after_publication[
            "northbound_quarterly_holding_ratio"
        ]
        self.assertEqual(quarterly.value, 0.04)
        self.assertEqual(quarterly.published_date, "2024-10-31")
        self.assertEqual(
            quarterly.calculation_version,
            CAPITAL_FLOW_VERSION,
        )

    def test_southbound_is_context_not_direct_a_share_flow(self):
        record = _flow_record(
            scope="southbound",
            observation_date="2026-01-02",
            published_date="2026-01-02",
            regime="daily_net_flow",
            net_flow=-8.0,
        )

        factor = _by_name(
            self.calculator.calculate(
                "connect:southbound",
                [record],
                "2026-01-02",
            )
        )["southbound_net_flow"]

        self.assertEqual(factor.value, -8.0)
        self.assertIs(
            factor.parameters["a_share_direct_flow"],
            False,
        )
        self.assertFalse(hasattr(factor, "asset_id"))

    def test_state_holder_requires_disclosed_holder_regime(self):
        with self.assertRaisesRegex(
            ValueError,
            "regime=disclosed_holder",
        ):
            _flow_record(
                scope="state_holder",
                regime="quarterly_holding",
                holding_ratio=0.03,
            )

        valid = _flow_record(
            scope="state_holder",
            regime="disclosed_holder",
            holding_ratio=0.03,
            source="official-quarterly-report",
        )
        factor = _by_name(
            self.calculator.calculate(
                "holder:state",
                [valid],
                "2026-01-02",
            )
        )["state_disclosed_holder_ratio"]
        self.assertEqual(
            factor.parameters["identity_source"],
            "official_disclosure",
        )
        self.assertEqual(factor.source, "official-quarterly-report")


def _market_record(**overrides):
    values = {
        "asset_id": "asset:test",
        "activity_type": "lhb_listing",
        "trade_date": "2026-01-02",
        "published_date": "2026-01-02",
        "source": "unit-test",
    }
    values.update(overrides)
    return MarketActivityRecord(**values)


def _flow_record(**overrides):
    values = {
        "scope": "northbound",
        "observation_date": "2026-01-02",
        "published_date": "2026-01-02",
        "regime": "daily_turnover_post_20240819",
        "source": "unit-test",
    }
    values.update(overrides)
    return CapitalFlowDisclosureRecord(**values)


def _by_name(values):
    return {value.factor_name: value for value in values}


if __name__ == "__main__":
    unittest.main()
