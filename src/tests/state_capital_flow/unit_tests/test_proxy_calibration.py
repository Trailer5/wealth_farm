import unittest
from decimal import Decimal

from src.state_capital_flow import (
    CORE_BROAD_ETF_CANDIDATES,
    STATE_ETF_CANDIDATE_CATALOG_VERSION,
    ConfirmedHoldingChange,
    ETFDailyObservation,
    ETFShareContinuityService,
    ETFShareUnitCoverage,
    ETFShareUnitEvent,
    StateETFCoverageService,
    StateHolderEvidence,
    StateProxyCalibrationService,
    WeeklyStateFlowMetric,
    assess_tracking_stop,
)


CORE_A = "state:central_huijin_investment"
CORE_B = "state:china_securities_finance"
AGGREGATOR_URL = "https://example.test/aggregator"
OFFICIAL_URL = "https://example.test/official"


class CandidateCoverageTest(unittest.TestCase):
    def test_catalog_is_unique_but_confirmation_is_evidence_driven(self):
        self.assertEqual(
            STATE_ETF_CANDIDATE_CATALOG_VERSION,
            "state_etf_candidate_catalog_v1",
        )
        self.assertGreaterEqual(len(CORE_BROAD_ETF_CANDIDATES), 10)
        self.assertEqual(
            len({item.symbol for item in CORE_BROAD_ETF_CANDIDATES}),
            len(CORE_BROAD_ETF_CANDIDATES),
        )
        candidates = CORE_BROAD_ETF_CANDIDATES[:3]
        report = StateETFCoverageService().evaluate(
            candidates,
            [
                _holder("a-1", candidates[0].asset_id, CORE_A),
                _holder("a-2", candidates[0].asset_id, CORE_B),
                _holder(
                    "b-aggregate",
                    candidates[1].asset_id,
                    CORE_A,
                    source_tier="aggregator",
                ),
                _holder("c-1", candidates[2].asset_id, CORE_A),
                _holder(
                    "future",
                    candidates[1].asset_id,
                    CORE_A,
                    published_at="2026-05-01",
                ),
            ],
            [
                _observation(candidates[0].asset_id, "2026-03-02"),
                _observation(candidates[0].asset_id, "2026-03-09"),
                _observation(candidates[1].asset_id, "2026-03-03"),
                _observation(candidates[1].asset_id, "2026-03-10"),
                _observation(candidates[2].asset_id, "2026-03-04"),
                _observation(
                    candidates[2].asset_id,
                    "2026-03-11",
                    available_at="2026-05-01",
                ),
            ],
            "2026-04-01",
        )

        self.assertEqual(report.candidate_count, 3)
        self.assertEqual(report.confirmed_count, 2)
        self.assertEqual(report.weekly_ready_count, 1)
        self.assertAlmostEqual(report.holder_coverage_ratio, 2 / 3)
        self.assertAlmostEqual(report.weekly_coverage_ratio, 1 / 3)
        statuses = {item.candidate.symbol: item for item in report.statuses}
        self.assertEqual(statuses[candidates[0].symbol].status, "confirmed")
        self.assertEqual(
            statuses[candidates[0].symbol].official_core_holder_count,
            2,
        )
        self.assertEqual(
            statuses[candidates[1].symbol].status,
            "holder_missing",
        )
        self.assertEqual(
            statuses[candidates[2].symbol].status,
            "weekly_observation_missing",
        )


