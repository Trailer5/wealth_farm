import unittest

from src.data_src.process_executor import (
    ProviderProcessError,
    ProviderProcessTimeoutError,
    run_provider_task,
)


FIXTURE_MODULE = "src.tests.data_src.unit_tests.process_fixtures"


class ProviderProcessExecutorTest(unittest.TestCase):
    def test_returns_picklable_value(self):
        result = run_provider_task(
            FIXTURE_MODULE,
            "echo",
            {"value": {"count": 3}},
            timeout_seconds=5,
        )

        self.assertEqual(result, {"count": 3})

    def test_large_result_does_not_deadlock_queue(self):
        result = run_provider_task(
            FIXTURE_MODULE,
            "large_payload",
            {"size": 5_000_000},
            timeout_seconds=5,
        )

        self.assertEqual(len(result["payload"]), 5_000_000)

    def test_timeout_terminates_child(self):
        with self.assertRaises(ProviderProcessTimeoutError):
            run_provider_task(
                FIXTURE_MODULE,
                "sleep_then_return",
                {"seconds": 5},
                timeout_seconds=0.2,
            )

    def test_child_error_is_propagated(self):
        with self.assertRaises(ProviderProcessError) as context:
            run_provider_task(
                FIXTURE_MODULE,
                "raise_error",
                {},
                timeout_seconds=5,
            )

        self.assertIn("fixture failure", str(context.exception))


if __name__ == "__main__":
    unittest.main()
