import math
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from src.macro_data import MacroPoint, MacroSeriesResult, macro_series
from src.market_analysis import MarketArchiveService, historical_percentile
from src.research_contracts import Confidence

AS_OF = "2026-10-23"


def _business_days(end: str, count: int) -> list[str]:
    days = []
    current = date.fromisoformat(end)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return sorted(days)


def _month_ends(end: str, count: int) -> list[str]:
    year, month = int(end[:4]), int(end[5:7])
    days = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        next_first = date(year + (month == 12), month % 12 + 1, 1)
        days.append((next_first - timedelta(days=1)).isoformat())
    return sorted(days) + ["2026-10-22"]


def _series():
    months = _month_ends(AS_OF, 120)
    daily = _business_days("2026-10-22", 400)
    return {
        "csi300_pe_ttm": [(day, 10 + 4 * (index % 12) / 11) for index, day in enumerate(months)],
        "csi300_pb": [(day, 1.2 + 0.01 * (index % 30)) for index, day in enumerate(months)],
        "csi500_pe_ttm": [(day, 20 + (index % 10)) for index, day in enumerate(months)],
        "cgb_10y": [(day, 3.2 - 1.5 * index / len(months)) for index, day in enumerate(months)],
        "shibor_3m": [(day, 1.5) for day in daily[-120:]],
        "lpr_1y": [("2026-09-22", 3.0), ("2026-10-20", 3.0)],
        "new_high_60": [(day, 100 + index % 50) for index, day in enumerate(daily)],
        "new_low_60": [(day, 120 + index % 70) for index, day in enumerate(daily)],
        "margin_balance_total": [(day, 20000 + 5 * index) for index, day in enumerate(daily)],
    }


class _Macro:
    def __init__(self, series, failing=()):
        self.series = series
        self.failing = set(failing)

    def get(self, series_id, *, start_date, end_date, as_of_date, allow_remote):
        if series_id in self.failing:
            raise RuntimeError(f"{series_id} down")
        definition = macro_series(series_id)
        points = tuple(
            MacroPoint(day, day, value, definition.unit, day, "published", "test")
            for day, value in self.series[series_id]
            if start_date <= day <= as_of_date
        )
        return MacroSeriesResult(
            definition=definition,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            points=points,
            fetched_remote=False,
            stale=False,
        )


class _DataAccess:
    def get(self, request):
        days = _business_days("2026-10-23", 700)
        records = tuple(
            {
                "trade_date": day,
                "close": 4000 * (1 + 0.0002 * index + 0.01 * math.sin(index / 7)),
            }
            for index, day in enumerate(days)
        )
        return SimpleNamespace(records=records)


class MarketArchiveTest(unittest.TestCase):
    def test_archive_builds_all_dimensions_deterministically(self):
        service = MarketArchiveService(macro_data=_Macro(_series()), data_access=_DataAccess())

        first = service.build(AS_OF, allow_remote=False)
        second = service.build(AS_OF, allow_remote=False)

        assessment = first.assessment
        self.assertEqual(
            [item.name for item in assessment.dimensions],
            ["估值", "股债性价比", "利率与流动性", "趋势", "波动", "宽度", "两融"],
        )
        self.assertEqual(assessment.assessment_id, second.assessment.assessment_id)
        self.assertEqual(assessment.confidence, Confidence.HIGH)
        self.assertEqual(assessment.subject_id, "market:cn_a")
        self.assertTrue(all(item.historical_percentile is not None for item in assessment.dimensions))
        self.assertIn("估值", assessment.label)
        self.assertGreaterEqual(len(assessment.change_signals), 5)
        self.assertEqual(first.metrics["估值"]["csi300_pe_ttm"]["sample_count"], 121)
        self.assertTrue(any("资金流" in item for item in first.gaps))

    def test_missing_series_becomes_a_gap_and_lowers_confidence(self):
        service = MarketArchiveService(
            macro_data=_Macro(_series(), failing={"margin_balance_total", "new_high_60"}),
            data_access=_DataAccess(),
        )

        archive = service.build(AS_OF, allow_remote=False)

        names = [item.name for item in archive.assessment.dimensions]
        self.assertNotIn("两融", names)
        self.assertNotIn("宽度", names)
        self.assertEqual(archive.assessment.confidence, Confidence.MEDIUM)
        self.assertIn("margin_balance_total", archive.source_errors)
        self.assertTrue(any(item.startswith("两融") for item in archive.gaps))

    def test_percentile_uses_only_the_trailing_window(self):
        points = [("2010-01-01", 100.0), ("2020-01-01", 1.0), ("2025-01-01", 2.0), ("2026-01-01", 3.0)]

        stat = historical_percentile(points, end_date="2026-06-30", years=10)

        self.assertEqual(stat.sample_count, 3)
        self.assertEqual(stat.percentile, 1.0)
        self.assertEqual(stat.window_start, "2020-01-01")


if __name__ == "__main__":
    unittest.main()
