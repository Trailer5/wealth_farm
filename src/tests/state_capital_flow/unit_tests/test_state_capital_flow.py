import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.data_src.models import DocumentTable
from src.state_capital_flow import (
    DEFAULT_STATE_ENTITY_REGISTRY,
    ETFDailyObservation,
    OfficialHolderTableParser,
    SQLiteStateCapitalFlowRepository,
    StateCapitalFlowTracker,
    StateHolderEvidence,
    StatePurchaseAnnouncement,
)


CORE_ID = "state:central_huijin_investment"
SECOND_CORE_ID = "state:china_securities_finance"
LONG_TERM_ID = "state:national_social_security_fund_council"
SOURCE_URL = "https://example.test/official.pdf"


class StateEntityRegistryAndParserTest(unittest.TestCase):
    def test_exact_alias_matching_keeps_long_term_entities_out_of_core(self):
        registry = DEFAULT_STATE_ENTITY_REGISTRY

        self.assertEqual(
            registry.match("  中央 汇金  ").entity_id,
            CORE_ID,
        )
        self.assertEqual(
            registry.match("中国证券金融股份有限公司").entity_id,
            SECOND_CORE_ID,
        )
        self.assertIsNone(registry.match("中国证券金融股份有限公司股东账户"))
        social_security = registry.match("全国社会保障基金理事会")
        self.assertEqual(social_security.entity_id, LONG_TERM_ID)
        self.assertEqual(social_security.group, "state_long_term")
        self.assertFalse(registry.is_core(LONG_TERM_ID))

    def test_chinese_official_table_normalizes_percent_and_isolates_bad_rows(
        self,
    ):
        parser = OfficialHolderTableParser()
        rows = [
            {
                "持有人名称": "中央汇金",
                "持有份额": "1,000",
                "占总份额比例": "12.5%",
            },
            {
                "持有人": "中国证券金融股份有限公司股东账户",
                "持股数量": "200",
                "持股比例": "2",
            },
            {
                "持有人": "证金公司",
                "持股数量": "错误",
                "持股比例": "2",
            },
            {"持有人": "普通机构", "持股数量": "999"},
        ]
        first = parser.parse(
            rows,
            "asset:etf-a",
            "2025-annual",
            "2025-12-31",
            "2026-03-31",
            "fund:annual-report",
            SOURCE_URL,
        )
        second = parser.parse(
            rows,
            "asset:etf-a",
            "2025-annual",
            "2025-12-31",
            "2026-03-31",
            "fund:annual-report",
            SOURCE_URL,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].holding_shares, Decimal("1000"))
        self.assertEqual(first[0].holding_ratio, Decimal("0.125"))
        self.assertEqual(first[0].source_tier, "official")
        self.assertTrue(
            first[0].parameters["holding_ratio_normalized_from_percent"]
        )
        self.assertEqual(first[0].evidence_id, second[0].evidence_id)

    def test_official_pdf_table_keeps_page_and_row_provenance(self):
        tables = [
            DocumentTable(
                document_id="sse:510300:2025-annual",
                table_index=9,
                page_number=73,
                rows=[
                    ["序号", "持有人名称", "持有份额（份）", "占总份额比例（%）"],
                    ["1", "中央汇金投资有限责任公司", "35,654,598,859", "40.14"],
                    ["2", "普通机构", "100", "0.01"],
                ],
                extractor="pdfplumber",
                status="extracted",
            )
        ]

        evidence = OfficialHolderTableParser().parse_document_tables(
            tables,
            "asset:etf:510300",
            "2025-annual",
            "2025-12-31",
            "2026-03-31",
            "sse:fund_annual_report",
            "https://www.sse.com.cn/report.pdf",
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            evidence[0].holding_shares,
            Decimal("35654598859"),
        )
        self.assertEqual(evidence[0].holding_ratio, Decimal("0.4014"))
        self.assertEqual(evidence[0].parameters["table_index"], 9)
        self.assertEqual(evidence[0].parameters["row_index"], 1)
        self.assertEqual(evidence[0].parameters["page_number"], 73)


class StateCapitalFlowTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = StateCapitalFlowTracker()

    def test_confirmed_change_uses_two_visible_official_periods_only(self):
        evidence = [
            _holder(
                "old",
                "asset:etf-a",
                "2025-half",
                "2025-06-30",
                "2025-08-30",
                "100",
                "0.10",
            ),
            _holder(
                "current",
                "asset:etf-a",
                "2025-annual",
                "2025-12-31",
                "2026-03-31",
                "130",
                "0.12",
            ),
            _holder(
                "future",
                "asset:etf-a",
                "2026-half",
                "2026-06-30",
                "2026-08-31",
                "200",
                "0.15",
            ),
            _holder(
                "aggregate",
                "asset:etf-a",
                "2026-q1",
                "2026-03-31",
                "2026-04-30",
                "999",
                "0.99",
                source_tier="aggregator",
            ),
            _holder(
                "single-only",
                "asset:etf-b",
                "2025-annual",
                "2025-12-31",
                "2026-03-31",
                "50",
                "0.05",
            ),
        ]

        changes = self.tracker.confirmed_holding_changes(
            evidence,
            "2026-08-09",
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].instrument_asset_id, "asset:etf-a")
        self.assertEqual(changes[0].delta_shares, Decimal("30"))
        self.assertEqual(changes[0].available_at, "2026-03-31")
        self.assertEqual(changes[0].evidence_tier, "confirmed_interval")
        history = self.tracker.confirmed_holding_changes(
            evidence,
            "2026-09-01",
            all_intervals=True,
        )
        self.assertEqual(
            [item.delta_shares for item in history],
            [Decimal("30"), Decimal("70")],
        )

    def test_three_etf_weekly_proxy_is_hand_calculable(self):
        metrics = self.tracker.weekly_metrics(
            "basket:state-held",
            _three_etf_observations(),
            _three_etf_holders(),
            [],
            "2026-08-09",
        )
        values = {metric.factor_name: metric for metric in metrics}

        self.assertAlmostEqual(
            values["state_held_etf_weekly_anonymous_flow_cny"].value,
            -50,
        )
        weighted = values["state_exposure_weighted_weekly_proxy_cny"]
        self.assertAlmostEqual(weighted.value, -18)
        self.assertTrue(weighted.parameters["pro_rata_assumption"])
        self.assertAlmostEqual(
            values["state_held_etf_weekly_positive_breadth"].value,
            2 / 3,
        )
        for metric in metrics:
            self.assertEqual(metric.week_start, "2026-08-03")
            self.assertEqual(metric.week_end, "2026-08-09")
            self.assertEqual(metric.available_at, "2026-08-08")
            self.assertEqual(metric.parameters["eligible_member_count"], 3)
            self.assertEqual(metric.parameters["coverage_ratio"], 1)
            self.assertTrue(
                metric.parameters["identity_confirmed_by_holder_report"]
            )
            self.assertFalse(metric.parameters["identity_inference"])
            self.assertTrue(metric.parameters["not_confirmed_state_flow"])
        self.assertEqual(
            values[
                "state_held_etf_weekly_anonymous_flow_cny"
            ].parameters["sample_count"],
            3,
        )
        self.assertEqual(weighted.parameters["sample_count"], 2)
        self.assertEqual(
            values[
                "state_held_etf_weekly_positive_breadth"
            ].parameters["sample_count"],
            3,
        )
        self.assertEqual(
            values[
                "state_held_etf_weekly_positive_breadth"
            ].parameters["member_details"]["asset:etf-a"][
                "delta_total_shares"
            ],
            "10",
        )

    def test_aggregator_cannot_enter_and_gap_over_14_days_is_excluded(self):
        holders = [
            _holder(
                "a",
                "asset:etf-a",
                "2025-annual",
                "2025-12-31",
                "2026-08-01",
                "10",
                "0.1",
            ),
            _holder(
                "gap",
                "asset:etf-gap",
                "2025-annual",
                "2025-12-31",
                "2026-08-01",
                "10",
                "0.1",
            ),
            _holder(
                "aggregator",
                "asset:etf-aggregate",
                "2025-annual",
                "2025-12-31",
                "2026-08-01",
                "10",
                "0.1",
                source_tier="aggregator",
            ),
        ]
        observations = [
            _observation("asset:etf-a", "2026-07-31", "100", "1"),
            _observation("asset:etf-a", "2026-08-07", "110", "1"),
            _observation("asset:etf-gap", "2026-07-17", "100", "1"),
            _observation("asset:etf-gap", "2026-08-07", "110", "1"),
            _observation("asset:etf-aggregate", "2026-07-31", "100", "1"),
            _observation("asset:etf-aggregate", "2026-08-07", "1000", "1"),
        ]

        metrics = self.tracker.weekly_metrics(
            "basket:test",
            observations,
            holders,
            [],
            "2026-08-09",
        )

        anonymous = next(
            metric
            for metric in metrics
            if metric.factor_name
            == "state_held_etf_weekly_anonymous_flow_cny"
        )
        self.assertEqual(anonymous.value, 10)
        self.assertEqual(
            anonymous.parameters["members"],
            ["asset:etf-a", "asset:etf-gap"],
        )
        self.assertEqual(anonymous.parameters["eligible_member_count"], 2)
        self.assertEqual(anonymous.parameters["sample_count"], 1)
        self.assertEqual(anonymous.parameters["coverage_ratio"], 0.5)

    def test_official_buy_flag_does_not_change_anonymous_amount(self):
        arguments = (
            "basket:state-held",
            _three_etf_observations(),
            _three_etf_holders(),
        )
        without_announcement = self.tracker.weekly_metrics(
            *arguments,
            [],
            "2026-08-09",
        )
        with_announcement = self.tracker.weekly_metrics(
            *arguments,
            [
                StatePurchaseAnnouncement(
                    announcement_id="announcement:buy",
                    entity_id=CORE_ID,
                    published_at="2026-08-06",
                    event_start_date="2026-08-03",
                    event_end_date="2026-08-31",
                    direction="buy",
                    exact_amount_cny=None,
                    exact_shares=None,
                    source_url=SOURCE_URL,
                )
            ],
            "2026-08-09",
        )
        without_values = {
            metric.factor_name: metric.value for metric in without_announcement
        }
        with_values = {
            metric.factor_name: metric for metric in with_announcement
        }

        self.assertEqual(
            with_values[
                "state_held_etf_weekly_anonymous_flow_cny"
            ].value,
            without_values["state_held_etf_weekly_anonymous_flow_cny"],
        )
        flag = with_values["state_purchase_confirmed_flag"]
        self.assertEqual(flag.value, 1)
        self.assertEqual(flag.evidence_tier, "confirmed_direction")

    def test_minimum_reduction_lower_bound_is_hand_calculable_and_separate(
        self,
    ):
        holders = [
            _holder(
                "holder-core-a",
                "asset:etf-a",
                "2025-annual",
                "2025-12-31",
                "2026-03-31",
                "500",
                None,
            ),
            _holder(
                "holder-core-b",
                "asset:etf-a",
                "2025-annual",
                "2025-12-31",
                "2026-03-31",
                "235",
                None,
                entity_id=SECOND_CORE_ID,
            ),
        ]

        def lower_bound(previous_shares, current_shares, confirmed=True):
            metrics = self.tracker.weekly_metrics(
                "basket:lower-bound",
                [
                    _observation(
                        "asset:etf-a",
                        "2026-07-31",
                        previous_shares,
                        "1",
                    ),
                    _observation(
                        "asset:etf-a",
                        "2026-08-07",
                        current_shares,
                        "1",
                        continuity_confirmed=confirmed,
                    ),
                ],
                holders,
                [],
                "2026-08-09",
            )
            return next(
                (
                    metric
                    for metric in metrics
                    if metric.factor_name
                    == "state_minimum_reduction_since_disclosure_shares"
                ),
                None,
            )

        positive_proxy = lower_bound("600", "700")
        negative_proxy = lower_bound("900", "700")
        zero = lower_bound("900", "800")
        unconfirmed = lower_bound("900", "700", confirmed=False)

        self.assertEqual(positive_proxy.value, 35)
        self.assertEqual(negative_proxy.value, 35)
        self.assertEqual(zero.value, 0)
        self.assertIsNone(unconfirmed)
        self.assertEqual(
            positive_proxy.evidence_tier,
            "confirmed_lower_bound",
        )
        self.assertEqual(positive_proxy.available_at, "2026-08-07")
        self.assertTrue(
            positive_proxy.parameters["confirmed_as_lower_bound"]
        )
        self.assertTrue(
            positive_proxy.parameters["not_confirmed_state_flow"]
        )
        details = positive_proxy.parameters["member_details"]["asset:etf-a"]
        self.assertEqual(details["disclosed_core_holding_shares"], "735")
        self.assertEqual(details["current_total_shares"], "700")
        self.assertEqual(details["minimum_reduction_shares"], "35")
        self.assertEqual(
            details["holder_evidence_ids"],
            ["holder-core-a", "holder-core-b"],
        )
        self.assertTrue(details["continuity_confirmed"])

    def test_share_unit_continuity_requires_strict_bool(self):
        with self.assertRaises(TypeError):
            _observation(
                "asset:etf-a",
                "2026-08-07",
                "700",
                "1",
                continuity_confirmed=1,
            )


