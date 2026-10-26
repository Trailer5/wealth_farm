"""Call several independent sources at once and keep every outcome."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar("T")


class SourceStillRunning(TimeoutError):
    """The source had not answered when the grace period after the first success ended."""


def fan_out(
    calls: Mapping[str, Callable[[], T]],
    *,
    accept: Callable[[T], bool] = lambda _: True,
    grace_seconds: float | None = None,
) -> dict[str, T | Exception]:
    """Run each call in its own thread; outcomes keep the input order.

    Each call must bound its own runtime (provider tasks run in terminable
    child processes with a deadline). With ``grace_seconds``, slower calls
    are reported as ``SourceStillRunning`` once that long has passed after
    the first accepted outcome; they finish in the background.
    """

    if not calls:
        return {}
    pool = ThreadPoolExecutor(max_workers=len(calls))
    futures = {name: pool.submit(call) for name, call in calls.items()}
    pending: set[Future] = set(futures.values())
    deadline: float | None = None
    try:
        while pending:
            timeout = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            if (
                deadline is None
                and grace_seconds is not None
                and any(_accepted(item, accept) for item in done)
            ):
                deadline = time.monotonic() + grace_seconds
    finally:
        pool.shutdown(wait=False)
    outcomes: dict[str, T | Exception] = {}
    for name, future in futures.items():
        if not future.done():
            outcomes[name] = SourceStillRunning(
                f"no answer {grace_seconds:g}s after the first successful source"
            )
            continue
        try:
            outcomes[name] = future.result()
        except Exception as exc:  # noqa: BLE001 - reported per source
            outcomes[name] = exc
    return outcomes


def _accepted(future: Future, accept: Callable[[T], bool]) -> bool:
    if future.exception() is not None:
        return False
    return bool(accept(future.result()))
