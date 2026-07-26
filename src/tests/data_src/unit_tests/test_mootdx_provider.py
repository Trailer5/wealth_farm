import unittest

from src.data_src.mootdx import MootdxDataSource


class FakeFrame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        if orient != "records":
            raise AssertionError(orient)
        return self.records


class FakeMootdxClient:
    def __init__(self):
        self.closed = False
        self.last_call = None

    def bars(self, symbol, frequency, start, offset):
        self.last_call = (symbol, frequency, start, offset)
        return FakeFrame(
            [
                {
                    "datetime": "2026-07-23 15:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                    "amount": 1050,
                },
                {
                    "datetime": "2026-07-24 15:00",
                    "open": 10.5,
                    "high": 12,
                    "low": 10,
                    "close": 11,
                    "vol": 120,
                    "amount": 1320,
                },
            ]
        )

    def close(self):
        self.closed = True


class MootdxDataSourceTest(unittest.TestCase):
    def test_daily_bars_are_normalized_and_filtered(self):
        client = FakeMootdxClient()
        source = MootdxDataSource(
            client_factory=lambda market, timeout: client,
            timeout=3,
        )

        bars = source.get_a_share_daily_bars(
            "sh.600519",
            "2026-07-24",
            "2026-07-24",
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "600519")
        self.assertEqual(bars[0].trade_date, "2026-07-24")
        self.assertEqual(bars[0].volume, 12000)
        self.assertEqual(bars[0].source, "mootdx:bars")
        self.assertEqual(client.last_call[0:3], ("600519", 9, 0))
        self.assertTrue(client.closed)

    def test_unsupported_long_range_is_rejected(self):
        source = MootdxDataSource(
            client_factory=lambda market, timeout: FakeMootdxClient()
        )

        with self.assertRaises(ValueError):
            source.get_a_share_daily_bars(
                "600519",
                "2020-01-01",
                "2026-07-24",
            )


if __name__ == "__main__":
    unittest.main()
