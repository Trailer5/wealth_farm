"""Unified unit test runner for local checks and CI."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


UNIT_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UNIT_TEST_DIR.parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all project unit tests.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Run tests with verbose output.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))
    suite = unittest.defaultTestLoader.discover(str(UNIT_TEST_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
