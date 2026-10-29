"""Strict dict codec for frozen contract dataclasses."""

from __future__ import annotations

import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


def to_payload(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_payload(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [to_payload(item) for item in value]
    return value


def from_payload(cls: type[T], payload: Any) -> T:
    if not isinstance(payload, dict):
        raise TypeError(f"{cls.__name__} payload must be an object")
    hints = typing.get_type_hints(cls)
    known = {item.name: item for item in fields(cls)}
    unknown = set(payload) - set(known)
    if unknown:
        raise ValueError(
            f"Unknown {cls.__name__} fields: {sorted(unknown)}"
        )
    kwargs = {}
    for name, item in known.items():
        if name in payload:
            kwargs[name] = _decode(hints[name], payload[name], name)
        elif item.default is MISSING and item.default_factory is MISSING:
            raise ValueError(f"Missing {cls.__name__} field: {name}")
    return cls(**kwargs)


def _decode(annotation: Any, value: Any, name: str) -> Any:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        (inner,) = [item for item in args if item is not type(None)]
        return _decode(inner, value, name)
    if origin is tuple:
        if not isinstance(value, list):
            raise TypeError(f"{name} must be a list")
        return tuple(_decode(args[0], item, name) for item in value)
    if is_dataclass(annotation):
        return from_payload(annotation, value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation is Decimal:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a decimal string")
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a decimal string") from exc
    if annotation is bool:
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
        return value
    if annotation is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be an integer")
        return value
    if annotation is str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        return value
    raise TypeError(f"Unsupported contract field type for {name}")
