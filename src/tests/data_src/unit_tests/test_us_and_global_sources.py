import unittest

from src.data_src.common import normalize_us_symbol
from src.data_src.federal_register.provider import parse_documents
from src.data_src.google_news.provider import article_id, parse_rss, resolve_article_url, search_query
from src.data_src.nasdaq.provider import parse_history, parse_listing
from src.data_src.sec_edgar.provider import parse_search, parse_submissions
from src.data_src.sina.us_stock import bars_with_qfq_preclose, parse_daily_k, parse_qfq_factors
from src.data_src.throttle import host_family

# Sina's NVDA table around the 2024 10-for-1 split (2024-06-10) and the $0.01 dividend (ex 2024-06-11).
_NVDA_QFQ = (
    'var NVDA_qfq= {"total":4,"data":[{"d":"2024-06-11","f":"1","c":"-0.57"},'
    '{"d":"2024-06-10","f":"1","c":"-0.58"},{"d":"2024-03-05","f":"0.1","c":"-0.58"},'
    '{"d":"2023-12-05","f":"0.1","c":"-0.584"}]}\n/* trailing */'
)
_NVDA_DAILY = (
    'var _x=([{"d":"2024-06-06","o":"1240","h":"1255","l":"1183","c":"1209.98","v":"1","a":"1"},'
    '{"d":"2024-06-07","o":"1197","h":"1216","l":"1180","c":"1208.88","v":"1","a":"1"},'
    '{"d":"2024-06-10","o":"120.37","h":"123.1","l":"117.01","c":"121.79","v":"1","a":"1"},'
    '{"d":"2024-06-11","o":"121.77","h":"122.87","l":"118.74","c":"120.91","v":"1","a":"1"}]);'
)


class SinaUsBarsTest(unittest.TestCase):
    def test_reference_close_handles_split_and_dividend_days(self):
        bars = bars_with_qfq_preclose(
            "NVDA",
            parse_daily_k(_NVDA_DAILY),
            parse_qfq_factors(_NVDA_QFQ),
            "2024-06-07",
            "2024-06-11",
        )

        by_day = {bar.trade_date: bar for bar in bars}
        self.assertEqual(sorted(by_day), ["2024-06-07", "2024-06-10", "2024-06-11"])
        # Ordinary day: yesterday's close.
        self.assertAlmostEqual(by_day["2024-06-07"].preclose, 1209.98)
        # Split day: yesterday's close in post-split shares.
        self.assertAlmostEqual(by_day["2024-06-10"].preclose, 120.888)
        # Ex-dividend day: yesterday's close less the dividend.
        self.assertAlmostEqual(by_day["2024-06-11"].preclose, 121.78)
        self.assertEqual(by_day["2024-06-10"].source, "sina_us:qfq_preclose")

    def test_no_factor_table_means_no_corporate_actions(self):
        bars = bars_with_qfq_preclose("X", parse_daily_k(_NVDA_DAILY), [], "2024-06-07", "2024-06-07")

        self.assertAlmostEqual(bars[0].preclose, 1209.98)


class NasdaqTest(unittest.TestCase):
    def test_history_is_sorted_with_previous_close_as_reference(self):
        payload = {
            "data": {
                "tradesTable": {
                    "rows": [
                        {"date": "06/11/2024", "close": "$120.91", "open": "$121.77", "high": "$122.87", "low": "$118.74", "volume": "1,000"},
                        {"date": "06/10/2024", "close": "$121.79", "open": "$120.37", "high": "$123.10", "low": "$117.01", "volume": "2,000"},
                        {"date": "06/07/2024", "close": "$120.888", "open": "N/A", "high": "", "low": "", "volume": ""},
                    ]
                }
            }
        }

        bars = parse_history(payload, "NVDA", "2024-06-10", "2024-06-11")

        self.assertEqual([bar.trade_date for bar in bars], ["2024-06-10", "2024-06-11"])
        self.assertAlmostEqual(bars[0].preclose, 120.888)
        self.assertAlmostEqual(bars[1].preclose, 121.79)
        self.assertEqual(bars[0].volume, 2000.0)

    def test_listing_maps_exchange_and_requires_a_name(self):
        info = parse_listing(
            {"data": {"symbol": "SMH", "companyName": "VanEck Semiconductor ETF", "exchange": "NASDAQ-GM"}},
            "SMH",
            "exchange_fund",
        )

        self.assertEqual((info.exchange, info.security_type), ("nasdaq", "exchange_fund"))
        self.assertIsNone(parse_listing({"data": None}, "NVDA", "stock"))


