import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.data_src.models import NewsItem
from src.event_intel import (
    EventIntelService,
    Observable,
    SQLiteEventIntelRepository,
    html_to_text,
    intel_item_id,
)


def _news(day, title, url):
    return NewsItem(source="google_news", kind="news", title=title, url=url, published_on=day, publisher="Reuters", language="en")


class EventIntelTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.calls = []
        self.fail = False
        self.service = EventIntelService(
            SQLiteEventIntelRepository(Path(self.temp_dir.name) / "db.sqlite3"),
            fetch=self._fetch,
            clock=lambda: datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _fetch(self, task, payload):
        self.calls.append((task, payload))
        if self.fail:
            raise RuntimeError("feed down")
        return [
            _news("2026-11-12", "Nvidia board accepts Huang resignation", "https://example.com/a"),
            _news("2026-11-20", "Published after the window", "https://example.com/b"),
            _news("2026-11-01", "Before the window", "https://example.com/c"),
        ]

    def test_search_keeps_items_inside_the_window_and_stores_them(self):
        result = self.service.search("news", query="Nvidia Huang", since="2026-11-10", until="2026-11-15")

        self.assertEqual([item["title"] for item in result["items"]], ["Nvidia board accepts Huang resignation"])
        self.assertEqual(self.calls[0][0], "search_news")
        stored = self.service.items([intel_item_id("https://example.com/a")])
        self.assertEqual(stored[0]["published_on"], "2026-11-12")
        self.assertIsNotNone(result["search_id"])

    def test_failed_search_falls_back_to_stored_items_and_says_so(self):
        self.service.search("news", query="Nvidia Huang", since="2026-11-10", until="2026-11-15")
        self.fail = True

        result = self.service.search("news", query="nvidia", since="2026-11-10", until="2026-11-15")

        self.assertIn("feed down", result["error"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(any("本地已存" in note for note in result["notes"]))

    def test_source_arguments_are_checked(self):
        for source, kwargs in (("news", {}), ("sec-filings", {"query": "x"}), ("federal-register", {}), ("rss", {"query": "x"})):
            with self.subTest(source):
                with self.assertRaises(ValueError):
                    self.service.search(source, **kwargs)


class ReadPagesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sent = []
        self.limited = True
        self.service = EventIntelService(
            SQLiteEventIntelRepository(Path(self.temp_dir.name) / "db.sqlite3"),
            fetch=self._fetch,
            clock=lambda: datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _fetch(self, task, payload):
        url = payload["url"]
        self.sent.append(url)
        if "news.google.com" in url and self.limited and url.endswith("/2"):
            raise RuntimeError("SourceCoolingDown: google_news 冷却中")
        if url.endswith("/403"):
            raise RuntimeError("DataSourceError: HTTP 403 for https://blocked.example/403")
        return {"html": f"<html><title>T {url[-1]}</title><body><p>body of {url}</p></body></html>",
                "content_type": "text/html", "final_url": url}

    def test_batch_stops_a_rate_limited_host_and_reruns_only_what_is_missing(self):
        urls = [f"https://news.google.com/articles/{n}" for n in (1, 2, 3)] + ["https://blocked.example/403", "https://other.example/ok"]

        first = self.service.read_pages(urls=urls)

        self.assertEqual([page["url"][-1] for page in first["pages"]], ["1", "k"])
        self.assertEqual([item["url"][-1] for item in first["deferred"]], ["2", "3"])
        self.assertNotIn("https://news.google.com/articles/3", self.sent)
        self.assertEqual(len(first["failed"]), 1)
        self.limited = False
        self.sent.clear()

        second = self.service.read_pages(urls=urls)

        self.assertEqual(len(second["pages"]), 4)
        self.assertTrue(all(page["cached"] for page in second["pages"] if page["url"].endswith(("/1", "/ok"))))
        self.assertNotIn("https://news.google.com/articles/1", self.sent)
        self.assertEqual(second["deferred"], [])


class ObservableTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = EventIntelService(
            SQLiteEventIntelRepository(Path(self.temp_dir.name) / "db.sqlite3"),
            fetch=lambda task, payload: [_news("2026-11-12", "BIS rule on Malaysia", "https://example.com/rule")],
            clock=lambda: datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc),
        )
        search = self.service.search("news", query="BIS rule", since="2026-11-01", until="2026-11-15")
        self.search_ref = f"search:{search['search_id']}"
        self.item_ref = intel_item_id("https://example.com/rule")
        self.observable = Observable(
            observable_id="obs:us-bis-se-asia-rule",
            title="BIS rule on SE Asia AI chips",
            kind="event",
            unit="0/1",
            criteria="A Federal Register BIS rule adding license requirements for Malaysia or Singapore",
            created_on="2026-11-15",
            watch_queries=("en:BIS rule Malaysia AI chips", "zh:东南亚 芯片 出口管制"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _baseline(self, **overrides):
        payload = {
            "period_end": "2026-11-10",
            "value": "0",
            "available_at": "2026-11-15",
            "evidence_refs": [self.search_ref],
            "note": "not yet as of the search",
        }
        payload.update(overrides)
        return payload

    def test_define_stores_definition_and_baseline_once(self):
        first = self.service.define_observable(self.observable, self._baseline())
        again = self.service.define_observable(self.observable, self._baseline())

        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertFalse(again["baseline"]["appended"])
        self.assertEqual(len(self.service.observations(self.observable.observable_id)), 1)

    def test_changed_definition_is_rejected(self):
        self.service.define_observable(self.observable, self._baseline())
        changed = Observable(**{**self.observable.to_dict(), "watch_queries": ()})

        with self.assertRaises(ValueError):
            self.service.define_observable(changed, self._baseline())

    def test_observation_rules(self):
        self.service.define_observable(self.observable, self._baseline())
        cases = {
            "event values are 0 or 1": {"value": "0.5"},
            "period after availability": {"period_end": "2026-11-14", "available_at": "2026-11-13"},
            "not yet public": {"period_end": "2026-11-16", "available_at": "2026-11-16"},
            "earlier than every evidence": {"evidence_refs": [self.item_ref], "period_end": "2026-11-11", "available_at": "2026-11-11"},
            "unknown evidence": {"evidence_refs": ["intel:0000000000000000"]},
            "no evidence": {"evidence_refs": []},
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.service.record_observation(
                        {"observable_id": self.observable.observable_id, **self._baseline(), **change}
                    )

    def test_corrections_supersede_by_period(self):
        self.service.define_observable(self.observable, self._baseline())
        self.service.record_observation(
            {
                "observable_id": self.observable.observable_id,
                "period_end": "2026-11-12",
                "value": "1",
                "available_at": "2026-11-12",
                "evidence_refs": [self.item_ref],
                "note": "rule published",
            }
        )

        history = self.service.observations(self.observable.observable_id)

        self.assertEqual([(item.period_end, item.value) for item in history], [("2026-11-10", Decimal(0)), ("2026-11-12", Decimal(1))])

    def test_agent_evidence_links_become_citable_items(self):
        item = self.service.add_evidence(url="https://www.bis.gov/x", title="BIS press", published_on="2026-11-13")

        self.assertEqual(item["source"], "agent_evidence")
        with self.assertRaises(ValueError):
            self.service.add_evidence(url="https://x", title="future", published_on="2026-12-01")


class HtmlTextTest(unittest.TestCase):
    def test_scripts_navigation_and_inline_xbrl_headers_are_dropped(self):
        title, text = html_to_text(
            "<html><head><title>8-K</title><style>p{}</style></head><body><nav>Menu</nav>"
            "<ix:header>0001045810false</ix:header><p>Item 5.02 Departure</p><script>x()</script>"
            "<p>Kress&nbsp;appointed</p></body></html>"
        )

        self.assertEqual(title, "8-K")
        self.assertEqual(text, "Item 5.02 Departure\n\nKress appointed")


if __name__ == "__main__":
    unittest.main()
