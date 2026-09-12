"""Fail-closed SQLite online-backup and isolated-restore certification."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from waterfallhunter.core.signal_metadata import canonical_sha256


class BackupCertificationError(RuntimeError):
    """Raised before a backup can be represented as certified."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _identity_from_stat(stat_result: os.stat_result) -> dict[str, int]:
    return {
        "device_id": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        isolation_level=None,
        timeout=30.0,
    )


def _open_read_only_identity_bound(
    path: Path,
) -> tuple[sqlite3.Connection, int, dict[str, int]]:
    """Open the exact inode behind path; keep the fd alive for the connection."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        identity = _identity_from_stat(os.fstat(descriptor))
        path_identity = _identity_from_stat(path.stat())
        if path_identity != identity:
            raise BackupCertificationError("BACKUP_SOURCE_IDENTITY_CHANGED")
        connection = sqlite3.connect(
            f"file:/proc/self/fd/{descriptor}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=30.0,
        )
        return connection, descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def audit_sqlite_snapshot(path: Path) -> dict[str, Any]:
    """Audit a closed/restored snapshot without exposing row contents."""
    connection = _open_read_only(path)
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        objects = connection.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        tables = [
            str(row[1])
            for row in objects
            if str(row[0]) == "table"
        ]
        table_counts = {
            name: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(name)}"
                ).fetchone()[0]
            )
            for name in tables
        }
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        logical_digest = hashlib.sha256()
        for statement in connection.iterdump():
            logical_digest.update(statement.encode("utf-8"))
            logical_digest.update(b"\n")
    finally:
        connection.close()
    if integrity != ["ok"]:
        raise BackupCertificationError("SQLITE_INTEGRITY_CHECK_FAILED")
    if foreign_key_violations:
        raise BackupCertificationError("SQLITE_FOREIGN_KEY_CHECK_FAILED")
    schema_material = [
        {
            "type": str(object_type),
            "name": str(name),
            "table": str(table_name),
            "sql": str(sql),
        }
        for object_type, name, table_name, sql in objects
    ]
    body = {
        "contract_version": "sqlite_snapshot_audit_v1",
        "file_sha256": _sha256_file(path),
        "file_size_bytes": path.stat().st_size,
        "integrity_check": "ok",
        "foreign_key_violation_count": 0,
        "user_version": user_version,
        "schema_version": schema_version,
        "object_counts": {
            kind: sum(1 for row in objects if str(row[0]) == kind)
            for kind in ("table", "index", "trigger", "view")
        },
        "table_counts": dict(sorted(table_counts.items())),
        "schema_sha256": canonical_sha256(schema_material),
        "logical_content_sha256": logical_digest.hexdigest(),
    }
    return {**body, "audit_sha256": canonical_sha256(body)}


def _require_source(path: Path) -> Path:
    if path.is_symlink() or not path.is_absolute() or not path.is_file():
        raise BackupCertificationError("BACKUP_SOURCE_INVALID")
    return path.resolve()


def _require_new_target(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or path.exists():
        raise BackupCertificationError(f"{label}_TARGET_INVALID")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise BackupCertificationError(f"{label}_PARENT_INVALID")
    resolved = path.resolve(strict=False)
    if resolved.parent != parent.resolve():
        raise BackupCertificationError(f"{label}_TARGET_AMBIGUOUS")
    return resolved


def _online_backup(source: Path, destination: Path) -> dict[str, int]:
    """Backup the inode opened for source; return that bound source identity."""
    partial = destination.with_name(f".{destination.name}.partial")
    if partial.exists() or partial.is_symlink():
        raise BackupCertificationError("BACKUP_PARTIAL_ALREADY_EXISTS")
    source_connection: sqlite3.Connection | None = None
    source_fd: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(partial, flags, 0o600)
        os.close(descriptor)
        source_connection, source_fd, identity = _open_read_only_identity_bound(source)
        target_connection = sqlite3.connect(partial, timeout=30.0)
        try:
            source_connection.backup(target_connection, pages=4_096, sleep=0.05)
            journal = target_connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if str(journal[0] if journal else "").lower() != "delete":
                raise BackupCertificationError("BACKUP_JOURNAL_FINALIZATION_FAILED")
        finally:
            target_connection.close()
        bound_after = _identity_from_stat(os.fstat(source_fd))
        path_after = _identity_from_stat(source.stat())
        if bound_after != identity or path_after != identity:
            raise BackupCertificationError("BACKUP_SOURCE_IDENTITY_CHANGED")
        descriptor = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(partial, destination)
        partial.unlink()
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return identity
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if source_connection is not None:
            source_connection.close()
        if source_fd is not None:
            os.close(source_fd)


def restore_sqlite_snapshot(*, source: Path, target: Path) -> dict[str, Any]:
    """Restore one immutable SQLite snapshot to a new isolated target."""
    source_path = _require_source(source)
    target_path = _require_new_target(target, label="RESTORE")
    _online_backup(source_path, target_path)
    return audit_sqlite_snapshot(target_path)


def create_certified_backup(
    *,
    source: Path,
    backup: Path,
    restore_target: Path,
    source_failure_domain: str,
    destination_failure_domain: str,
    enforce_distinct_device: bool = True,
) -> dict[str, Any]:
    """Create, validate, and independently restore one immutable backup."""
    source_path = _require_source(source)
    identity_before = _identity_from_stat(source_path.stat())
    backup_path = _require_new_target(backup, label="BACKUP")
    restore_path = _require_new_target(restore_target, label="RESTORE")
    if backup_path == restore_path:
        raise BackupCertificationError("BACKUP_AND_RESTORE_TARGETS_MUST_DIFFER")
    if not source_failure_domain.strip() or not destination_failure_domain.strip():
        raise BackupCertificationError("FAILURE_DOMAIN_ID_REQUIRED")
    if source_failure_domain == destination_failure_domain:
        raise BackupCertificationError("FAILURE_DOMAIN_NOT_INDEPENDENT")
    if enforce_distinct_device and (
        source_path.stat().st_dev == backup_path.parent.stat().st_dev
    ):
        raise BackupCertificationError("BACKUP_DEVICE_NOT_INDEPENDENT")
    if backup_path.parent.stat().st_dev != restore_path.parent.stat().st_dev:
        raise BackupCertificationError("RESTORE_TARGET_OUTSIDE_DESTINATION_DOMAIN")

    backup_started_at = int(time.time())
    source_identity = _online_backup(source_path, backup_path)
    if source_identity != identity_before:
        raise BackupCertificationError("BACKUP_SOURCE_IDENTITY_CHANGED")
    identity_after = _identity_from_stat(source_path.stat())
    if identity_after != source_identity:
        raise BackupCertificationError("BACKUP_SOURCE_IDENTITY_CHANGED")
    backup_completed_at = int(time.time())
    if backup_completed_at < backup_started_at:
        raise BackupCertificationError("BACKUP_TIMESTAMP_INVALID")
    backup_audit = audit_sqlite_snapshot(backup_path)
    _online_backup(backup_path, restore_path)
    restore_audit = audit_sqlite_snapshot(restore_path)
    comparable_fields = (
        "user_version",
        "schema_version",
        "logical_content_sha256",
        "object_counts",
        "table_counts",
        "schema_sha256",
    )
    mismatches = [
        field
        for field in comparable_fields
        if backup_audit[field] != restore_audit[field]
    ]
    if mismatches:
        raise BackupCertificationError(
            "RESTORE_AUDIT_MISMATCH:" + ",".join(mismatches)
        )
    body = {
        "contract_version": "sqlite_backup_certification_v1",
        "status": "BACKUP_RESTORE_CERTIFIED",
        "source_path": str(source_path),
        "source_identity": source_identity,
        "source_failure_domain": source_failure_domain,
        "destination_failure_domain": destination_failure_domain,
        "device_separation_enforced": enforce_distinct_device,
        "backup_path": str(backup_path),
        "restore_target_path": str(restore_path),
        "backup_started_at": backup_started_at,
        "backup_completed_at": backup_completed_at,
        "backup_audit": backup_audit,
        "restore_audit": restore_audit,
        "restore_matches_backup": True,
        "rollback_source_sha256": backup_audit["file_sha256"],
        "production_migration_authorized": False,
        "production_deployment_authorized": False,
    }
    return {**body, "certification_sha256": canonical_sha256(body)}
