"""Machine-readable failures and structured input for public Agent workflows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def read_json_input(path: str) -> Any:
    """Read a UTF-8 JSON file, or stdin when the argument is '-'."""

    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig"))


def failure(exc: Exception, *, operation: str, retryable: bool = False) -> dict[str, Any]:
    """Stable categories without inferring investment meaning from error prose."""

    if isinstance(exc, (KeyError, FileNotFoundError)):
        code = "not_found"
    elif isinstance(exc, PermissionError):
        code = "not_authorized"
    elif isinstance(exc, (ValueError, TypeError)):
        code = "invalid_input_or_state"
    elif isinstance(exc, OSError):
        code = "io_error"
    else:
        code = "execution_failed"
    code = getattr(exc, "code", code)
    return {"status": "error", "operation": operation,
            "error": {"code": code, "type": type(exc).__name__, "message": str(exc),
                      "retryable": retryable or getattr(exc, "retryable", False)},
            "gaps": [str(exc)], "next_actions": []}