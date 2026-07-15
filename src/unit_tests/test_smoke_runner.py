import unittest

from src.smoke_tests.run_data_source_smoke import run_check, trim_sample


class SmokeRunnerTest(unittest.TestCase):
    def test_run_check_success(self):
        result = run_check("ok", lambda: [{"symbol": "510300", "name": "沪深300ETF", "extra": "ignored"}])

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["sample"][0]["symbol"], "510300")
        self.assertNotIn("extra", result["sample"][0])

    def test_run_check_failure(self):
        result = run_check("bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        self.assertFalse(result["ok"])
        self.assertIn("RuntimeError", result["error"])

    def test_trim_sample_non_list(self):
        self.assertEqual(trim_sample({"x": 1}), {"x": 1})


if __name__ == "__main__":
    unittest.main()
