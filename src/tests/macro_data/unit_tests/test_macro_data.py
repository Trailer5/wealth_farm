import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.data_src.macro_sources import _period, _period_end
from src.data_src.models import MacroObservation
from src.macro_data import (
    AVAILABLE_INFERRED,
    AVAILABLE_PUBLISHED,
    MacroDataService,
    SQLiteMacroDataRepository,
    macro_series,
)


def _cpi(period, value, source, published_at=None):
    return MacroObservation(
        series_id="cpi_yoy",
        period=period,
        period_end=_period_end(period),
        value=value,
        unit="%",
        source=source,
        published_at=published_at,
    )


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, source, series_id, start_date, end_date, timeout):
        self.calls.append(source)
        response = self.responses[source]
        if isinstance(response, Exception):
            raise response
        return response


class MacroDataServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SQLiteMacroDataRepository(
            Path(self.temp_dir.name) / "macro.sqlite3"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _service(self, fetcher):
        return MacroDataService(
            self.repository,
            fetcher=fetcher,
            clock=lambda: datetime.now(timezone.utc),
        )

    def test_merge_prefers_priority_source_and_published_date(self):
        fetcher = FakeFetcher(
            {
                "akshare": [
                    _cpi("2026-08", 0.8, "akshare:macro_china_cpi"),
                    _cpi("2026-09", 0.9, "akshare:macro_china_cpi"),
                ],
                "nbs": [_cpi("2026-09", 0.9, "nbs:data_release_page", "2026-10-15")],
            }
        )

        result = self._service(fetcher).get(
            "cpi_yoy",
            start_date="2026-01-01",
            end_date="2026-10-26",
        )

        august, september = result.points
        self.assertEqual(september.source, "akshare:macro_china_cpi")
        self.assertEqual(september.available_at, "2026-10-15")
        self.assertEqual(september.available_at_basis, AVAILABLE_PUBLISHED)
        self.assertEqual(september.confirmed_by, ("nbs:data_release_page",))
        self.assertEqual(august.available_at, "2026-09-15")
        self.assertEqual(august.available_at_basis, AVAILABLE_INFERRED)
        self.assertEqual(result.successful_sources, ("akshare", "nbs"))
        self.assertEqual(result.conflicts, ())

    def test_as_of_hides_values_not_yet_published(self):
        fetcher = FakeFetcher(
            {
                "akshare": [
                    _cpi("2026-08", 0.8, "akshare:macro_china_cpi"),
                    _cpi("2026-09", 0.9, "akshare:macro_china_cpi"),
                ],
                "nbs": RuntimeError("release page down"),
            }
        )

        result = self._service(fetcher).get(
            "cpi_yoy",
            start_date="2026-01-01",
            end_date="2026-10-26",
            as_of_date="2026-10-10",
        )

        self.assertEqual([item.period for item in result.points], ["2026-08"])
        self.assertIn("release page down", result.provider_errors["nbs"])

    def test_conflicting_sources_are_reported_and_fresh_cache_is_reused(self):
        fetcher = FakeFetcher(
            {
                "akshare": [_cpi("2026-09", 0.9, "akshare:macro_china_cpi")],
                "nbs": [_cpi("2026-09", 1.2, "nbs:data_release_page", "2026-10-15")],
            }
        )
        service = self._service(fetcher)

        first = service.get("cpi_yoy", start_date="2026-01-01", end_date="2026-10-26")
        second = service.get("cpi_yoy", start_date="2026-02-01", end_date="2026-10-01")

        self.assertEqual(first.conflicts[0]["period"], "2026-09")
        self.assertEqual(first.points[0].value, 0.9)
        self.assertEqual(first.points[0].available_at_basis, AVAILABLE_INFERRED)
        self.assertFalse(second.fetched_remote)
        self.assertEqual(len(fetcher.calls), 2)

    def test_all_sources_failing_is_stale(self):
        fetcher = FakeFetcher({"akshare": RuntimeError("down"), "nbs": RuntimeError("down")})

        result = self._service(fetcher).get(
            "cpi_yoy",
            start_date="2026-01-01",
            end_date="2026-10-26",
        )

        self.assertTrue(result.stale)
        self.assertEqual(result.points, ())


class MacroCatalogTest(unittest.TestCase):
    def test_period_parsing_and_release_rules(self):
        self.assertEqual(_period("2026年09月份"), "2026-09")
        self.assertEqual(_period("202604"), "2026-04")
        self.assertEqual(_period("2026年第1-3季度"), "2026-Q3")
        self.assertEqual(_period("2026年第1季度"), "2026-Q1")
        self.assertEqual(_period_end("2026-Q4"), "2026-12-31")
        self.assertEqual(
            macro_series("gdp_yoy_ytd").release_rule.available_at("2026-12-31"),
            "2027-01-25",
        )
        self.assertEqual(
            macro_series("lpr_1y").release_rule.available_at("2026-10-20"),
            "2026-10-20",
        )
        with self.assertRaises(ValueError):
            macro_series("unknown")


if __name__ == "__main__":
    unittest.main()
