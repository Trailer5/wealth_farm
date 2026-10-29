import unittest

from src.research_acceptance.report_service import _valuation_position_facts
from src.valuation_position import ValuationPositionService


def _row(day, pe, pb=5.0, ps=8.0):
    return {"trade_date": day, "pe_ttm": pe, "pb": pb, "ps": ps}


class FakeCrossSection:
    def __init__(self, series):
        self.series = series

    def valuation_history(self, symbols, *, start_date, end_date, allow_remote=True):
        return {
            "series": {s: [r for r in self.series.get(s, []) if start_date <= r["trade_date"] <= end_date] for s in symbols},
            "provider_errors": {},
        }


class FakeSector:
    def peers(self, symbol, allow_remote=True):
        level3 = {"code": "851251", "name": "白酒Ⅲ"}
        return {
            "path": {"l1": {"name": "食品饮料"}, "l2": {"name": "白酒Ⅱ"}, "l3": level3},
            "peer_industry": level3,
            "peers": [{"symbol": f"00000{i}", "name": f"同行{i}"} for i in range(1, 7)],
        }


class ValuationPositionTest(unittest.TestCase):
    def setUp(self):
        own = [
            _row("2025-10-31", 30.0),
            _row("2025-11-28", 25.0),
            _row("2025-12-31", -5.0),
            _row("2026-01-30", 20.0),
            _row("2026-10-23", 22.0),
            _row("2026-10-26", 21.0),
        ]
        peers = {f"00000{i}": [_row("2026-10-23", value)] for i, value in enumerate((10, 15, 18, 25, 40, -3), 1)}
        self.service = ValuationPositionService(
            cross_section=FakeCrossSection({"600519": own, **peers}),
            sector_data=FakeSector(),
        )

    def test_own_history_uses_month_end_samples_and_skips_losses(self):
        result = self.service.position("600519", as_of_date="2026-10-26")

        pe = result["own_history"]["pe_ttm"]
        # Month-end samples 30, 25, 20 and the latest 21; the -5 loss month is counted apart.
        self.assertEqual(pe["sample_count"], 4)
        self.assertEqual(pe["percentile"], 0.5)
        self.assertEqual(pe["non_positive_samples"], 1)
        self.assertEqual(pe["current_date"], "2026-10-26")

    def test_peer_percentile_counts_only_profitable_peers(self):
        result = self.service.position("600519", as_of_date="2026-10-26")

        pe = result["peers"]["metrics"]["pe_ttm"]
        self.assertEqual(pe["peer_count"], 5)
        self.assertEqual(pe["loss_making_peers"], 1)
        self.assertEqual(pe["percentile"], 0.6)
        self.assertEqual(result["peers"]["industry"]["code"], "851251")

    def test_report_renders_both_percentiles(self):
        facts = _valuation_position_facts(
            self.service.position("600519", as_of_date="2026-10-26")
        )

        labels = [fact.label for fact in facts]
        self.assertIn("市盈率(TTM)自身历史分位", labels)
        self.assertIn("市盈率(TTM)同行分位", labels)
        self.assertTrue(any("白酒Ⅲ" in fact.value for fact in facts))


if __name__ == "__main__":
    unittest.main()
