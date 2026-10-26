import unittest
from datetime import date, timedelta

from src.macro_data import MacroPoint, MacroSeriesResult, macro_series
from src.macro_state import MacroArchiveService
from src.research_contracts import Confidence, SubjectType

AS_OF = "2026-10-26"


def _months(last: str, count: int) -> list[tuple[str, str]]:
    year, month = int(last[:4]), int(last[5:7])
    output = []
    for _ in range(count):
        next_first = date(year + (month == 12), month % 12 + 1, 1)
        output.append((f"{year:04d}-{month:02d}", (next_first - timedelta(days=1)).isoformat()))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(output, key=lambda item: item[1])


def _monthly(last: str, values):
    months = _months(last, 140)
    return [(period, end, values(index)) for index, (period, end) in enumerate(months)]


def _series(social_last="2026-04"):
    return {
        "pmi_manufacturing": _monthly("2026-09", lambda i: 49 + (i % 5) * 0.5),
        "pmi_non_manufacturing": _monthly("2026-09", lambda i: 50.2),
        "gdp_yoy_ytd": [("2026-Q3", "2026-09-30", 4.6)],
        "cpi_yoy": _monthly("2026-09", lambda i: 2 - i / 100),
        "ppi_yoy": _monthly("2026-09", lambda i: -1.0 if i < 137 else 2.0),
        "m2_yoy": _monthly("2026-09", lambda i: 8.0),
        "m1_yoy": _monthly("2026-09", lambda i: 3 + (i % 7)),
        "new_rmb_loans": _monthly("2026-09", lambda i: 10000 + 10 * i),
        "social_financing_flow": _monthly(social_last, lambda i: 20000),
        "shibor_3m": [("2026-10-24", "2026-10-24", 1.41)],
        "cgb_10y": [("2026-07-20", "2026-07-20", 1.73), ("2026-10-23", "2026-10-23", 1.64)],
        "lpr_1y": [("2025-10-20", "2025-10-20", 3.1), ("2026-10-20", "2026-10-20", 3.0)],
        "lpr_5y": [("2026-10-20", "2026-10-20", 3.5)],
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
            MacroPoint(period, end, value, definition.unit, end, "published", "test")
            for period, end, value in self.series[series_id]
            if start_date <= end <= as_of_date
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


class MacroArchiveTest(unittest.TestCase):
    def test_archive_builds_five_dimensions(self):
        archive = MacroArchiveService(macro_data=_Macro(_series())).build(AS_OF)

        assessment = archive.assessment
        self.assertEqual(assessment.subject_type, SubjectType.MACRO)
        self.assertEqual(
            [item.name for item in assessment.dimensions],
            ["增长", "通胀", "货币与流动性", "信用", "政策利率"],
        )
        self.assertEqual(assessment.confidence, Confidence.HIGH)
        self.assertIn("制造业 PMI", assessment.dimensions[0].value)
        self.assertIn("2026-09", assessment.dimensions[0].value)
        self.assertTrue(any("PPI 同比由" in item for item in assessment.change_signals))
        self.assertIn("下调", assessment.label)
        self.assertTrue(any(item.startswith("social_financing_flow") for item in archive.gaps))
        credit = archive.metrics["信用"]["new_rmb_loans_ttm_growth"]
        self.assertGreater(credit["value"], 0)

    def test_stale_core_series_and_missing_dimension_lower_confidence(self):
        series = _series()
        series["cpi_yoy"] = _monthly("2026-05", lambda i: 1.0)
        archive = MacroArchiveService(
            macro_data=_Macro(series, failing={"new_rmb_loans"})
        ).build(AS_OF)

        names = [item.name for item in archive.assessment.dimensions]
        self.assertNotIn("信用", names)
        self.assertEqual(archive.assessment.confidence, Confidence.MEDIUM)
        self.assertIn("new_rmb_loans", archive.source_errors)
        self.assertTrue(any(item.startswith("cpi_yoy") for item in archive.gaps))


if __name__ == "__main__":
    unittest.main()
