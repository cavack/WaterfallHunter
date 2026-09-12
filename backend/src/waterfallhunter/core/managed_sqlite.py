from __future__ import annotations

import sqlite3
from pathlib import Path


class ManagedSQLiteError(RuntimeError):
    """Raised before mutation when managed SQLite invariants are unavailable."""


class ManagedSQLiteConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the connection."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect_managed_sqlite(
    database: str | Path,
    *,
    timeout: float = 5.0,
    isolation_level: str | None = "DEFERRED",
    uri: bool = False,
) -> sqlite3.Connection:
    """Open a managed connection with verified foreign-key enforcement."""

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            str(database),
            timeout=timeout,
            isolation_level=isolation_level,
            uri=uri,
            factory=ManagedSQLiteConnection,
        )
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        if row != (1,):
            raise ManagedSQLiteError("MANAGED_SQLITE_FOREIGN_KEYS_UNAVAILABLE")
        return conn
    except Exception as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, ManagedSQLiteError):
            raise
        raise ManagedSQLiteError(
            "MANAGED_SQLITE_FOREIGN_KEYS_UNAVAILABLE"
        ) from exc


class _ManagedConnection:
    """Connection wrapper whose context manager commits, rolls back, and closes."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def managed_connection(database: str | Path, *, timeout: float = 5.0) -> _ManagedConnection:
    """Open, verify, and hand out a self-closing managed SQLite connection."""
    return _ManagedConnection(connect_managed_sqlite(database, timeout=timeout))
