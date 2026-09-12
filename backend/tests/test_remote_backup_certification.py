from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from waterfallhunter.core.github_release_backup_verification import (
    TrustedRemoteBackupVerification,
)
from waterfallhunter.core.remote_backup_certification import (
    RemoteBackupCertificationError,
    build_remote_backup_certification,
    validate_remote_encryption_evidence,
)
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.sqlite_backup_certification import audit_sqlite_snapshot


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES ('redacted')")
        connection.execute("PRAGMA user_version=5")
        connection.commit()
    finally:
        connection.close()


def _manifest(backup_audit: dict) -> dict[str, object]:
    return {
        "contract_version": "wfh_encrypted_backup_bundle_v1",
        "algorithm": "AES-256-GCM",
        "compression": "zlib",
        "nonce_b64": base64.b64encode(b"n" * 12).decode("ascii"),
        "tag_b64": base64.b64encode(b"t" * 16).decode("ascii"),
        "plaintext_size_bytes": backup_audit["file_size_bytes"],
        "plaintext_sha256": backup_audit["file_sha256"],
        "ciphertext_sha256": "c" * 64,
        "max_chunk_bytes": 1_500_000_000,
        "chunks": [
            {
                "name": "part-000.enc",
                "index": 0,
                "size_bytes": 1234,
                "sha256": "a" * 64,
            }
        ],
    }


