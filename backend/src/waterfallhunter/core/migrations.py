from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable


_MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
_SQL_TRIVIA = r"(?:\s|--[^\r\n]*(?:\r\n|\r|\n|\Z)|/\*.*?\*/)"
_COMMENT_ONLY_SQL = re.compile(
    rf"\A(?:{_SQL_TRIVIA})*\Z",
    flags=re.DOTALL,
)
_LEADING_SQL_TRIVIA = re.compile(rf"\A(?:{_SQL_TRIVIA})*", flags=re.DOTALL)
_TRANSACTION_CONTROL = re.compile(
    r"(?:BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE)\b", flags=re.IGNORECASE
)
_SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
_REQUIRED_HISTORY_COLUMNS = {
    "version": ("INTEGER", None, 1),
    "name": ("TEXT", 1, 0),
    "checksum_sha256": ("TEXT", 1, 0),
    "applied_at": ("INTEGER", 1, 0),
    "source_revision": ("TEXT", 0, 0),
}
_EXPECTED_HISTORY_TRIGGERS = {
    "schema_migrations_no_update": "UPDATE",
    "schema_migrations_no_delete": "DELETE",
}
_MIGRATION_INFRASTRUCTURE_OBJECT_NAMES = frozenset(
    {
        _SCHEMA_MIGRATIONS_TABLE,
        "db_readiness_probe",
        *_EXPECTED_HISTORY_TRIGGERS,
    }
)


class MigrationError(RuntimeError):
    """Base class for migration-system failures."""


class MigrationDiscoveryError(MigrationError):
    """Raised when packaged migration definitions are ambiguous or invalid."""


class MigrationChecksumMismatch(MigrationError):
    """Raised when applied migration bytes no longer match immutable history."""


class MigrationStateError(MigrationError):
    """Raised when SQLite schema-version state is internally inconsistent."""


def migration_infrastructure_object_names() -> frozenset[str]:
    """Return globally reserved SQLite names owned by the migration system."""
    return _MIGRATION_INFRASTRUCTURE_OBJECT_NAMES


@dataclass(frozen=True, slots=True)
class Migration:
    """Immutable migration identity, exact SQL bytes, and content checksum."""

    version: int
    name: str
    filename: str
    sql_bytes: bytes
    checksum_sha256: str

    @classmethod
    def from_bytes(
        cls,
        *,
        version: int,
        name: str,
        filename: str,
        sql_bytes: bytes,
    ) -> "Migration":
        """Build a migration after enforcing canonical filename identity."""
        if version < 1:
            raise MigrationDiscoveryError("migration versions must be positive")
        if not name:
            raise MigrationDiscoveryError("migration name must not be empty")
        if not filename:
            raise MigrationDiscoveryError("migration filename must not be empty")
        if not isinstance(sql_bytes, bytes):
            raise MigrationDiscoveryError("migration SQL must be raw bytes")

        match = _MIGRATION_FILENAME.fullmatch(filename)
        if match is None:
            raise MigrationDiscoveryError("migration filename is not canonical")
        if int(match.group("version")) != version or match.group("name") != str(name):
            raise MigrationDiscoveryError(
                "migration filename does not match declared version/name"
            )

        return cls(
            version=int(version),
            name=str(name),
            filename=str(filename),
            sql_bytes=sql_bytes,
            checksum_sha256=hashlib.sha256(sql_bytes).hexdigest(),
        )