class ShareContinuityTest(unittest.TestCase):
    def test_tiers_events_gaps_and_application_remain_distinct(self):
        service = ETFShareContinuityService()
        official = _coverage("official", "2026-01-01", "2026-03-31")
        aggregator = _coverage("aggregator", "2026-01-01", "2026-03-31")
        partial = _coverage("official", "2026-01-02", "2026-03-31")
        event = ETFShareUnitEvent(
            asset_id="asset:etf:test",
            event_date="2026-02-01",
            factor=Decimal("2"),
            source_tier="official",
            source="exchange",
            source_url=OFFICIAL_URL,
        )
        blocked_coverage = _coverage(
            "official",
            "2026-01-01",
            "2026-03-31",
            events=(event,),
        )

        official_result = service.assess(
            "asset:etf:test",
            "2026-01-01",
            "2026-03-31",
            [official],
        )
        aggregator_result = service.assess(
            "asset:etf:test",
            "2026-01-01",
            "2026-03-31",
            [aggregator],
        )
        blocked_result = service.assess(
            "asset:etf:test",
            "2026-01-01",
            "2026-03-31",
            [blocked_coverage],
        )
        uncovered_result = service.assess(
            "asset:etf:test",
            "2026-01-01",
            "2026-03-31",
            [partial],
        )

        self.assertEqual(official_result.status, "official_confirmed")
        self.assertEqual(aggregator_result.status, "aggregator_checked")
        self.assertEqual(blocked_result.status, "blocked")
        self.assertEqual(blocked_result.event_count, 1)
        self.assertEqual(uncovered_result.status, "uncovered")
        observation = _observation("asset:etf:test", "2026-03-09")
        self.assertTrue(
            service.apply_to_observation(
                observation,
                official_result,
            ).share_unit_continuity_confirmed
        )
        preconfirmed = ETFDailyObservation(
            instrument_asset_id=observation.instrument_asset_id,
            trade_date=observation.trade_date,
            available_at=observation.available_at,
            total_shares=observation.total_shares,
            nav=observation.nav,
            source=observation.source,
            share_unit_continuity_confirmed=True,
        )
        self.assertFalse(
            service.apply_to_observation(
                preconfirmed,
                aggregator_result,
            ).share_unit_continuity_confirmed
        )


class ProxyCalibrationTest(unittest.TestCase):
    def test_aggregates_entities_deduplicates_metrics_and_blocks_future_data(self):
        changes = [
            _change("a", CORE_A, "10", "20"),
            _change("a", CORE_B, "30", "35"),
            _change("b", CORE_A, "30", "20"),
            _change("c", CORE_A, "10", "15"),
            _change(
                "future",
                CORE_A,
                "1",
                "100",
                current_published_at="2026-05-01",
            ),
        ]
        weekly = [
            _metric(
                "2026-01-04",
                "2026-01-04",
                {"a": "5", "b": "2", "c": "0"},
            ),
            _metric(
                "2026-01-11",
                "2026-01-11",
                {"a": "10", "b": "3", "c": "0"},
            ),
            _metric(
                "2026-01-11",
                "2026-01-11",
                {"a": "10", "b": "3", "c": "0"},
                factor_name="state_held_etf_weekly_positive_breadth",
            ),
            _metric(
                "2026-01-18",
                "2026-01-18",
                {"a": "-2", "b": "1", "c": "0"},
            ),
            _metric(
                "2026-01-25",
                "2026-03-01",
                {"a": "-999", "b": "999", "c": "999"},
            ),
        ]

        result = StateProxyCalibrationService().calibrate(
            changes,
            weekly,
            "2026-04-01",
        )

        self.assertEqual(result.case_count, 3)
        self.assertEqual(result.match_count, 1)
        self.assertEqual(result.mismatch_count, 1)
        self.assertEqual(result.unknown_count, 1)
        self.assertEqual(result.direction_accuracy, 0.5)
        cases = {item.instrument: item for item in result.cases}
        self.assertEqual(cases["a"].confirmed_delta_shares, Decimal("15"))
        self.assertEqual(cases["a"].proxy_delta_shares, Decimal("13"))
        self.assertEqual(cases["a"].outcome, "match")
        self.assertEqual(cases["b"].proxy_delta_shares, Decimal("6"))
        self.assertEqual(cases["b"].outcome, "mismatch")
        self.assertEqual(cases["c"].outcome, "unknown")
        self.assertNotIn("future", cases)

    def test_stop_assessment_passes_and_reports_each_failed_threshold(self):
        candidates = CORE_BROAD_ETF_CANDIDATES[:2]
        coverage = StateETFCoverageService().evaluate(
            candidates,
            [
                _holder("one", candidates[0].asset_id, CORE_A),
                _holder("two", candidates[1].asset_id, CORE_A),
            ],
            [
                _observation(candidates[0].asset_id, "2026-03-02"),
                _observation(candidates[0].asset_id, "2026-03-09"),
                _observation(candidates[1].asset_id, "2026-03-03"),
                _observation(candidates[1].asset_id, "2026-03-10"),
            ],
            "2026-04-01",
        )
        calibration = StateProxyCalibrationService().calibrate(
            [
                _change("a", CORE_A, "10", "20"),
                _change("b", CORE_A, "20", "10"),
                _change("c", CORE_A, "5", "5"),
            ],
            [
                _metric(
                    "2026-01-04",
                    "2026-01-04",
                    {"a": "1", "b": "1", "c": "1"},
                )
            ],
            "2026-04-01",
        )

        passed = assess_tracking_stop(
            coverage,
            calibration,
            min_confirmed_etfs=2,
            min_calibration_cases=2,
            min_direction_accuracy=0.5,
        )
        failed = assess_tracking_stop(
            coverage,
            calibration,
            min_confirmed_etfs=3,
            min_calibration_cases=4,
            min_direction_accuracy=0.75,
        )

        self.assertTrue(passed.ready_to_stop)
        self.assertEqual(passed.failure_reasons, ())
        self.assertTrue(passed.parameters["research_assumption"])
        self.assertFalse(failed.ready_to_stop)
        self.assertEqual(
            set(failed.failure_reasons),
            {
                "insufficient_confirmed_etfs",
                "insufficient_weekly_ready_etfs",
                "insufficient_calibration_cases",
                "direction_accuracy_below_threshold",
            },
        )


