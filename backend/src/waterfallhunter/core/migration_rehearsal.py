"""Isolated canonical-migration and backup-based rollback rehearsal."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import closing, redirect_stdout
from pathlib import Path
from typing import Any

from waterfallhunter.core.migration_preflight import PreflightState, classify_database
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.sqlite_backup_certification import (
    BackupCertificationError,
    audit_sqlite_snapshot,
    restore_sqlite_snapshot,
)
from waterfallhunter.migrate_database import main as migration_main


class MigrationRehearsalError(RuntimeError):
    """Raised when staging migration or rollback evidence is incomplete."""


_ROLLBACK_COMPARABLE_FIELDS = (
    "logical_content_sha256",
    "user_version",
    "schema_version",
    "object_counts",
    "table_counts",
    "schema_sha256",
)



def migration_executable_sha256() -> str:
    """Hash the exact migration/rehearsal implementation and packaged SQL."""
    package_root = Path(__file__).resolve().parents[1]
    fixed = (
        package_root / "migrate_database.py",
        package_root / "core" / "migration_rehearsal.py",
        package_root / "core" / "migration_preflight.py",
        package_root / "core" / "migrations.py",
        package_root / "core" / "schema_contract.py",
        package_root / "core" / "schema_unique_constraints.py",
        package_root / "core" / "signal_metadata.py",
    )
    migration_dir = package_root / "migrations"
    sql_files = tuple(sorted(migration_dir.glob("*.sql")))
    paths = (*fixed, *sql_files)
    if not sql_files:
        raise MigrationRehearsalError("MIGRATION_EXECUTABLE_IDENTITY_UNAVAILABLE")
    material: list[dict[str, str]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise MigrationRehearsalError("MIGRATION_EXECUTABLE_IDENTITY_UNAVAILABLE")
        relative = path.relative_to(package_root).as_posix()
        material.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return canonical_sha256(material)

def _verified_backup_path(certification: dict[str, Any]) -> Path:
    claimed_hash = str(certification.get("certification_sha256", ""))
    material = {
        key: value
        for key, value in certification.items()
        if key != "certification_sha256"
    }
    if claimed_hash != canonical_sha256(material):
        raise MigrationRehearsalError("BACKUP_CERTIFICATION_HASH_MISMATCH")
    if certification.get("status") != "BACKUP_RESTORE_CERTIFIED":
        raise MigrationRehearsalError("BACKUP_NOT_CERTIFIED")

    remote = certification.get("contract_version") == "sqlite_remote_backup_certification_v1"
    path_key = "local_restore_path" if remote else "backup_path"
    audit_key = "restore_audit" if remote else "backup_audit"
    backup = Path(str(certification.get(path_key, "")))
    if not backup.is_absolute() or backup.is_symlink() or not backup.is_file():
        raise MigrationRehearsalError("CERTIFIED_BACKUP_UNREADABLE")
    sidecars = [Path(f"{backup}{suffix}") for suffix in ("-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        raise MigrationRehearsalError("CERTIFIED_BACKUP_HAS_SQLITE_SIDECARS")
    try:
        audit = audit_sqlite_snapshot(backup)
    except (BackupCertificationError, OSError) as error:
        raise MigrationRehearsalError("CERTIFIED_BACKUP_UNREADABLE") from error
    certified_audit = certification.get(audit_key)
    if not isinstance(certified_audit, dict) or (
        audit.get("audit_sha256") != certified_audit.get("audit_sha256")
    ):
        raise MigrationRehearsalError("CERTIFIED_BACKUP_AUDIT_MISMATCH")
    if any(path.exists() for path in sidecars):
        raise MigrationRehearsalError("CERTIFIED_BACKUP_CREATED_SQLITE_SIDECARS")
    return backup


def _require_isolated_targets(
    backup: Path,
    migration_target: Path,
    rollback_target: Path,
) -> None:
    destination = backup.parent.resolve()
    targets = (migration_target.resolve(strict=False), rollback_target.resolve(strict=False))
    if targets[0] == targets[1]:
        raise MigrationRehearsalError("MIGRATION_AND_ROLLBACK_TARGETS_MUST_DIFFER")
    if any(target.parent != destination for target in targets):
        raise MigrationRehearsalError("REHEARSAL_TARGET_OUTSIDE_CERTIFIED_DESTINATION")
    device = backup.stat().st_dev
    if any(target.parent.stat().st_dev != device for target in targets):
        raise MigrationRehearsalError("REHEARSAL_TARGET_DEVICE_MISMATCH")


def _finalize_sqlite_snapshot(target: Path) -> None:
    """Checkpoint WAL state into one standalone database file for audit/cleanup."""
    try:
        with closing(
            sqlite3.connect(target, timeout=30.0, isolation_level=None)
        ) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            journal = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if str(journal[0] if journal else "").lower() != "delete":
                raise MigrationRehearsalError("MIGRATION_JOURNAL_FINALIZATION_FAILED")
    except sqlite3.Error as error:
        raise MigrationRehearsalError("MIGRATION_JOURNAL_FINALIZATION_FAILED") from error
    sidecars = [Path(f"{target}{suffix}") for suffix in ("-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        raise MigrationRehearsalError("MIGRATION_ARTIFACT_HAS_SQLITE_SIDECARS")


def _run_canonical_migration(target: Path, source_revision: str) -> dict[str, Any]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = migration_main(
            [
                "--db-path",
                str(target),
                "--source-revision",
                source_revision,
                "--apply",
            ]
        )
    line = output.getvalue().strip()
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise MigrationRehearsalError("MIGRATION_OUTPUT_INVALID") from error
    if status != 0 or payload.get("ok") is not True:
        reasons = ",".join(str(value) for value in payload.get("reason_codes", []))
        raise MigrationRehearsalError(f"STAGING_MIGRATION_FAILED:{reasons}")
    return payload


def rehearse_migration_and_rollback(
    *,
    backup_certification: dict[str, Any],
    migration_target: Path,
    rollback_target: Path,
    source_revision: str,
) -> dict[str, Any]:
    """Migrate one restored clone, then prove rollback on another clean clone."""
    if len(source_revision) != 40 or any(
        character not in "0123456789abcdef" for character in source_revision
    ):
        raise MigrationRehearsalError("SOURCE_REVISION_INVALID")
    backup = _verified_backup_path(backup_certification)
    _require_isolated_targets(backup, migration_target, rollback_target)
    baseline_audit = audit_sqlite_snapshot(backup)
    pre_migration_audit = restore_sqlite_snapshot(
        source=backup,
        target=migration_target,
    )
    if any(
        baseline_audit[field] != pre_migration_audit[field]
        for field in _ROLLBACK_COMPARABLE_FIELDS
    ):
        raise MigrationRehearsalError("MIGRATION_CLONE_MISMATCH")

    migration_result = _run_canonical_migration(migration_target, source_revision)
    postflight = classify_database(db_path=migration_target)
    if (
        postflight.state is not PreflightState.MIGRATED_COMPATIBLE
        or postflight.user_version != CURRENT_RUNTIME_SCHEMA_VERSION
    ):
        raise MigrationRehearsalError("MIGRATION_POSTFLIGHT_FAILED")
    post_migration_audit = audit_sqlite_snapshot(migration_target)

    rollback_audit = restore_sqlite_snapshot(source=backup, target=rollback_target)
    rollback_mismatches = [
        field
        for field in _ROLLBACK_COMPARABLE_FIELDS
        if baseline_audit[field] != rollback_audit[field]
    ]
    if rollback_mismatches:
        raise MigrationRehearsalError(
            "ROLLBACK_RESTORE_MISMATCH:" + ",".join(rollback_mismatches)
        )
    body = {
        "contract_version": "sqlite_migration_rollback_rehearsal_v1",
        "status": "MIGRATION_AND_ROLLBACK_REHEARSED",
        "source_revision": source_revision,
        "migration_executable_sha256": migration_executable_sha256(),
        "backup_certification_sha256": backup_certification["certification_sha256"],
        "baseline_audit_sha256": baseline_audit["audit_sha256"],
        "migration_target": str(migration_target),
        "migration_result": migration_result,
        "post_migration_audit": post_migration_audit,
        "rollback_target": str(rollback_target),
        "rollback_audit": rollback_audit,
        "rollback_matches_baseline": True,
        "production_migration_authorized": False,
        "production_deployment_authorized": False,
    }
    return {**body, "rehearsal_sha256": canonical_sha256(body)}


def rehearse_migration_and_rollback_sequential(
    *,
    backup_certification: dict[str, Any],
    working_target: Path,
    source_revision: str,
) -> dict[str, Any]:
    """Prove migration and rollback with one reusable working database target."""
    if len(source_revision) != 40 or any(
        character not in "0123456789abcdef" for character in source_revision
    ):
        raise MigrationRehearsalError("SOURCE_REVISION_INVALID")
    backup = _verified_backup_path(backup_certification)
    target = working_target.resolve(strict=False)
    destination = backup.parent.resolve()
    if target.parent != destination or target == backup.resolve():
        raise MigrationRehearsalError("REHEARSAL_TARGET_OUTSIDE_CERTIFIED_DESTINATION")
    if target.exists() or target.is_symlink():
        raise MigrationRehearsalError("REHEARSAL_TARGET_ALREADY_EXISTS")
    if target.parent.stat().st_dev != backup.stat().st_dev:
        raise MigrationRehearsalError("REHEARSAL_TARGET_DEVICE_MISMATCH")

    baseline_audit = audit_sqlite_snapshot(backup)
    rollback_retained = False
    try:
        pre_migration_audit = restore_sqlite_snapshot(source=backup, target=target)
        if any(
            baseline_audit[field] != pre_migration_audit[field]
            for field in _ROLLBACK_COMPARABLE_FIELDS
        ):
            raise MigrationRehearsalError("MIGRATION_CLONE_MISMATCH")
        migration_result = _run_canonical_migration(target, source_revision)
        _finalize_sqlite_snapshot(target)
        postflight = classify_database(db_path=target)
        if (
            postflight.state is not PreflightState.MIGRATED_COMPATIBLE
            or postflight.user_version != CURRENT_RUNTIME_SCHEMA_VERSION
        ):
            raise MigrationRehearsalError("MIGRATION_POSTFLIGHT_FAILED")
        post_migration_audit = audit_sqlite_snapshot(target)

        sidecars = [Path(f"{target}{suffix}") for suffix in ("-wal", "-shm")]
        if any(path.exists() for path in sidecars):
            raise MigrationRehearsalError("MIGRATION_ARTIFACT_HAS_SQLITE_SIDECARS")
        target.unlink()

        rollback_audit = restore_sqlite_snapshot(source=backup, target=target)
        rollback_mismatches = [
            field
            for field in _ROLLBACK_COMPARABLE_FIELDS
            if baseline_audit[field] != rollback_audit[field]
        ]
        if rollback_mismatches:
            raise MigrationRehearsalError(
                "ROLLBACK_RESTORE_MISMATCH:" + ",".join(rollback_mismatches)
            )
        rollback_retained = True
        body = {
            "contract_version": "sqlite_migration_rollback_rehearsal_v2",
            "status": "MIGRATION_AND_ROLLBACK_REHEARSED",
            "source_revision": source_revision,
            "migration_executable_sha256": migration_executable_sha256(),
            "backup_certification_sha256": backup_certification["certification_sha256"],
            "baseline_audit_sha256": baseline_audit["audit_sha256"],
            "working_target": str(target),
            "migration_result": migration_result,
            "post_migration_audit": post_migration_audit,
            "migration_artifact_retained": False,
            "rollback_audit": rollback_audit,
            "rollback_matches_baseline": True,
            "rollback_artifact_retained": True,
            "production_migration_authorized": False,
            "production_deployment_authorized": False,
        }
        return {**body, "rehearsal_sha256": canonical_sha256(body)}
    finally:
        if not rollback_retained:
            for artifact in (target, Path(f"{target}-wal"), Path(f"{target}-shm")):
                artifact.unlink(missing_ok=True)
