import unittest

from src.data_src.nbs import NBS_DATA_RELEASE_URL, NBSMacroDataSource


class FakeTextClient:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, params=None):
        self.urls.append(url)
        return self.text


class NBSMacroDataSourceTest(unittest.TestCase):
    def test_latest_official_cpi_preserves_release_availability(self):
        client = FakeTextClient(
            """
            <html><body>
              <a>2026年6月份居民消费价格同比上涨1.0%</a>
              <span>2026-07-09</span>
              <a>2026年7月份居民消费价格同比上涨0.5%</a>
              <span>2026-08-09</span>
            </body></html>
            """
        )

        result = NBSMacroDataSource(client).get_latest_cpi_yoy()

        self.assertEqual(client.urls, [NBS_DATA_RELEASE_URL])
        self.assertEqual(result["reference_period"], "2026-07")
        self.assertEqual(result["available_at"], "2026-08-09")
        self.assertEqual(result["cpi_yoy_percent"], 0.5)
        self.assertFalse(result["historical_backtest_eligible"])

    def test_decline_and_flat_are_signed(self):
        decline = NBSMacroDataSource(
            FakeTextClient(
                "2026年6月份居民消费价格同比下降0.3% 2026-07-09"
            )
        ).get_latest_cpi_yoy()
        flat = NBSMacroDataSource(
            FakeTextClient(
                "2026年7月份居民消费价格同比持平 2026-08-09"
            )
        ).get_latest_cpi_yoy()

        self.assertEqual(decline["cpi_yoy_percent"], -0.3)
        self.assertEqual(flat["cpi_yoy_percent"], 0.0)


if __name__ == "__main__":
    unittest.main()
