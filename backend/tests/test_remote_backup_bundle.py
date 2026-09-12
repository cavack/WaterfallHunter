from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from waterfallhunter.core.remote_backup_bundle import (
    RemoteBackupBundleError,
    encrypt_sqlite_backup_bundle,
    restore_sqlite_backup_bundle,
)
from waterfallhunter.core.sqlite_backup_certification import audit_sqlite_snapshot


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
        for _ in range(32):
            connection.execute("INSERT INTO sample(payload) VALUES (?)", (os.urandom(256),))
        connection.execute("PRAGMA user_version=5")


def test_encrypt_split_and_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "backup.db"
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    restored = tmp_path / "restored.db"
    _database(source)
    key = os.urandom(32)
    manifest = encrypt_sqlite_backup_bundle(
        source=source,
        output_dir=output_dir,
        prefix="registry",
        key=key,
        max_chunk_bytes=512,
    )
    assert manifest["contract_version"] == "wfh_encrypted_backup_bundle_v1"
    assert manifest["algorithm"] == "AES-256-GCM"
    assert len(manifest["chunks"]) > 1

    restore_sqlite_backup_bundle(
        manifest_path=output_dir / "registry.manifest.json",
        bundle_dir=output_dir,
        target=restored,
        key=key,
    )
    assert audit_sqlite_snapshot(source)["logical_content_sha256"] == audit_sqlite_snapshot(restored)["logical_content_sha256"]
    assert restored.read_bytes() == source.read_bytes()


def test_restore_rejects_tampered_encrypted_chunk(tmp_path: Path) -> None:
    source = tmp_path / "backup.db"
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    restored = tmp_path / "restored.db"
    _database(source)
    key = os.urandom(32)
    manifest = encrypt_sqlite_backup_bundle(
        source=source,
        output_dir=output_dir,
        prefix="registry",
        key=key,
        max_chunk_bytes=1024,
    )
    first = output_dir / manifest["chunks"][0]["name"]
    first.write_bytes(first.read_bytes() + b"tamper")

    with pytest.raises(RemoteBackupBundleError, match="BUNDLE_CHUNK_MISMATCH"):
        restore_sqlite_backup_bundle(
            manifest_path=output_dir / "registry.manifest.json",
            bundle_dir=output_dir,
            target=restored,
            key=key,
        )


def test_restore_wrong_key_fails_closed_without_plaintext_artifact(tmp_path: Path) -> None:
    source = tmp_path / "wrong-key-backup.db"
    output_dir = tmp_path / "wrong-key-bundle"
    output_dir.mkdir()
    restored = tmp_path / "wrong-key-restored.db"
    key = os.urandom(32)
    _database(source)
    encrypt_sqlite_backup_bundle(
        source=source,
        output_dir=output_dir,
        prefix="registry",
        key=key,
        max_chunk_bytes=1024,
    )

    with pytest.raises(RemoteBackupBundleError):
        restore_sqlite_backup_bundle(
            manifest_path=output_dir / "registry.manifest.json",
            bundle_dir=output_dir,
            target=restored,
            key=os.urandom(32),
        )
    assert not restored.exists()
    assert not restored.with_name(f".{restored.name}.partial").exists()
