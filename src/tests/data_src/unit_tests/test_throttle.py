import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.data_src import BaoStockDataSource
from src.data_src.common import DataSourceError
from src.data_src.throttle import SourceCoolingDown, SourceThrottle, host_family


class _Clock:
    def __init__(self):
        self.now = 1_000.0
        self.waits = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)


class SourceThrottleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "throttle.sqlite3"
        self.clock = _Clock()

    def tearDown(self):
        self.temp.cleanup()

    def _throttle(self):
        return SourceThrottle(self.path, clock=self.clock.time, sleep=self.clock.sleep)

    def test_slots_are_shared_between_throttles_on_one_file(self):
        first, second = self._throttle(), self._throttle()
        first.acquire("eastmoney")
        second.acquire("eastmoney")
        second.acquire("sina")
        self.assertEqual(len(self.clock.waits), 1)
        self.assertGreaterEqual(self.clock.waits[0], 0.5)
        self.assertLessEqual(self.clock.waits[0], 0.5 * 1.3)

    def test_cooldown_blocks_until_it_ends(self):
        throttle = self._throttle()
        throttle.trip("ths", 60, "HTTP 429")
        with self.assertRaisesRegex(SourceCoolingDown, "HTTP 429"):
            self._throttle().acquire("ths")
        self.assertIn("ths", throttle.cooldowns())
        self.clock.now += 61
        throttle.acquire("ths")
        self.assertEqual(throttle.cooldowns(), {})

    def test_host_families(self):
        self.assertEqual(host_family("https://push2his.eastmoney.com/api/qt"), "eastmoney")
        self.assertEqual(host_family("http://hq.sinajs.cn/list=sh510300"), "sina")
        self.assertEqual(host_family("https://d.10jqka.com.cn/v6/line"), "ths")
        self.assertEqual(host_family("https://example.org/x"), "example.org")


class BaoStockCooldownTest(unittest.TestCase):
    def test_blacklisted_login_trips_a_long_cooldown(self):
        trips = []

        class _Throttle:
            def acquire(self, key):
                return None

            def trip(self, key, seconds, reason):
                trips.append((key, seconds, reason))

        module = SimpleNamespace(
            login=lambda: SimpleNamespace(error_code="10001011", error_msg="黑名单用户"),
            logout=lambda: None,
        )
        provider = BaoStockDataSource(module, throttle=_Throttle())
        with self.assertRaises(DataSourceError):
            provider.login()
        self.assertEqual(trips[0][0], "baostock")
        self.assertGreaterEqual(trips[0][1], 3600)


if __name__ == "__main__":
    unittest.main()
