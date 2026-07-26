"""SQLite persistence for lightweight user spaces."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import User


class SQLiteUserSpaceRepository:
    """Persist users without authentication or permission machinery."""

    SCHEMA_VERSION = 7
    SCHEMA_DESCRIPTION = "lightweight users and default user space"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the user space schema."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_one_default
                ON users (is_default)
                WHERE is_default = 1;
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def insert_user(self, user: User) -> None:
        """Insert one user."""

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        display_name,
                        status,
                        is_default,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        user.user_id,
                        user.display_name,
                        user.status,
                        1 if user.is_default else 0,
                        user.created_at,
                        user.updated_at,
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"User already exists: {user.display_name}") from exc

    def get_user(self, user_id: str) -> User | None:
        """Return one user by id."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, display_name, status, is_default, created_at, updated_at
                FROM users
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def find_by_display_name(self, display_name: str) -> User | None:
        """Return one user by case-insensitive display name."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, display_name, status, is_default, created_at, updated_at
                FROM users
                WHERE display_name = ? COLLATE NOCASE
                """,
                [display_name],
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def get_default_user(self) -> User | None:
        """Return the configured default user."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, display_name, status, is_default, created_at, updated_at
                FROM users
                WHERE is_default = 1
                """
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def list_users(self) -> list[User]:
        """Return all users."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, display_name, status, is_default, created_at, updated_at
                FROM users
                ORDER BY is_default DESC, display_name
                """
            ).fetchall()
        return [_user_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute("PRAGMA foreign_keys = ON")
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        user_id=str(row["user_id"]),
        display_name=str(row["display_name"]),
        status=str(row["status"]),
        is_default=bool(row["is_default"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
