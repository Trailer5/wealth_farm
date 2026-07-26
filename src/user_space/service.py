"""Lightweight user creation and context selection."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .models import USER_ACTIVE, User, UserContext
from .sqlite_repository import SQLiteUserSpaceRepository


class UserSpaceService:
    """Provide a default user and simple user context lookup."""

    DEFAULT_USER_ID = "user:default"
    DEFAULT_DISPLAY_NAME = "Default"

    def __init__(self, repository: SQLiteUserSpaceRepository | None = None) -> None:
        self.repository = repository or SQLiteUserSpaceRepository()
        self.repository.initialize()
        self._ensure_default_user()

    def create_user(self, display_name: str) -> User:
        """Create one active non-default user."""

        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("display_name must not be empty")
        now = _now()
        user = User(
            user_id=f"user:{uuid4()}",
            display_name=normalized_name,
            status=USER_ACTIVE,
            is_default=False,
            created_at=now,
            updated_at=now,
        )
        self.repository.insert_user(user)
        return user

    def get_user(self, user_id: str) -> User | None:
        """Return one user."""

        return self.repository.get_user(user_id)

    def find_user(self, display_name: str) -> User | None:
        """Return one user by display name."""

        return self.repository.find_by_display_name(display_name.strip())

    def list_users(self) -> list[User]:
        """Return all user spaces."""

        return self.repository.list_users()

    def default_context(self) -> UserContext:
        """Return the default user context."""

        user = self.repository.get_default_user()
        if user is None:
            raise RuntimeError("Default user is missing")
        return UserContext(user_id=user.user_id)

    def context_for_user(self, user_id: str) -> UserContext:
        """Select one active user context."""

        user = self.repository.get_user(user_id)
        if user is None:
            raise KeyError(f"Unknown user_id: {user_id}")
        if user.status != USER_ACTIVE:
            raise ValueError(f"User is not active: {user_id}")
        return UserContext(user_id=user.user_id)

    def context_for_display_name(self, display_name: str) -> UserContext:
        """Select one user by display name."""

        user = self.find_user(display_name)
        if user is None:
            raise KeyError(f"Unknown user: {display_name}")
        return self.context_for_user(user.user_id)

    def _ensure_default_user(self) -> None:
        if self.repository.get_default_user() is not None:
            return
        now = _now()
        self.repository.insert_user(
            User(
                user_id=self.DEFAULT_USER_ID,
                display_name=self.DEFAULT_DISPLAY_NAME,
                status=USER_ACTIVE,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