class GoogleNewsTest(unittest.TestCase):
    _RSS = """<?xml version="1.0"?><rss><channel>
      <item><title>Jensen Huang resigns - Reuters</title><link>https://news.google.com/a</link>
        <pubDate>Thu, 12 Nov 2026 22:10:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
      <item><title>Old story - WSJ</title><link>https://news.google.com/b</link>
        <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate><source url="https://www.wsj.com">WSJ</source></item>
      <item><title>No date</title><link>https://news.google.com/c</link></item>
    </channel></rss>"""

    def test_items_keep_publisher_and_respect_the_date_window(self):
        items = parse_rss(self._RSS, language="en", since="2026-11-10", until="2026-11-15")

        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].title, items[0].publisher, items[0].published_on), ("Jensen Huang resigns", "Reuters", "2026-11-12"))

    def test_before_operator_is_exclusive_so_it_uses_the_next_day(self):
        self.assertEqual(
            search_query("Nvidia", "2026-11-01", "2026-11-15"),
            "Nvidia after:2026-11-01 before:2026-11-16",
        )

    def test_article_link_resolves_to_the_publisher_url(self):
        class _Session:
            def __init__(self):
                self.posted = None

            def get(self, url, **_):
                return type("Page", (), {"text": '<c-wiz data-n-a-sg="SIG" data-n-a-ts="1763251200"></c-wiz>'})()

            def post(self, url, data, **_):
                self.posted = data["f.req"]
                body = '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://finance.yahoo.com/x.html\\",1]",null]]'
                return type("Reply", (), {"text": ")]}'\n\n" + body})()

        session = _Session()
        link = "https://news.google.com/rss/articles/CBMiABC?oc=5"

        self.assertEqual(article_id(link), "CBMiABC")
        self.assertIsNone(article_id("https://www.reuters.com/articles/x"))
        self.assertEqual(resolve_article_url(link, session=session), "https://finance.yahoo.com/x.html")
        self.assertIn("CBMiABC", session.posted)
        self.assertIn("SIG", session.posted)


class SecEdgarTest(unittest.TestCase):
    def test_submissions_filter_forms_and_dates_and_build_archive_links(self):
        payload = {
            "filings": {
                "recent": {
                    "form": ["8-K", "4", "10-Q"],
                    "filingDate": ["2026-11-12", "2026-11-06", "2026-08-26"],
                    "accessionNumber": ["0001045810-26-000082", "0001-26-1", "0001045810-26-000075"],
                    "primaryDocument": ["nvda-20261111.htm", "x.xml", "nvda-20260726.htm"],
                    "primaryDocDescription": ["8-K", "4", "10-Q"],
                    "items": ["5.02,9.01", "", ""],
                    "reportDate": ["2026-11-11", "", "2026-07-26"],
                }
            }
        }

        items = parse_submissions(payload, cik=1045810, company="NVIDIA CORP (NVDA)", forms=("8-K", "10-Q"), since="2026-09-01")

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].url,
            "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000082/nvda-20261111.htm",
        )
        self.assertIn("5.02", items[0].title)

    def test_full_text_hits_link_to_the_matching_document(self):
        payload = {
            "hits": {
                "hits": [
                    {
                        "_id": "0001829126-26-011139:ex10-1.htm",
                        "_source": {"ciks": ["0002109869"], "file_date": "2026-10-15", "form": "8-K",
                                    "display_names": ["Exascale Labs (XLAB)"], "file_description": "EXHIBIT 10.1"},
                    }
                ]
            }
        }

        (item,) = parse_search(payload)

        self.assertEqual(item.url, "https://www.sec.gov/Archives/edgar/data/2109869/000182912626011139/ex10-1.htm")
        self.assertEqual(item.published_on, "2026-10-15")


class FederalRegisterTest(unittest.TestCase):
    def test_documents_carry_type_agency_and_abstract(self):
        (item,) = parse_documents(
            {
                "results": [
                    {
                        "title": "Revision to License Review Policy",
                        "type": "Rule",
                        "abstract": "BIS revises ...",
                        "publication_date": "2026-01-15",
                        "html_url": "https://www.federalregister.gov/d/2026-00001",
                        "agencies": [{"name": "Industry and Security Bureau"}],
                    }
                ]
            }
        )

        self.assertEqual(item.title, "[Rule] Revision to License Review Policy")
        self.assertEqual((item.kind, item.publisher), ("rule", "Industry and Security Bureau"))


class CommonTest(unittest.TestCase):
    def test_us_tickers(self):
        self.assertEqual(normalize_us_symbol(" brk.b "), "BRK.B")
        self.assertEqual(normalize_us_symbol("gb_nvda"), "NVDA")
        for bad in ("600519", "", "TOO-LONG-TICKER"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    normalize_us_symbol(bad)

    def test_sec_hosts_throttle_separately(self):
        self.assertEqual(host_family("https://data.sec.gov/submissions/x.json"), "sec_data")
        self.assertEqual(host_family("https://www.sec.gov/Archives/x"), "sec_www")
        self.assertEqual(host_family("https://api.nasdaq.com/api/quote"), "nasdaq")


if __name__ == "__main__":
    unittest.main()