def validate_migrations(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
    """Return canonical exact-byte migrations in one contiguous sequence."""
    canonical: list[Migration] = []
    for migration in migrations:
        if not isinstance(migration, Migration):
            raise MigrationDiscoveryError("migration set contains an invalid object")
        rebuilt = Migration.from_bytes(
            version=migration.version,
            name=migration.name,
            filename=migration.filename,
            sql_bytes=migration.sql_bytes,
        )
        if migration.checksum_sha256 != rebuilt.checksum_sha256:
            raise MigrationDiscoveryError(
                "migration checksum does not match exact SQL bytes"
            )
        canonical.append(rebuilt)

    ordered = tuple(sorted(canonical, key=lambda item: item.version))
    if not ordered:
        return ()

    versions = [item.version for item in ordered]
    if len(set(versions)) != len(versions):
        raise MigrationDiscoveryError("duplicate migration version")

    expected = list(range(1, len(ordered) + 1))
    if versions != expected:
        raise MigrationDiscoveryError(
            "migration versions must be contiguous and start at version 1"
        )

    filenames = [item.filename for item in ordered]
    if len(set(filenames)) != len(filenames):
        raise MigrationDiscoveryError("duplicate migration filename")

    return ordered


def discover_migrations(
    package: str = "waterfallhunter.migrations",
) -> tuple[Migration, ...]:
    """Discover and validate packaged SQL migrations using exact file bytes."""
    discovered: list[Migration] = []

    try:
        root = resources.files(package)
    except (ModuleNotFoundError, TypeError) as exc:
        raise MigrationDiscoveryError(
            f"migration package is unavailable: {package}"
        ) from exc

    for entry in root.iterdir():
        if not entry.name.endswith(".sql"):
            continue

        match = _MIGRATION_FILENAME.fullmatch(entry.name)
        if match is None:
            raise MigrationDiscoveryError(
                f"invalid migration filename: {entry.name}"
            )

        sql_bytes = entry.read_bytes()
        discovered.append(
            Migration.from_bytes(
                version=int(match.group("version")),
                name=match.group("name"),
                filename=entry.name,
                sql_bytes=sql_bytes,
            )
        )

    return validate_migrations(discovered)


def _split_sql_statements(sql_bytes: bytes) -> tuple[str, ...]:
    """Split UTF-8 SQL only at SQLite-complete statement boundaries."""
    try:
        sql = sql_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError("migration SQL must be valid UTF-8") from exc

    statements: list[str] = []
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character != ";":
            continue
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statements.append(candidate.strip())
            buffer.clear()

    remainder = "".join(buffer).strip()
    if remainder and _COMMENT_ONLY_SQL.fullmatch(remainder):
        remainder = ""
    if remainder:
        if sqlite3.complete_statement(remainder):
            statements.append(remainder)
        else:
            raise MigrationError("migration SQL contains an incomplete statement")

    return tuple(statement for statement in statements if statement)


def _reject_transaction_control(statements: Iterable[str]) -> None:
    """Reject SQL that can escape the runner-owned migration transaction."""
    for statement in statements:
        executable = _LEADING_SQL_TRIVIA.sub("", statement, count=1)
        if _TRANSACTION_CONTROL.match(executable):
            raise MigrationError("migration SQL must not control transactions")


def _history_trigger_sql_is_canonical(
    *,
    trigger_name: str,
    operation: str,
    sql: str,
) -> bool:
    """Return whether a history trigger exactly enforces the canonical abort rule."""
    normalized = re.sub(r"\s+", " ", sql.strip())
    pattern = (
        rf"CREATE TRIGGER(?: IF NOT EXISTS)? {re.escape(trigger_name)} "
        rf"BEFORE {re.escape(operation)} ON {_SCHEMA_MIGRATIONS_TABLE} "
        r"BEGIN SELECT RAISE\s*\(\s*ABORT\s*,\s*"
        r"'schema_migrations is immutable'\s*\)\s*;\s*END;?"
    )
    return re.fullmatch(pattern, normalized, flags=re.IGNORECASE) is not None


class MigrationRunner:
    """Apply immutable, checksum-verified SQLite migrations."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        migrations: Iterable[Migration] | None = None,
        busy_timeout_ms: int = 5_000,
        source_revision: str | None = None,
    ) -> None:
        """Configure a runner without opening or mutating the target database."""
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self._db_path = Path(db_path)
        self._migrations = validate_migrations(
            discover_migrations() if migrations is None else migrations
        )
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._source_revision = source_revision

    def verify(self) -> tuple[int, ...]:
        """Verify existing migration history without mutating the database."""
        if not self._db_path.is_file():
            raise MigrationError("database does not exist")

        timeout_seconds = max(self._busy_timeout_ms / 1_000.0, 0.001)
        uri = f"{self._db_path.resolve().as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=timeout_seconds,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise MigrationError("database open failed") from exc

        try:
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            return tuple(sorted(self._verify_state(conn)))
        finally:
            conn.close()

    def apply(self) -> tuple[int, ...]:
        """Apply pending migrations and return versions applied by this runner."""
        if not self._db_path.parent.is_dir():
            raise MigrationError("database parent directory does not exist")

        timeout_seconds = max(self._busy_timeout_ms / 1_000.0, 0.001)

        try:
            conn = sqlite3.connect(
                self._db_path,
                timeout=timeout_seconds,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise MigrationError("database open failed") from exc

        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            self._bootstrap_history(conn)
            applied_versions = self._verify_state(conn)

            newly_applied: list[int] = []
            for migration in self._migrations:
                if migration.version in applied_versions:
                    continue
                if self._apply_one(conn, migration):
                    newly_applied.append(migration.version)
                applied_versions = self._verify_state(conn)

            return tuple(newly_applied)
        finally:
            conn.close()

    @staticmethod
    def _bootstrap_history(conn: sqlite3.Connection) -> None:
        """Create immutable migration-history infrastructure atomically."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT type, name, tbl_name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            history_rows = [
                row
                for row in rows
                if str(row[1]).casefold() == _SCHEMA_MIGRATIONS_TABLE.casefold()
            ]
            if history_rows:
                history_row = history_rows[0]
                if (
                    len(history_rows) != 1
                    or str(history_row[0]).casefold() != "table"
                    or str(history_row[1]) != _SCHEMA_MIGRATIONS_TABLE
                    or str(history_row[2]) != _SCHEMA_MIGRATIONS_TABLE
                ):
                    raise MigrationStateError(
                        "migration history name is owned by a noncanonical object"
                    )
                MigrationRunner._verify_history_schema(conn)
                conn.execute("COMMIT")
                return

            reserved = {
                name.casefold() for name in migration_infrastructure_object_names()
            }
            if any(str(row[1]).casefold() in reserved for row in rows):
                raise MigrationStateError(
                    "migration infrastructure name is already in use"
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    applied_at INTEGER NOT NULL,
                    source_revision TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS schema_migrations_no_update
                BEFORE UPDATE ON schema_migrations
                BEGIN
                    SELECT RAISE(ABORT, 'schema_migrations is immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS schema_migrations_no_delete
                BEFORE DELETE ON schema_migrations
                BEGIN
                    SELECT RAISE(ABORT, 'schema_migrations is immutable');
                END
                """
            )
            MigrationRunner._verify_history_schema(conn)
            conn.execute("COMMIT")
        except (sqlite3.Error, MigrationError) as exc:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("migration history bootstrap failed") from exc

    @staticmethod
    def _verify_history_triggers(conn: sqlite3.Connection) -> None:
        """Fail closed unless both canonical immutability triggers are installed."""
        try:
            rows = conn.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type='trigger' AND name IN (?, ?)",
                tuple(_EXPECTED_HISTORY_TRIGGERS),
            ).fetchall()
        except sqlite3.Error as exc:
            raise MigrationStateError("migration history triggers are unreadable") from exc

        triggers = {str(row[0]): row for row in rows}
        if set(triggers) != set(_EXPECTED_HISTORY_TRIGGERS):
            raise MigrationStateError("migration history immutability triggers are missing")

        for trigger_name, operation in _EXPECTED_HISTORY_TRIGGERS.items():
            row = triggers[trigger_name]
            target_table = str(row[1]).casefold()
            trigger_sql = row[2]
            if target_table != _SCHEMA_MIGRATIONS_TABLE.casefold():
                raise MigrationStateError(
                    "migration history immutability trigger target is invalid"
                )
            if not isinstance(trigger_sql, str) or not _history_trigger_sql_is_canonical(
                trigger_name=trigger_name,
                operation=operation,
                sql=trigger_sql,
            ):
                raise MigrationStateError(
                    "migration history immutability trigger definition is invalid"
                )

    @staticmethod
    def _verify_history_schema(conn: sqlite3.Connection) -> None:
        """Fail closed unless migration history has the required canonical schema."""
        try:
            rows = conn.execute("PRAGMA table_info(schema_migrations)").fetchall()
        except sqlite3.Error as exc:
            raise MigrationStateError("migration history schema is unreadable") from exc

        columns = {str(row[1]): row for row in rows}
        missing = set(_REQUIRED_HISTORY_COLUMNS) - set(columns)
        if missing:
            raise MigrationStateError("migration history schema is incomplete")

        primary_key_columns = [str(row[1]) for row in rows if int(row[5]) > 0]
        if primary_key_columns != ["version"]:
            raise MigrationStateError(
                "migration history must use version as its sole primary key"
            )

        for name, (required_type, required_notnull, required_pk) in (
            _REQUIRED_HISTORY_COLUMNS.items()
        ):
            row = columns[name]
            declared_type = str(row[2]).strip().upper()
            notnull = int(row[3])
            primary_key = int(row[5])
            if declared_type != required_type or primary_key != required_pk:
                raise MigrationStateError(
                    "migration history schema constraints are invalid"
                )
            if required_notnull is not None and notnull != required_notnull:
                raise MigrationStateError(
                    "migration history schema constraints are invalid"
                )

        MigrationRunner._verify_history_triggers(conn)

    def _verify_state(self, conn: sqlite3.Connection) -> set[int]:
        """Validate history schema, rows, checksums, and ``user_version``."""
        self._verify_history_schema(conn)
        try:
            rows = conn.execute(
                "SELECT version, name, checksum_sha256 "
                "FROM schema_migrations ORDER BY version"
            ).fetchall()
            user_version_row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(user_version_row[0])
            history_versions = [int(row[0]) for row in rows]
        except (sqlite3.Error, TypeError, ValueError, IndexError) as exc:
            raise MigrationStateError("migration history state is malformed") from exc

        expected_history = list(range(1, len(history_versions) + 1))
        if history_versions != expected_history:
            raise MigrationStateError("migration history is not contiguous")

        expected_user_version = history_versions[-1] if history_versions else 0
        if user_version != expected_user_version:
            raise MigrationStateError(
                "PRAGMA user_version disagrees with migration history"
            )

        migrations_by_version = {
            migration.version: migration for migration in self._migrations
        }
        for version, name, checksum in rows:
            migration = migrations_by_version.get(int(version))
            if migration is None:
                raise MigrationStateError(
                    "applied migration version is missing from the migration set"
                )
            if str(name) != migration.name:
                raise MigrationStateError("applied migration name has changed")
            if str(checksum) != migration.checksum_sha256:
                raise MigrationChecksumMismatch(
                    "applied migration checksum does not match migration bytes"
                )

        return set(history_versions)

    def _apply_one(self, conn: sqlite3.Connection, migration: Migration) -> bool:
        """Apply one migration under a serialized pending check.

        Returns ``True`` when this runner applies the migration and ``False``
        when another runner committed it before this runner acquired the lock.
        """
        statements = _split_sql_statements(migration.sql_bytes)
        if not statements:
            raise MigrationError("migration SQL must contain at least one statement")
        _reject_transaction_control(statements)

        try:
            conn.execute("BEGIN IMMEDIATE")
            applied_versions = self._verify_state(conn)
            if migration.version in applied_versions:
                conn.execute("COMMIT")
                return False

            expected_version = max(applied_versions, default=0) + 1
            if migration.version != expected_version:
                raise MigrationStateError(
                    "migration application order disagrees with history"
                )

            for statement in statements:
                conn.execute(statement)
            conn.execute(
                """
                INSERT INTO schema_migrations (
                    version,
                    name,
                    checksum_sha256,
                    applied_at,
                    source_revision
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    int(time.time()),
                    self._source_revision,
                ),
            )
            conn.execute(f"PRAGMA user_version={migration.version}")
            conn.execute("COMMIT")
            return True
        except Exception as exc:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(
                f"migration version {migration.version} failed"
            ) from exc