def _manifest_payload(backup_audit: dict) -> bytes:
    return (
        json.dumps(_manifest(backup_audit), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _trusted(backup_audit: dict) -> TrustedRemoteBackupVerification:
    manifest_sha256 = hashlib.sha256(_manifest_payload(backup_audit)).hexdigest()
    body = {
        "contract_version": "github_release_backup_verification_v1",
        "github_host": "github.com",
        "repository": "cavack/wfh-dr",
        "release_id": 77,
        "tag_name": "wfh-dr-test",
        "private_repository": True,
        "published_at_epoch": 1_787_957_000,
        "asset_ids": {
            "part-000.enc": 101,
            "waterfall_registry.manifest.json": 102,
        },
        "asset_sha256": {
            "part-000.enc": "a" * 64,
            "waterfall_registry.manifest.json": manifest_sha256,
        },
    }
    return TrustedRemoteBackupVerification.model_validate(
        {
            **body,
            "verification_report_sha256": canonical_sha256(body),
        }
    )


def _assets(backup_audit: dict) -> list[dict[str, object]]:
    payload = _manifest_payload(backup_audit)
    return [
        {"name": "part-000.enc", "id": 101, "size_bytes": 1234, "sha256": "a" * 64},
        {
            "name": "waterfall_registry.manifest.json",
            "id": 102,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    ]


def _encryption(backup_audit: dict) -> dict[str, object]:
    manifest = _manifest(backup_audit)
    return {
        "algorithm": "AES-256-GCM",
        "compression": "zlib",
        "manifest_asset_name": "waterfall_registry.manifest.json",
        "manifest_sha256": hashlib.sha256(_manifest_payload(backup_audit)).hexdigest(),
        "plaintext_sha256": backup_audit["file_sha256"],
        "ciphertext_sha256": "c" * 64,
        "chunk_count": 1,
        "manifest": manifest,
    }


def test_remote_backup_certificate_binds_off_host_release_and_restored_sqlite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    staging = tmp_path / "staging.db"
    restored = tmp_path / "restored.db"
    _database(source)
    _database(staging)
    _database(restored)
    source_identity = {"device_id": source.stat().st_dev, "inode": source.stat().st_ino}
    backup_audit = audit_sqlite_snapshot(staging)
    assets = _assets(backup_audit)
    report = build_remote_backup_certification(
        source=source,
        source_identity=source_identity,
        source_failure_domain="production-vda1",
        destination_failure_domain="github-private-release:cavack/wfh-dr",
        backup_audit=backup_audit,
        restored_backup_path=restored,
        remote_assets=assets,
        remote_verification=_trusted(backup_audit),
        backup_started_at=1_787_956_900,
        backup_completed_at=1_787_956_950,
        encryption=_encryption(backup_audit),
    )

    assert report["contract_version"] == "sqlite_remote_backup_certification_v1"
    assert report["status"] == "BACKUP_RESTORE_CERTIFIED"
    assert report["off_host_separation_enforced"] is True
    assert report["remote_repository"] == "cavack/wfh-dr"
    assert report["remote_release_id"] == 77
    assert report["restore_matches_backup"] is True
    assert report["backup_audit"]["audit_sha256"] == audit_sqlite_snapshot(staging)["audit_sha256"]
    assert report["restore_audit"]["audit_sha256"] == audit_sqlite_snapshot(restored)["audit_sha256"]
    assert report["production_migration_authorized"] is False
    assert report["production_deployment_authorized"] is False
    assert len(report["certification_sha256"]) == 64


def test_remote_backup_certificate_rejects_same_failure_domain(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    staging = tmp_path / "staging.db"
    restored = tmp_path / "restored.db"
    _database(source)
    _database(staging)
    _database(restored)
    backup_audit = audit_sqlite_snapshot(staging)
    source_stat = source.stat()
    remote_assets = _assets(backup_audit)
    remote_verification = _trusted(backup_audit)
    encryption = _encryption(backup_audit)
    with pytest.raises(RemoteBackupCertificationError, match="FAILURE_DOMAIN_NOT_INDEPENDENT"):
        build_remote_backup_certification(
            source=source,
            source_identity={"device_id": source_stat.st_dev, "inode": source_stat.st_ino},
            source_failure_domain="same",
            destination_failure_domain="same",
            backup_audit=backup_audit,
            restored_backup_path=restored,
            remote_assets=remote_assets,
            remote_verification=remote_verification,
            backup_started_at=1_787_956_900,
            backup_completed_at=1_787_956_950,
            encryption=encryption,
        )


def test_remote_backup_certificate_normalizes_failure_domains_before_comparison(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-normalized-domain.db"
    staging = tmp_path / "staging-normalized-domain.db"
    restored = tmp_path / "restored-normalized-domain.db"
    _database(source)
    _database(staging)
    _database(restored)
    backup_audit = audit_sqlite_snapshot(staging)

    with pytest.raises(RemoteBackupCertificationError, match="FAILURE_DOMAIN_NOT_INDEPENDENT"):
        build_remote_backup_certification(
            source=source,
            source_identity={"device_id": source.stat().st_dev, "inode": source.stat().st_ino},
            source_failure_domain="production-vda1",
            destination_failure_domain=" production-vda1 ",
            backup_audit=backup_audit,
            restored_backup_path=restored,
            remote_assets=_assets(backup_audit),
            remote_verification=_trusted(backup_audit),
            backup_started_at=1_787_956_900,
            backup_completed_at=1_787_956_950,
            encryption=_encryption(backup_audit),
        )


def test_remote_backup_certificate_accepts_precomputed_audit_after_plaintext_staging_removed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-precomputed.db"
    staging = tmp_path / "staging-precomputed.db"
    restored = tmp_path / "restored-precomputed.db"
    _database(source)
    _database(staging)
    _database(restored)
    backup_audit = audit_sqlite_snapshot(staging)
    staging.unlink()
    source_identity = {"device_id": source.stat().st_dev, "inode": source.stat().st_ino}
    report = build_remote_backup_certification(
        source=source,
        source_identity=source_identity,
        source_failure_domain="production-vda1",
        destination_failure_domain="github-private-release:cavack/wfh-dr",
        backup_audit=backup_audit,
        restored_backup_path=restored,
        remote_assets=_assets(backup_audit),
        remote_verification=_trusted(backup_audit),
        backup_started_at=1_787_956_900,
        backup_completed_at=1_787_956_950,
        encryption=_encryption(backup_audit),
    )
    assert report["backup_audit"]["audit_sha256"] == backup_audit["audit_sha256"]
    assert report["restore_matches_backup"] is True


def test_remote_backup_certificate_rejects_bundle_without_manifest_and_plaintext_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-incomplete-bundle.db"
    staging = tmp_path / "staging-incomplete-bundle.db"
    restored = tmp_path / "restored-incomplete-bundle.db"
    _database(source)
    _database(staging)
    _database(restored)
    source_stat = source.stat()
    backup_audit = audit_sqlite_snapshot(staging)
    remote_verification = _trusted(backup_audit)
    invalid_assets = [{"name": "part-000.enc", "id": 101, "size_bytes": 1234, "sha256": "a" * 64}]
    invalid_encryption = {"algorithm": "AES-256-GCM", "manifest_sha256": "b" * 64}
    with pytest.raises(RemoteBackupCertificationError, match="REMOTE_BACKUP_ENCRYPTION_INVALID"):
        build_remote_backup_certification(
            source=source,
            source_identity={"device_id": source_stat.st_dev, "inode": source_stat.st_ino},
            source_failure_domain="production-vda1",
            destination_failure_domain="github-private-release:cavack/wfh-dr",
            backup_audit=backup_audit,
            restored_backup_path=restored,
            remote_assets=invalid_assets,
            remote_verification=remote_verification,
            backup_started_at=1_787_956_900,
            backup_completed_at=1_787_956_950,
            encryption=invalid_encryption,
        )


def test_remote_backup_certificate_rejects_manifest_content_not_bound_to_assets(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging-manifest-mismatch.db"
    _database(staging)
    backup_audit = audit_sqlite_snapshot(staging)
    encryption = _encryption(backup_audit)
    encryption["manifest"] = {
        "contract_version": "wfh_encrypted_backup_bundle_v1",
        "algorithm": "AES-256-GCM",
        "compression": "zlib",
        "nonce_b64": "AAAAAAAAAAAAAAAA",
        "tag_b64": "AAAAAAAAAAAAAAAAAAAAAA==",
        "plaintext_size_bytes": backup_audit["file_size_bytes"],
        "plaintext_sha256": backup_audit["file_sha256"],
        "ciphertext_sha256": "c" * 64,
        "max_chunk_bytes": 1_500_000_000,
        "chunks": [
            {
                "name": "different-part.enc",
                "index": 0,
                "size_bytes": 1234,
                "sha256": "d" * 64,
            }
        ],
    }

    with pytest.raises(
        RemoteBackupCertificationError,
        match="REMOTE_BACKUP_ENCRYPTION_INVALID",
    ):
        validate_remote_encryption_evidence(
            encryption=encryption,
            backup_audit=backup_audit,
            remote_assets=_assets(backup_audit),
        )


def test_remote_backup_certificate_rejects_truncated_baseline_audit_cleanly(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-truncated-audit.db"
    staging = tmp_path / "staging-truncated-audit.db"
    restored = tmp_path / "restored-truncated-audit.db"
    _database(source)
    _database(staging)
    _database(restored)
    backup_audit = audit_sqlite_snapshot(staging)
    backup_audit.pop("schema_sha256")
    audit_material = {key: value for key, value in backup_audit.items() if key != "audit_sha256"}
    backup_audit["audit_sha256"] = canonical_sha256(audit_material)

    source_stat = source.stat()
    valid_audit = audit_sqlite_snapshot(staging)
    remote_assets = _assets(valid_audit)
    remote_verification = _trusted(valid_audit)
    encryption = _encryption(valid_audit)
    with pytest.raises(RemoteBackupCertificationError, match="REMOTE_BACKUP_BASELINE_AUDIT_INVALID"):
        build_remote_backup_certification(
            source=source,
            source_identity={"device_id": source_stat.st_dev, "inode": source_stat.st_ino},
            source_failure_domain="production-vda1",
            destination_failure_domain="github-private-release:cavack/wfh-dr",
            backup_audit=backup_audit,
            restored_backup_path=restored,
            remote_assets=remote_assets,
            remote_verification=remote_verification,
            backup_started_at=1_787_956_900,
            backup_completed_at=1_787_956_950,
            encryption=encryption,
        )
