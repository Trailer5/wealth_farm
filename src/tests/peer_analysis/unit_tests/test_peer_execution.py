import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.peer_analysis import (
    IndustryAssignment,
    PeerMetricExecutionService,
    PeerSet,
    PeerSetMember,
)
from src.research_workflow import ResearchExecutionOptions


class PeerMetricExecutionTest(unittest.TestCase):
    def setUp(self):
        self.peer_set = PeerSet(
            peer_set_id="peer:test",
            target_asset_id="asset:target",
            name="Test peers",
            source="manual_review",
            version="v1",
            effective_date="2026-01-01",
            members=(
                PeerSetMember("asset:peer1", True, "included"),
                PeerSetMember("asset:peer2", True, "included"),
                PeerSetMember("asset:excluded", False, "excluded"),
            ),
        )
        self.industry = IndustryAssignment(
            asset_id="asset:target",
            industry_code="test",
            industry_name="Test",
            classification_source="manual_review",
            classification_version="v1",
            effective_date="2026-01-01",
        )

    def test_uses_latest_common_period_for_included_assets(self):
        data_access = _data_access(
            {
                "asset:target": ("2024-12-31", "2025-12-31"),
                "asset:peer1": ("2024-12-31", "2025-12-31"),
                "asset:peer2": ("2023-12-31", "2025-12-31"),
            }
        )
        fundamental = Mock()
        peer = Mock()
        peer.save_peer_set.side_effect = lambda value: value
        peer.compare.return_value = SimpleNamespace(
            comparisons=()
        )
        result = PeerMetricExecutionService(
            data_access=data_access,
            fundamental_service=fundamental,
            peer_service=peer,
        ).execute(
            peer_set=self.peer_set,
            industry=self.industry,
            as_of_date="2026-08-03",
            metric_names=("roe",),
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.common_report_period,
            "2025-12-31",
        )
        self.assertEqual(len(result.prepared_asset_ids), 3)
        self.assertTrue(
            all(
                call.kwargs["target_report_period"]
                == "2025-12-31"
                for call in fundamental.analyze.call_args_list
            )
        )
        requested_ids = {
            call.kwargs["asset_id"]
            for call in data_access.get_financial_bundle.call_args_list
        }
        self.assertNotIn("asset:excluded", requested_ids)

    def test_no_common_period_fails_before_calculation(self):
        data_access = _data_access(
            {
                "asset:target": ("2025-12-31",),
                "asset:peer1": ("2024-12-31",),
                "asset:peer2": ("2023-12-31",),
            }
        )
        fundamental = Mock()
        with self.assertRaisesRegex(
            ValueError,
            "no common report period",
        ):
            PeerMetricExecutionService(
                data_access=data_access,
                fundamental_service=fundamental,
                peer_service=Mock(),
            ).execute(
                peer_set=self.peer_set,
                industry=self.industry,
                as_of_date="2026-08-03",
                metric_names=("roe",),
                options=ResearchExecutionOptions(
                    allow_remote=False
                ),
            )
        fundamental.analyze.assert_not_called()


def _data_access(periods_by_asset):
    service = Mock()

    def bundle(asset_id, **kwargs):
        periods = periods_by_asset[asset_id]
        return SimpleNamespace(
            period_summary=lambda: SimpleNamespace(
                available_periods=periods
            )
        )

    service.get_financial_bundle.side_effect = bundle
    service.get.side_effect = lambda request: SimpleNamespace(
        records=(
            {
                "trade_date": "2026-08-03",
                "close": 10,
            },
        )
    )
    return service


if __name__ == "__main__":
    unittest.main()
