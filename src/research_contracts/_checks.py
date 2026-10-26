"""Field checks shared by the research request and artifact contracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def optional_text(value: object, name: str) -> None:
    if value is not None:
        require_text(value, name)


def require_texts(
    values: tuple[str, ...],
    name: str,
    *,
    nonempty: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if nonempty and not values:
        raise ValueError(f"{name} must not be empty")
    for value in values:
        require_text(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


def require_date(value: object, name: str) -> date:
    try:
        parsed = date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return parsed


def require_instance(value: object, expected: type, name: str) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{name} must be {expected.__name__}")


def require_items(
    values: tuple[object, ...],
    expected: type,
    name: str,
    *,
    nonempty: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if nonempty and not values:
        raise ValueError(f"{name} must not be empty")
    for value in values:
        require_instance(value, expected, name)


def require_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
