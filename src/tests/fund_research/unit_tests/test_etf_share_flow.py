import unittest
from decimal import Decimal

from src.factor_engineering import FactorValue
from src.fund_research.share_flow import (
    EXCHANGE_FUND_FLOW_VERSION,
    ExchangeFundFlowService,
    ExchangeFundNavRecord,
    ExchangeFundShareRecord,
)


ASSET_ID = "asset:csi300-etf"


def share(
    stat_date,
    shares,
    *,
    source="unit-test:shares",
    split_adjusted=False,
):
    return ExchangeFundShareRecord(
        asset_id=ASSET_ID,
        stat_date=stat_date,
        shares=Decimal(shares),
        source=source,
        split_adjusted=split_adjusted,
    )


def nav(nav_date, unit_nav, *, source="unit-test:nav"):
    return ExchangeFundNavRecord(
        asset_id=ASSET_ID,
        nav_date=nav_date,
        unit_nav=Decimal(unit_nav),
        source=source,
    )


def factor(asset_id, factor_name, value):
    return FactorValue(
        asset_id=asset_id,
        factor_name=factor_name,
        as_of_date="2026-08-07",
        value=value,
        calculation_version=EXCHANGE_FUND_FLOW_VERSION,
    )


class ExchangeFundShareFlowTest(unittest.TestCase):
    def setUp(self):
        self.service = ExchangeFundFlowService()

    def test_calculates_latest_share_change_and_rejects_duplicates(self):
        values = self.service.calculate(
            ASSET_ID,
            "2026-08-07",
            [
                share("2026-08-03", "100"),
                share("2026-08-06", "120", source="current"),
                share("2026-08-08", "999"),
            ],
            [],
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].factor_name, "etf_share_change")
        self.assertAlmostEqual(values[0].value, 0.2)
        self.assertEqual(
            values[0].calculation_version,
            "exchange_fund_flow_v1",
        )
        self.assertEqual(
            values[0].source_dataset,
            "unit-test:shares,current",
        )
        with self.assertRaisesRegex(ValueError, "duplicate share date"):
            self.service.calculate(
                ASSET_ID,
                "2026-08-07",
                [
                    share("2026-08-03", "100"),
                    share("2026-08-06", "110"),
                    share("2026-08-06", "120"),
                ],
                [],
            )

    def test_calculates_negative_estimated_net_subscription(self):
        values = self.service.calculate(
            ASSET_ID,
            "2026-08-07",
            [share("2026-08-03", "100"), share("2026-08-07", "80")],
            [nav("2026-08-07", "2.5")],
        )
        by_name = {value.factor_name: value for value in values}

        self.assertAlmostEqual(by_name["etf_share_change"].value, -0.2)
        self.assertAlmostEqual(
            by_name["estimated_net_subscription"].value,
            -50.0,
        )

    def test_uses_latest_nav_not_later_than_share_stat_date(self):
        values = self.service.calculate(
            ASSET_ID,
            "2026-08-09",
            [share("2026-08-03", "100"), share("2026-08-07", "110")],
            [
                nav("2026-08-06", "2", source="older-nav"),
                nav("2026-08-08", "9", source="future-nav"),
            ],
        )
        estimated = next(
            value
            for value in values
            if value.factor_name == "estimated_net_subscription"
        )

        self.assertEqual(estimated.value, 20.0)
        self.assertEqual(estimated.parameters["nav_ref_date"], "2026-08-06")
        self.assertEqual(estimated.parameters["nav_ref_source"], "older-nav")
        self.assertNotIn("future-nav", estimated.source_dataset)

    def test_missing_eligible_nav_omits_estimated_subscription(self):
        values = self.service.calculate(
            ASSET_ID,
            "2026-08-08",
            [share("2026-08-03", "100"), share("2026-08-07", "110")],
            [nav("2026-08-08", "2")],
        )

        self.assertEqual(
            [value.factor_name for value in values],
            ["etf_share_change"],
        )
        self.assertIsNone(values[0].parameters["nav_ref_date"])
        self.assertIsNone(values[0].parameters["nav_ref_source"])

    def test_rejects_inconsistent_adjacent_split_flags(self):
        with self.assertRaisesRegex(ValueError, "split_adjusted"):
            self.service.calculate(
                ASSET_ID,
                "2026-08-07",
                [
                    share("2026-08-03", "100", split_adjusted=False),
                    share("2026-08-07", "200", split_adjusted=True),
                ],
                [nav("2026-08-07", "2")],
            )

    def test_estimated_parameters_are_explicit(self):
        values = self.service.calculate(
            ASSET_ID,
            "2026-08-07",
            [
                share("2026-08-03", "100", split_adjusted=True),
                share("2026-08-07", "110", split_adjusted=True),
            ],
            [nav("2026-08-07", "2", source="official-nav")],
        )

        for value in values:
            self.assertTrue(value.parameters["estimated"])
            self.assertIn("unit", value.parameters)
            self.assertIn("estimation_method", value.parameters)
            self.assertEqual(
                value.parameters["share_stat_date"],
                "2026-08-07",
            )
            self.assertEqual(
                value.parameters["nav_ref_date"],
                "2026-08-07",
            )
            self.assertEqual(
                value.parameters["nav_ref_source"],
                "official-nav",
            )
            self.assertTrue(value.parameters["split_adjusted"])
            self.assertEqual(
                value.parameters["disclaimer"],
                "estimated_not_official_net_subscription",
            )

    def test_aggregates_broad_basket_with_complete_weights(self):
        result = self.service.aggregate_broad_basket(
            "basket:broad-etf",
            "2026-08-07",
            [
                factor("asset:a", "etf_share_change", 0.1),
                factor("asset:b", "etf_share_change", -0.2),
            ],
            {
                "asset:a": Decimal("0.6"),
                "asset:b": Decimal("0.4"),
            },
        )

        self.assertEqual(result.basket_id, "basket:broad-etf")
        self.assertEqual(result.factor, "etf_share_change")
        self.assertAlmostEqual(result.value, -0.02)
        self.assertEqual(result.version, EXCHANGE_FUND_FLOW_VERSION)
        self.assertEqual(
            result.parameters["members"],
            ["asset:a", "asset:b"],
        )
        self.assertEqual(result.parameters["coverage_rate"], 1.0)
        self.assertNotIn("asset_id", result.parameters)

    def test_basket_rejects_missing_member_weight(self):
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            self.service.aggregate_broad_basket(
                "basket:broad-etf",
                "2026-08-07",
                [
                    factor("asset:a", "etf_share_change", 0.1),
                    factor("asset:b", "etf_share_change", -0.2),
                ],
                {"asset:a": Decimal("1")},
            )

    def test_records_require_decimal_iso_date_and_positive_value(self):
        with self.assertRaises(TypeError):
            ExchangeFundShareRecord(
                asset_id=ASSET_ID,
                stat_date="2026-08-07",
                shares=100,
                source="unit-test",
            )
        with self.assertRaises(ValueError):
            nav("2026/08/07", "1")
        with self.assertRaises(ValueError):
            share("2026-08-07", "0")


if __name__ == "__main__":
    unittest.main()
