from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from unittest import mock

import pytest

from waterfallhunter.core.sqlite_backup_certification import (
    BackupCertificationError,
    audit_sqlite_snapshot,
    create_certified_backup,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            PRAGMA journal_mode=WAL;
            CREATE TABLE parent(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE child(
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id),
                value TEXT NOT NULL
            );
            CREATE INDEX child_parent_idx ON child(parent_id);
            INSERT INTO parent(id, name) VALUES (1, 'redacted');
            INSERT INTO child(id, parent_id, value) VALUES (1, 1, 'redacted');
            PRAGMA user_version=7;
            """
        )


def test_online_backup_restore_is_integrity_and_count_certified(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "independent"
    source_dir.mkdir()
    destination_dir.mkdir()
    source = source_dir / "registry.db"
    backup = destination_dir / "registry.backup.db"
    restore = destination_dir / "restore-test.db"
    _database(source)

    report = create_certified_backup(
        source=source,
        backup=backup,
        restore_target=restore,
        source_failure_domain="production-block-volume",
        destination_failure_domain="independent-object-mount",
        enforce_distinct_device=False,
    )

    source_stat = source.stat()
    assert report["status"] == "BACKUP_RESTORE_CERTIFIED"
    assert report["source_path"] == str(source.resolve())
    assert set(report["source_identity"]) == {"device_id", "inode"}
    assert report["source_identity"] == {
        "device_id": source_stat.st_dev,
        "inode": source_stat.st_ino,
    }
    assert isinstance(report["backup_started_at"], int)
    assert isinstance(report["backup_completed_at"], int)
    assert report["backup_completed_at"] >= report["backup_started_at"] >= 1
    assert report["restore_matches_backup"] is True
    assert report["backup_audit"]["integrity_check"] == "ok"
    assert report["backup_audit"]["foreign_key_violation_count"] == 0
    assert report["backup_audit"]["table_counts"] == {"child": 1, "parent": 1}
    assert report["backup_audit"]["schema_sha256"] == report["restore_audit"][
        "schema_sha256"
    ]
    assert report["production_migration_authorized"] is False
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(restore.stat().st_mode) == 0o600
    assert len(report["certification_sha256"]) == 64
    assert audit_sqlite_snapshot(backup)["audit_sha256"] == report["backup_audit"][
        "audit_sha256"
    ]


def test_backup_fails_closed_when_source_identity_changes_after_open(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "independent"
    source_dir.mkdir()
    destination_dir.mkdir()
    source = source_dir / "registry.db"
    replacement = source_dir / "replacement.db"
    _database(source)
    _database(replacement)

    from waterfallhunter.core import sqlite_backup_certification as module

    real_bound = module._open_read_only_identity_bound

    def bound_then_replace(path: Path):
        connection, descriptor, identity = real_bound(path)
        path.unlink()
        os.link(replacement, path)
        return connection, descriptor, identity

    with mock.patch.object(
        module,
        "_open_read_only_identity_bound",
        side_effect=bound_then_replace,
    ):
        with pytest.raises(
            BackupCertificationError,
            match="BACKUP_SOURCE_IDENTITY_CHANGED",
        ):
            create_certified_backup(
                source=source,
                backup=destination_dir / "backup.db",
                restore_target=destination_dir / "restore.db",
                source_failure_domain="production-block-volume",
                destination_failure_domain="independent-object-mount",
                enforce_distinct_device=False,
            )

    assert not (destination_dir / "backup.db").exists()


def test_backup_fails_before_writing_when_failure_domain_is_not_independent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "registry.db"
    _database(source)
    backup = tmp_path / "backup.db"

    with pytest.raises(BackupCertificationError, match="FAILURE_DOMAIN_NOT_INDEPENDENT"):
        create_certified_backup(
            source=source,
            backup=backup,
            restore_target=tmp_path / "restore.db",
            source_failure_domain="same-volume",
            destination_failure_domain="same-volume",
            enforce_distinct_device=False,
        )

    assert not backup.exists()


def test_backup_never_overwrites_an_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "registry.db"
    backup = tmp_path / "backup.db"
    _database(source)
    backup.write_bytes(b"operator-owned")

    with pytest.raises(BackupCertificationError, match="BACKUP_TARGET_INVALID"):
        create_certified_backup(
            source=source,
            backup=backup,
            restore_target=tmp_path / "restore.db",
            source_failure_domain="source",
            destination_failure_domain="destination",
            enforce_distinct_device=False,
        )

    assert backup.read_bytes() == b"operator-owned"


def test_backup_and_restore_targets_must_be_distinct(tmp_path: Path) -> None:
    source = tmp_path / "registry.db"
    target = tmp_path / "snapshot.db"
    _database(source)

    with pytest.raises(BackupCertificationError, match="TARGETS_MUST_DIFFER"):
        create_certified_backup(
            source=source,
            backup=target,
            restore_target=target,
            source_failure_domain="source",
            destination_failure_domain="destination",
            enforce_distinct_device=False,
        )

    assert not target.exists()


def test_snapshot_audit_releases_read_only_handle_for_journal_finalization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "audit-handle.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    audit = audit_sqlite_snapshot(source)
    assert audit["integrity_check"] == "ok"

    connection = sqlite3.connect(source, timeout=1.0, isolation_level=None)
    try:
        row = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        connection.close()
    assert str(row[0] if row else "").lower() == "delete"