def _holder(
    evidence_id,
    asset_id,
    entity_id,
    *,
    source_tier="official",
    published_at="2026-03-01",
):
    return StateHolderEvidence(
        evidence_id=evidence_id,
        entity_id=entity_id,
        instrument_asset_id=asset_id,
        report_period="2025-annual",
        holding_date="2025-12-31",
        published_at=published_at,
        holding_shares=Decimal("100"),
        holding_ratio=None,
        source_tier=source_tier,
        source="unit-test",
        source_url=(
            OFFICIAL_URL if source_tier == "official" else AGGREGATOR_URL
        ),
    )


def _observation(asset_id, trade_date, *, available_at=None):
    return ETFDailyObservation(
        instrument_asset_id=asset_id,
        trade_date=trade_date,
        available_at=available_at or trade_date,
        total_shares=Decimal("100"),
        nav=Decimal("1"),
        source="unit-test",
    )


def _coverage(source_tier, start, end, *, events=()):
    return ETFShareUnitCoverage(
        asset_id="asset:etf:test",
        coverage_start=start,
        coverage_end=end,
        source_tier=source_tier,
        source="exchange" if source_tier == "official" else "aggregator",
        source_url=(
            OFFICIAL_URL if source_tier == "official" else AGGREGATOR_URL
        ),
        events=events,
    )


def _change(
    instrument,
    entity,
    previous,
    current,
    *,
    current_published_at="2026-02-20",
):
    return ConfirmedHoldingChange(
        entity_id=entity,
        instrument_asset_id=instrument,
        previous_holding_shares=Decimal(previous),
        current_holding_shares=Decimal(current),
        previous_report_period="2025-half",
        current_report_period="2025-annual",
        previous_holding_date="2026-01-01",
        current_holding_date="2026-01-31",
        previous_published_at="2026-02-01",
        current_published_at=current_published_at,
        delta_shares=Decimal(current) - Decimal(previous),
        available_at=current_published_at,
    )


def _metric(week_end, available_at, details, *, factor_name=None):
    end = __import__("datetime").date.fromisoformat(week_end)
    start = end - __import__("datetime").timedelta(days=6)
    return WeeklyStateFlowMetric(
        basket_id="basket:test",
        factor_name=(
            factor_name or "state_held_etf_weekly_anonymous_flow_cny"
        ),
        week_start=start.isoformat(),
        week_end=end.isoformat(),
        estimated_for_date=end.isoformat(),
        available_at=available_at,
        value=1.0,
        parameters={
            "members": sorted(details),
            "sample_count": len(details),
            "eligible_member_count": len(details),
            "coverage_ratio": 1.0,
            "identity_confirmed_by_holder_report": True,
            "identity_inference": False,
            "not_confirmed_state_flow": True,
            "member_details": {
                instrument: {"delta_total_shares": delta}
                for instrument, delta in details.items()
            },
        },
    )


if __name__ == "__main__":
    unittest.main()
