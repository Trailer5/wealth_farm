"""Run potentially blocking provider calls in terminable child processes."""

from __future__ import annotations

import importlib
import multiprocessing as mp
import queue
import traceback
from typing import Any

from .common import DataSourceError


class ProviderProcessTimeoutError(DataSourceError):
    """Raised when a provider child process exceeds its deadline."""


class ProviderProcessError(DataSourceError):
    """Raised when a provider child process fails."""

    def __init__(
        self,
        message: str,
        *,
        remote_traceback: str | None = None,
    ) -> None:
        super().__init__(message)
        self.remote_traceback = remote_traceback


def run_provider_task(
    module_name: str,
    function_name: str,
    kwargs: dict[str, Any],
    *,
    timeout_seconds: float,
) -> Any:
    """Run one importable function in a spawn process and return its value."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    context = mp.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_provider_worker,
        args=(
            module_name,
            function_name,
            kwargs,
            result_queue,
        ),
    )
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        result_queue.close()
        raise ProviderProcessTimeoutError(
            f"{module_name}.{function_name} exceeded {timeout_seconds:g} seconds"
        )
    try:
        payload = result_queue.get(timeout=1)
    except queue.Empty as exc:
        raise ProviderProcessError(
            f"{module_name}.{function_name} exited without a result; "
            f"exit_code={process.exitcode}"
        ) from exc
    finally:
        result_queue.close()
    if payload["ok"]:
        return payload["value"]
    raise ProviderProcessError(
        f"{payload['error_type']}: {payload['error']}",
        remote_traceback=payload["traceback"],
    )


def _provider_worker(
    module_name: str,
    function_name: str,
    kwargs: dict[str, Any],
    result_queue: Any,
) -> None:
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        value = function(**kwargs)
        result_queue.put({"ok": True, "value": value})
    except BaseException as exc:
        result_queue.put(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
