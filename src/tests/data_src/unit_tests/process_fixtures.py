"""Importable child-process fixtures for provider executor tests."""

from __future__ import annotations

import time


def echo(*, value):
    return value


def sleep_then_return(*, seconds):
    time.sleep(seconds)
    return "finished"


def raise_error():
    raise RuntimeError("fixture failure")
