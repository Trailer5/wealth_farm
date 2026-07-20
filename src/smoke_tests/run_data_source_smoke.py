"""Manual smoke checks for external data providers.

This script is intentionally separate from unit tests. It touches public network
data sources and should not run in CI by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual smoke checks for data providers.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["510300", "159915", "160706"],
        help="Exchange fund sample symbols.",
    )
    parser.add_argument(
        "--include-akshare",
        action="store_true",
        help="Also smoke AkShare A-share and ETF APIs.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.data_src.eastmoney import EastmoneyExchangeFundDataSource
    from src.data_src.sina import SinaExchangeFundDataSource
    from src.data_src.tencent import TencentExchangeFundDataSource

    checks: list[tuple[str, Callable[[], Any]]] = [
        ("exchange_fund.eastmoney", lambda: EastmoneyExchangeFundDataSource().get_exchange_fund_spot()),
        ("exchange_fund.tencent", lambda: TencentExchangeFundDataSource().get_exchange_fund_spot(args.symbols)),
        ("exchange_fund.sina", lambda: SinaExchangeFundDataSource().get_exchange_fund_spot(args.symbols)),
    ]
    if args.include_akshare:
        checks.extend(
            [
                # Disabled original check:
                # ("akshare.a_share_spot", lambda: AkShareDataSource().get_a_share_spot()),
                ("akshare.etf_spot", lambda: AkShareDataSource().get_etf_spot()),
            ]
        )

    results = [run_check(name, func) for name, func in checks]
    print(json.dumps(results, ensure_ascii=True, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


def run_check(name: str, func: Callable[[], Any]) -> dict[str, Any]:
    try:
        rows = func()
        sample = rows[:3] if isinstance(rows, list) else rows
        return {
            "name": name,
            "ok": bool(rows),
            "count": len(rows) if isinstance(rows, list) else None,
            "sample": trim_sample(sample),
            "error": None,
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "count": 0,
            "sample": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def trim_sample(sample: Any) -> Any:
    if not isinstance(sample, list):
        return sample
    trimmed = []
    for row in sample:
        if not isinstance(row, dict):
            trimmed.append(row)
            continue
        trimmed.append(
            {
                key: row.get(key)
                for key in ("symbol", "name", "fund_type", "exchange", "latest", "source")
                if key in row
            }
        )
    return trimmed


if __name__ == "__main__":
    raise SystemExit(main())