class SQLiteStateCapitalFlowRepositoryTest(unittest.TestCase):
    def test_upserts_are_idempotent_and_json_is_stable_without_asset_fk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state-flow.sqlite3"
            repository = SQLiteStateCapitalFlowRepository(db_path)
            repository.initialize()
            holder = _three_etf_holders()[0]
            metrics = StateCapitalFlowTracker().weekly_metrics(
                "basket:state-held",
                _three_etf_observations(),
                _three_etf_holders(),
                [],
                "2026-08-09",
            )

            repository.upsert_holder_evidence([holder])
            repository.upsert_holder_evidence([holder])
            repository.upsert_weekly_flow_metrics(metrics)
            repository.upsert_weekly_flow_metrics(metrics)

            stored_holders = repository.list_holder_evidence()
            stored_metrics = repository.list_weekly_flow_metrics(
                basket_id="basket:state-held"
            )
            connection = sqlite3.connect(db_path)
            try:
                holder_fks = connection.execute(
                    "PRAGMA foreign_key_list(state_holder_evidence)"
                ).fetchall()
                metric_fks = connection.execute(
                    "PRAGMA foreign_key_list(state_weekly_flow_metrics)"
                ).fetchall()
                parameters_json = connection.execute(
                    """
                    SELECT parameters_json
                    FROM state_weekly_flow_metrics
                    ORDER BY factor_name
                    LIMIT 1
                    """
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(stored_holders, [holder])
            self.assertEqual(len(stored_metrics), len(metrics))
            self.assertEqual(holder_fks, [])
            self.assertEqual(metric_fks, [])
            self.assertEqual(
                parameters_json,
                json.dumps(
                    json.loads(parameters_json),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )


def _holder(
    evidence_id,
    asset_id,
    report_period,
    holding_date,
    published_at,
    shares,
    ratio,
    *,
    entity_id=CORE_ID,
    source_tier="official",
):
    return StateHolderEvidence(
        evidence_id=evidence_id,
        entity_id=entity_id,
        instrument_asset_id=asset_id,
        report_period=report_period,
        holding_date=holding_date,
        published_at=published_at,
        holding_shares=Decimal(shares),
        holding_ratio=None if ratio is None else Decimal(ratio),
        source_tier=source_tier,
        source="unit-test:holder-report",
        source_url=SOURCE_URL,
    )


def _observation(
    asset_id,
    trade_date,
    shares,
    nav,
    *,
    available_at=None,
    continuity_confirmed=False,
):
    return ETFDailyObservation(
        instrument_asset_id=asset_id,
        trade_date=trade_date,
        available_at=available_at or trade_date,
        total_shares=Decimal(shares),
        nav=None if nav is None else Decimal(nav),
        source="unit-test:exchange",
        share_unit_continuity_confirmed=continuity_confirmed,
    )


def _three_etf_holders():
    return [
        _holder(
            "holder-a",
            "asset:etf-a",
            "2025-annual",
            "2025-12-31",
            "2026-08-01",
            "100",
            "0.10",
        ),
        _holder(
            "holder-b",
            "asset:etf-b",
            "2025-annual",
            "2025-12-31",
            "2026-08-03",
            "100",
            "0.20",
        ),
        _holder(
            "holder-c",
            "asset:etf-c",
            "2025-annual",
            "2025-12-31",
            "2026-08-05",
            "100",
            None,
        ),
    ]


def _three_etf_observations():
    return [
        _observation("asset:etf-a", "2026-07-31", "100", "2"),
        _observation("asset:etf-a", "2026-08-07", "110", "2"),
        _observation("asset:etf-b", "2026-07-31", "200", "5"),
        _observation(
            "asset:etf-b",
            "2026-08-07",
            "180",
            "5",
            available_at="2026-08-08",
        ),
        _observation("asset:etf-c", "2026-07-31", "50", "1.5"),
        _observation("asset:etf-c", "2026-08-07", "70", "1.5"),
    ]


if __name__ == "__main__":
    unittest.main()
