"""Certification contract for encrypted off-host SQLite disaster-recovery backups."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from waterfallhunter.core.github_release_backup_verification import (
    TrustedRemoteBackupVerification,
)
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.sqlite_backup_certification import (
    BackupCertificationError,
    audit_sqlite_snapshot,
)


class RemoteBackupCertificationError(RuntimeError):
    """Raised when remote backup evidence cannot be sealed safely."""


_COMPARABLE_FIELDS = (
    "user_version",
    "schema_version",
    "logical_content_sha256",
    "object_counts",
    "table_counts",
    "schema_sha256",
)


def _identity(path: Path) -> dict[str, int]:
    stat_result = path.stat()
    return {
        "device_id": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
    }


def _require_source(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RemoteBackupCertificationError("BACKUP_SOURCE_INVALID")
    return path.resolve()


def _require_snapshot(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RemoteBackupCertificationError(f"{label}_SNAPSHOT_INVALID")
    sidecars = [Path(f"{path}{suffix}") for suffix in ("-wal", "-shm")]
    if any(item.exists() for item in sidecars):
        raise RemoteBackupCertificationError(f"{label}_SNAPSHOT_HAS_SIDECARS")
    return path.resolve()


def _validate_assets(remote_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(remote_assets, list) or not remote_assets:
        raise RemoteBackupCertificationError("REMOTE_BACKUP_ASSETS_INVALID")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in remote_assets:
        if not isinstance(item, dict):
            raise RemoteBackupCertificationError("REMOTE_BACKUP_ASSETS_INVALID")
        name = item.get("name")
        asset_id = item.get("id")
        size = item.get("size_bytes")
        sha256 = item.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or name in seen
            or not isinstance(asset_id, int)
            or isinstance(asset_id, bool)
            or asset_id < 1
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise RemoteBackupCertificationError("REMOTE_BACKUP_ASSETS_INVALID")
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "id": asset_id,
                "size_bytes": size,
                "sha256": sha256,
            }
        )
    return sorted(normalized, key=lambda item: item["name"])


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_remote_encryption_evidence(
    *,
    encryption: dict[str, Any],
    backup_audit: dict[str, Any],
    remote_assets: list[dict[str, Any]],
) -> None:
    """Require the encrypted bundle metadata to be bound to the certified SQLite snapshot."""
    if not isinstance(encryption, dict):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")
    manifest_name = encryption.get("manifest_asset_name")
    manifest_sha256 = encryption.get("manifest_sha256")
    plaintext_sha256 = encryption.get("plaintext_sha256")
    ciphertext_sha256 = encryption.get("ciphertext_sha256")
    chunk_count = encryption.get("chunk_count")
    manifest_body = encryption.get("manifest")
    if (
        encryption.get("algorithm") != "AES-256-GCM"
        or encryption.get("compression") != "zlib"
        or not isinstance(manifest_name, str)
        or not manifest_name
        or Path(manifest_name).name != manifest_name
        or not _valid_sha256(manifest_sha256)
        or not _valid_sha256(plaintext_sha256)
        or not _valid_sha256(ciphertext_sha256)
        or not isinstance(chunk_count, int)
        or isinstance(chunk_count, bool)
        or chunk_count < 1
        or plaintext_sha256 != backup_audit.get("file_sha256")
        or not isinstance(manifest_body, dict)
    ):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")

    assets = _validate_assets(remote_assets)
    by_name = {item["name"]: item for item in assets}
    manifest_asset = by_name.get(manifest_name)
    chunk_assets = [item for item in assets if item["name"] != manifest_name]
    expected_manifest_keys = {
        "contract_version",
        "algorithm",
        "compression",
        "nonce_b64",
        "tag_b64",
        "plaintext_size_bytes",
        "plaintext_sha256",
        "ciphertext_sha256",
        "max_chunk_bytes",
        "chunks",
    }
    manifest_chunks = manifest_body.get("chunks")
    try:
        nonce = base64.b64decode(manifest_body.get("nonce_b64", ""), validate=True)
        tag = base64.b64decode(manifest_body.get("tag_b64", ""), validate=True)
        manifest_payload = (
            json.dumps(manifest_body, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise RemoteBackupCertificationError(
            "REMOTE_BACKUP_ENCRYPTION_INVALID"
        ) from None
    if (
        set(manifest_body) != expected_manifest_keys
        or manifest_body.get("contract_version") != "wfh_encrypted_backup_bundle_v1"
        or manifest_body.get("algorithm") != encryption.get("algorithm")
        or manifest_body.get("compression") != encryption.get("compression")
        or len(nonce) != 12
        or len(tag) != 16
        or manifest_body.get("plaintext_size_bytes") != backup_audit.get("file_size_bytes")
        or manifest_body.get("plaintext_sha256") != plaintext_sha256
        or manifest_body.get("ciphertext_sha256") != ciphertext_sha256
        or not isinstance(manifest_body.get("max_chunk_bytes"), int)
        or isinstance(manifest_body.get("max_chunk_bytes"), bool)
        or manifest_body["max_chunk_bytes"] < 256
        or not isinstance(manifest_chunks, list)
        or len(manifest_chunks) != chunk_count
    ):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")

    normalized_manifest_chunks: list[dict[str, Any]] = []
    for index, item in enumerate(manifest_chunks):
        if not isinstance(item, dict):
            raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")
        name = item.get("name")
        size_bytes = item.get("size_bytes")
        sha256 = item.get("sha256")
        if (
            set(item) != {"name", "index", "size_bytes", "sha256"}
            or item.get("index") != index
            or not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".enc")
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 1
            or not _valid_sha256(sha256)
        ):
            raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")
        normalized_manifest_chunks.append(
            {"name": name, "size_bytes": size_bytes, "sha256": sha256}
        )

    normalized_asset_chunks = [
        {
            "name": item["name"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
        }
        for item in chunk_assets
    ]
    if (
        manifest_asset is None
        or manifest_asset.get("size_bytes") != len(manifest_payload)
        or manifest_asset.get("sha256") != manifest_sha256
        or hashlib.sha256(manifest_payload).hexdigest() != manifest_sha256
        or sorted(normalized_manifest_chunks, key=lambda item: item["name"])
        != sorted(normalized_asset_chunks, key=lambda item: item["name"])
    ):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_ENCRYPTION_INVALID")


def _trusted_verification(
    value: TrustedRemoteBackupVerification | dict[str, Any],
) -> TrustedRemoteBackupVerification:
    try:
        if isinstance(value, TrustedRemoteBackupVerification):
            return value
        return TrustedRemoteBackupVerification.model_validate(value)
    except (TypeError, ValueError) as error:
        raise RemoteBackupCertificationError(
            "REMOTE_BACKUP_VERIFICATION_INVALID"
        ) from error


def build_remote_backup_certification(
    *,
    source: Path,
    source_identity: dict[str, int],
    source_failure_domain: str,
    destination_failure_domain: str,
    backup_audit: dict[str, Any],
    restored_backup_path: Path,
    remote_assets: list[dict[str, Any]],
    remote_verification: TrustedRemoteBackupVerification | dict[str, Any],
    backup_started_at: int,
    backup_completed_at: int,
    encryption: dict[str, Any],
) -> dict[str, Any]:
    """Seal one off-host remote backup after independent download and restore."""
    source_path = _require_source(source)
    source_failure_domain = source_failure_domain.strip()
    destination_failure_domain = destination_failure_domain.strip()
    if source_identity != _identity(source_path):
        raise RemoteBackupCertificationError("BACKUP_SOURCE_IDENTITY_CHANGED")
    if (
        not source_failure_domain
        or not destination_failure_domain
        or source_failure_domain == destination_failure_domain
    ):
        raise RemoteBackupCertificationError("FAILURE_DOMAIN_NOT_INDEPENDENT")
    if not isinstance(backup_started_at, int) or not isinstance(backup_completed_at, int):
        raise RemoteBackupCertificationError("BACKUP_TIMESTAMP_INVALID")
    if backup_started_at < 1 or backup_completed_at < backup_started_at:
        raise RemoteBackupCertificationError("BACKUP_TIMESTAMP_INVALID")
    if not isinstance(backup_audit, dict):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_BASELINE_AUDIT_INVALID")
    claimed_audit_hash = backup_audit.get("audit_sha256")
    audit_material = {key: value for key, value in backup_audit.items() if key != "audit_sha256"}
    if (
        backup_audit.get("contract_version") != "sqlite_snapshot_audit_v1"
        or backup_audit.get("integrity_check") != "ok"
        or backup_audit.get("foreign_key_violation_count") != 0
        or any(field not in backup_audit for field in _COMPARABLE_FIELDS)
        or "file_sha256" not in backup_audit
        or not isinstance(claimed_audit_hash, str)
        or claimed_audit_hash != canonical_sha256(audit_material)
    ):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_BASELINE_AUDIT_INVALID")
    restored_path = _require_snapshot(restored_backup_path, label="RESTORED")
    try:
        restore_audit = audit_sqlite_snapshot(restored_path)
    except (BackupCertificationError, OSError) as error:
        raise RemoteBackupCertificationError("REMOTE_BACKUP_SQLITE_AUDIT_FAILED") from error
    mismatches = [
        field for field in _COMPARABLE_FIELDS if backup_audit[field] != restore_audit[field]
    ]
    if mismatches:
        raise RemoteBackupCertificationError(
            "REMOTE_RESTORE_AUDIT_MISMATCH:" + ",".join(mismatches)
        )

    assets = _validate_assets(remote_assets)
    validate_remote_encryption_evidence(
        encryption=encryption,
        backup_audit=backup_audit,
        remote_assets=assets,
    )
    trusted = _trusted_verification(remote_verification)
    if (
        trusted.github_host != "github.com"
        or trusted.private_repository is not True
        or trusted.asset_ids != {item["name"]: item["id"] for item in assets}
        or trusted.asset_sha256 != {item["name"]: item["sha256"] for item in assets}
    ):
        raise RemoteBackupCertificationError("REMOTE_BACKUP_VERIFICATION_MISMATCH")

    body = {
        "contract_version": "sqlite_remote_backup_certification_v1",
        "status": "BACKUP_RESTORE_CERTIFIED",
        "source_path": str(source_path),
        "source_identity": source_identity,
        "source_failure_domain": source_failure_domain,
        "destination_failure_domain": destination_failure_domain,
        "off_host_separation_enforced": True,
        "storage_kind": "github_private_release",
        "remote_repository": trusted.repository,
        "remote_release_id": trusted.release_id,
        "remote_tag_name": trusted.tag_name,
        "remote_assets": assets,
        "remote_verification": trusted.model_dump(mode="python"),
        "backup_started_at": backup_started_at,
        "backup_completed_at": backup_completed_at,

        "backup_audit": backup_audit,
        "restore_audit": restore_audit,
        "restore_matches_backup": True,
        "local_restore_path": str(restored_path),
        "encryption": encryption,
        "rollback_source_sha256": backup_audit["file_sha256"],
        "production_migration_authorized": False,
        "production_deployment_authorized": False,
    }
    return {**body, "certification_sha256": canonical_sha256(body)}
