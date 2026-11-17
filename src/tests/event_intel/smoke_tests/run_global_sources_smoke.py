"""On-demand smoke: US prices and global intelligence sources against live endpoints.

Runs the public CLI on a scratch database; not part of the unit suite (needs the network).
A failure line says whether the remote was unavailable or answered with no data.

    python src/tests/event_intel/smoke_tests/run_global_sources_smoke.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = (
    ("prices NVDA/AMD/SMH", ("prices", "--symbols", "NVDA,AMD,SMH", "--market", "us", "--start-date", "2024-06-01")),
    ("news en", ("intel", "--source", "news", "--query", "Nvidia", "--limit", "3")),
    ("news zh", ("intel", "--source", "news", "--query", "英伟达", "--lang", "zh", "--limit", "3")),
    ("sec filings", ("intel", "--source", "sec-filings", "--ticker", "NVDA", "--since", "2026-01-01", "--limit", "3")),
    ("sec search", ("intel", "--source", "sec-search", "--query", '"export controls"', "--forms", "8-K", "--limit", "3")),
    ("federal register", ("intel", "--source", "federal-register", "--agency", "industry-and-security-bureau", "--limit", "3")),
)


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as folder:
        db = str(Path(folder) / "smoke.sqlite3")
        for name, args in CASES:
            proc = subprocess.run(
                [sys.executable, "-m", "src.research_orchestration.cli", *args, "--db-path", db],
                capture_output=True,
            )
            if proc.returncode:
                failures += 1
                print(f"FAIL {name}: remote unavailable or rejected: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
                continue
            payload = json.loads(proc.stdout.decode("utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("items", [])
            empty = not rows or (isinstance(payload, list) and any(not item.get("bars") for item in payload))
            error = None if isinstance(payload, list) else payload.get("error")
            if error or empty:
                failures += 1
                print(f"FAIL {name}: {'remote error ' + error if error else 'no data returned'}")
            else:
                print(f"OK   {name}: {len(rows)} rows")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
