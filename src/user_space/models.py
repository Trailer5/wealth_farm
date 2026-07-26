"""Minimal user identity and context models."""

from __future__ import annotations

from dataclasses import dataclass


USER_ACTIVE = "active"
USER_INACTIVE = "inactive"


@dataclass(frozen=True)
class User:
    """One lightweight user space."""

    user_id: str
    display_name: str
    status: str = USER_ACTIVE
    is_default: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class UserContext:
    """Current user passed through service boundaries."""

    user_id: str
