"""Fail-closed Phase 7 deployment-certification evidence gate."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

import sqlite3
import subprocess
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from waterfallhunter.core.deployment_provenance import (
    DEPLOYMENT_PROVENANCE_VERIFIED,
    evaluate_deployment_provenance,
)
from waterfallhunter.core.github_ci_verification import (
    TrustedCIVerification,
    TrustedCIVerificationError,
    resolve_github_ci_verification,
    resolve_github_current_main_revision,
)
from waterfallhunter.core.github_release_backup_verification import (
    TrustedRemoteBackupVerification,
    TrustedRemoteBackupVerificationError,
    resolve_github_release_backup_verification,
)
from waterfallhunter.core.github_remote_restore_verification import (
    TrustedIndependentRestoreVerification,
    TrustedIndependentRestoreVerificationError,
    resolve_github_independent_restore_verification,
    trusted_independent_restore_workflow_revision,
)
from waterfallhunter.core.migration_rehearsal import (
    MigrationRehearsalError,
    migration_executable_sha256,
)
from waterfallhunter.core.remote_backup_certification import (
    RemoteBackupCertificationError,
    validate_remote_encryption_evidence,
)
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION
from waterfallhunter.core.sqlite_backup_certification import (
    BackupCertificationError,
    audit_sqlite_snapshot,
)


MINIMUM_SHADOW_SOAK_SECONDS = 86_400
MAXIMUM_SHADOW_ERROR_RATE = 0.001
MINIMUM_SHADOW_REQUEST_COUNT = 1_000
MAXIMUM_READINESS_AGE_SECONDS = 3_600
MAXIMUM_BACKUP_AGE_SECONDS = 604_800
MAXIMUM_BACKUP_START_BIRTHTIME_SKEW_SECONDS = 300
MAXIMUM_REMOTE_BACKUP_PUBLISH_SKEW_SECONDS = 21_600
MAXIMUM_REPORT_VALIDITY_SECONDS = 3_600
SHA256_PATTERN = r"^[0-9a-f]{64}$"
IMAGE_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

_BACKUP_ARTIFACT_COMPARABLE_FIELDS = (
    "audit_sha256",
    "logical_content_sha256",
    "schema_sha256",
    "user_version",
    "table_counts",
    "object_counts",
    "integrity_check",
    "foreign_key_violation_count",
)


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision: str = Field(min_length=40, max_length=40)
    tested_image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    backend_tests_passed: StrictBool
    frontend_tests_passed: StrictBool
    e2e_tests_passed: StrictBool
    migration_tests_passed: StrictBool
    load_tests_passed: StrictBool
    fault_tests_passed: StrictBool
    security_tests_passed: StrictBool
    secret_scan_passed: StrictBool
    blocker_review_findings: int = Field(ge=0, strict=True)
    verification_report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _revision_shape(self) -> "VerificationEvidence":
        if any(character not in "0123456789abcdef" for character in self.source_revision):
            raise ValueError("verification revision must be lowercase hexadecimal")
        return self


class ReadinessEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision: str = Field(min_length=40, max_length=40)
    running_image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    runtime_fingerprint_sha256: str = Field(pattern=SHA256_PATTERN)
    environment: Literal["STAGING_SHADOW"]
    observed_at: int = Field(ge=1, strict=True)
    livez_ok: StrictBool
    healthz_ok: StrictBool
    readyz_ok: StrictBool
    schema_ready: StrictBool
    database_ready: StrictBool
    observed_schema_version: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _revision_shape(self) -> "ReadinessEvidence":
        if any(character not in "0123456789abcdef" for character in self.source_revision):
            raise ValueError("readiness revision must be lowercase hexadecimal")
        return self


class ShadowSoakEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision: str = Field(min_length=40, max_length=40)
    built_image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    runtime_fingerprint_sha256: str = Field(pattern=SHA256_PATTERN)
    environment: Literal["STAGING_SHADOW"]
    started_at: int = Field(ge=0, strict=True)
    ended_at: int = Field(ge=0, strict=True)
    request_count: int = Field(ge=0, strict=True)
    request_error_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    oom_events: int = Field(ge=0, strict=True)
    schema_errors: int = Field(ge=0, strict=True)
    live_order_path_count: int = Field(ge=0, strict=True)
    paper_only: StrictBool

    @model_validator(mode="after")
    def _valid_window(self) -> "ShadowSoakEvidence":
        if self.ended_at <= self.started_at:
            raise ValueError("shadow soak end must follow start")
        if self.paper_only is not True:
            raise ValueError("shadow soak must remain signal-only")
        if any(character not in "0123456789abcdef" for character in self.source_revision):
            raise ValueError("shadow soak revision must be lowercase hexadecimal")
        return self


class DeploymentCertificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["deployment_certification_request_v1"] = (
        "deployment_certification_request_v1"
    )
    source_revision: str = Field(min_length=40, max_length=40)
    ci_revision: str = Field(min_length=40, max_length=40)
    expected_production_database_path: str = Field(min_length=1)
    artifact_provenance: dict[str, Any]
    backup_certification: dict[str, Any]
    independent_restore_verification: dict[str, Any] | None = None
    migration_rollback_rehearsal: dict[str, Any]
    verification: VerificationEvidence
    readiness: ReadinessEvidence
    shadow_soak: ShadowSoakEvidence

    @model_validator(mode="after")
    def _revision_shape(self) -> "DeploymentCertificationRequest":
        for value in (self.source_revision, self.ci_revision):
            if any(character not in "0123456789abcdef" for character in value):
                raise ValueError("revisions must be exact lowercase Git SHA-1 values")
        database_path = Path(self.expected_production_database_path)
        if not database_path.is_absolute() or database_path.resolve(strict=False) != database_path:
            raise ValueError("expected production database path must be canonical and absolute")
        return self


class ReleaseRecoveryGateRequest(BaseModel):
    """Minimal pre-dispatch recovery evidence for a Production release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["release_recovery_gate_request_v1"] = (
        "release_recovery_gate_request_v1"
    )
    source_revision: str = Field(min_length=40, max_length=40)
    expected_production_database_path: str = Field(min_length=1)
    backup_certification: dict[str, Any]
    independent_restore_verification: dict[str, Any] | None = None
    migration_rollback_rehearsal: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _identity_shape(self) -> "ReleaseRecoveryGateRequest":
        if any(
            character not in "0123456789abcdef"
            for character in self.source_revision
        ):
            raise ValueError("source revision must be an exact lowercase Git SHA-1")
        database_path = Path(self.expected_production_database_path)
        if (
            not database_path.is_absolute()
            or database_path.resolve(strict=False) != database_path
        ):
            raise ValueError(
                "expected production database path must be canonical and absolute"
            )
        return self


def _hash_valid(document: dict[str, Any], hash_field: str) -> bool:
    expected = document.get(hash_field)
    material = {key: value for key, value in document.items() if key != hash_field}
    return isinstance(expected, str) and expected == canonical_sha256(material)


def _provenance_reasons(
    packet: DeploymentCertificationRequest,
    provenance: dict[str, Any],
) -> list[str]:
    checks = (
        (packet.source_revision != packet.ci_revision, "CI_REVISION_MISMATCH"),
        (provenance["status"] != DEPLOYMENT_PROVENANCE_VERIFIED, "ARTIFACT_PROVENANCE_INCOMPLETE"),
        (provenance["links"]["git_sha"] != packet.source_revision, "ARTIFACT_REVISION_MISMATCH"),
    )
    return [reason for failed, reason in checks if failed]


def _snapshot_audit_valid(audit: Any) -> bool:
    if not isinstance(audit, dict):
        return False
    required = {
        "contract_version",
        "file_sha256",
        "file_size_bytes",
        "integrity_check",
        "foreign_key_violation_count",
        "user_version",
        "schema_version",
        "object_counts",
        "table_counts",
        "schema_sha256",
        "logical_content_sha256",
        "audit_sha256",
    }
    return (
        required.issubset(audit)
        and audit.get("contract_version") == "sqlite_snapshot_audit_v1"
        and audit.get("integrity_check") == "ok"
        and audit.get("foreign_key_violation_count") == 0
        and isinstance(audit.get("file_size_bytes"), int)
        and not isinstance(audit.get("file_size_bytes"), bool)
        and audit["file_size_bytes"] > 0
        and isinstance(audit.get("user_version"), int)
        and not isinstance(audit.get("user_version"), bool)
        and audit["user_version"] >= 0
        and isinstance(audit.get("object_counts"), dict)
        and isinstance(audit.get("table_counts"), dict)
        and _hash_valid(audit, "audit_sha256")
    )


def _audits_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = (
        "logical_content_sha256",
        "user_version",
        "schema_version",
        "object_counts",
        "table_counts",
        "schema_sha256",
    )
    return all(left.get(field) == right.get(field) for field in fields)


def _complete_remote_backup_contract_valid(
    backup: dict[str, Any],
    *,
    expected_source_path: str,
) -> bool:
    required = {
        "contract_version",
        "status",
        "source_path",
        "source_identity",
        "source_failure_domain",
        "destination_failure_domain",
        "off_host_separation_enforced",
        "storage_kind",
        "remote_repository",
        "remote_release_id",
        "remote_tag_name",
        "remote_assets",
        "remote_verification",
        "backup_started_at",
        "backup_completed_at",
        "backup_audit",
        "restore_audit",
        "restore_matches_backup",
        "local_restore_path",
        "encryption",
        "rollback_source_sha256",
        "production_migration_authorized",
        "production_deployment_authorized",
        "certification_sha256",
    }
    if not required.issubset(backup):
        return False
    source_identity = backup.get("source_identity")
    backup_audit = backup.get("backup_audit")
    restore_audit = backup.get("restore_audit")
    local_restore = Path(str(backup.get("local_restore_path", "")))
    assets = backup.get("remote_assets")
    encryption = backup.get("encryption")
    started_at = backup.get("backup_started_at")
    completed_at = backup.get("backup_completed_at")
    try:
        trusted = TrustedRemoteBackupVerification.model_validate(
            backup.get("remote_verification")
        )
    except (TypeError, ValueError):
        return False
    if not isinstance(assets, list) or not assets:
        return False
    expected_ids: dict[str, int] = {}
    expected_sha: dict[str, str] = {}
    for item in assets:
        if not isinstance(item, dict):
            return False
        name = item.get("name")
        asset_id = item.get("id")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or name in expected_ids
            or not isinstance(asset_id, int)
            or isinstance(asset_id, bool)
            or asset_id < 1
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return False
        expected_ids[name] = asset_id
        expected_sha[name] = digest
    try:
        validate_remote_encryption_evidence(
            encryption=encryption,
            backup_audit=backup_audit if isinstance(backup_audit, dict) else {},
            remote_assets=assets,
        )
        encryption_valid = True
    except RemoteBackupCertificationError:
        encryption_valid = False
    return (
        backup.get("contract_version") == "sqlite_remote_backup_certification_v1"
        and backup.get("status") == "BACKUP_RESTORE_CERTIFIED"
        and backup.get("source_path") == expected_source_path
        and isinstance(source_identity, dict)
        and set(source_identity) == {"device_id", "inode"}
        and all(isinstance(value, int) and value >= 0 for value in source_identity.values())
        and isinstance(started_at, int)
        and isinstance(completed_at, int)
        and started_at >= 1
        and completed_at >= started_at
        and backup.get("source_failure_domain") != backup.get("destination_failure_domain")
        and backup.get("off_host_separation_enforced") is True
        and backup.get("storage_kind") == "github_private_release"
        and local_restore.is_absolute()
        and _snapshot_audit_valid(backup_audit)
        and _snapshot_audit_valid(restore_audit)
        and _audits_match(backup_audit, restore_audit)
        and backup.get("restore_matches_backup") is True
        and backup.get("rollback_source_sha256") == backup_audit.get("file_sha256")
        and isinstance(encryption, dict)
        and encryption_valid
        and trusted.repository == backup.get("remote_repository")
        and trusted.release_id == backup.get("remote_release_id")
        and trusted.tag_name == backup.get("remote_tag_name")
        and trusted.private_repository is True
        and trusted.asset_ids == expected_ids
        and trusted.asset_sha256 == expected_sha
        and backup.get("production_migration_authorized") is False
        and backup.get("production_deployment_authorized") is False
        and _hash_valid(backup, "certification_sha256")
    )


def _complete_backup_contract_valid(
    backup: dict[str, Any],
    *,
    expected_source_path: str,
) -> bool:
    if backup.get("contract_version") == "sqlite_remote_backup_certification_v1":
        return _complete_remote_backup_contract_valid(
            backup, expected_source_path=expected_source_path
        )
    required = {
        "contract_version",
        "status",
        "source_path",
        "source_identity",
        "source_failure_domain",
        "destination_failure_domain",
        "device_separation_enforced",
        "backup_path",
        "restore_target_path",
        "backup_started_at",
        "backup_completed_at",
        "backup_audit",
        "restore_audit",
        "restore_matches_backup",
        "rollback_source_sha256",
        "production_migration_authorized",
        "production_deployment_authorized",
        "certification_sha256",
    }
    backup_audit = backup.get("backup_audit")
    restore_audit = backup.get("restore_audit")
    source_identity = backup.get("source_identity")
    backup_path = Path(str(backup.get("backup_path", "")))
    restore_path = Path(str(backup.get("restore_target_path", "")))
    started_at = backup.get("backup_started_at")
    completed_at = backup.get("backup_completed_at")
    return (
        required.issubset(backup)
        and backup.get("contract_version") == "sqlite_backup_certification_v1"
        and backup.get("status") == "BACKUP_RESTORE_CERTIFIED"
        and backup.get("source_path") == expected_source_path
        and isinstance(source_identity, dict)
        and set(source_identity) == {"device_id", "inode"}
        and all(isinstance(value, int) and value >= 0 for value in source_identity.values())
        and isinstance(started_at, int)
        and isinstance(completed_at, int)
        and started_at >= 1
        and completed_at >= started_at
        and backup.get("source_failure_domain") != backup.get("destination_failure_domain")
        and backup.get("device_separation_enforced") is True
        and backup_path.is_absolute()
        and restore_path.is_absolute()
        and backup_path != restore_path
        and backup_path.parent == restore_path.parent
        and _snapshot_audit_valid(backup_audit)
        and _snapshot_audit_valid(restore_audit)
        and _audits_match(backup_audit, restore_audit)
        and backup.get("restore_matches_backup") is True
        and backup.get("rollback_source_sha256") == backup_audit.get("file_sha256")
        and backup.get("production_migration_authorized") is False
        and backup.get("production_deployment_authorized") is False
        and _hash_valid(backup, "certification_sha256")
    )


def _remote_backup_artifact_revalidation_reasons(
    backup: dict[str, Any],
) -> list[str]:
    restore_path = Path(str(backup.get("local_restore_path", "")))
    expected = backup.get("restore_audit")
    if not isinstance(expected, dict):
        return ["REMOTE_BACKUP_LOCAL_RESTORE_UNREADABLE"]
    try:
        if (
            not restore_path.is_absolute()
            or restore_path.is_symlink()
            or not restore_path.is_file()
        ):
            return ["REMOTE_BACKUP_LOCAL_RESTORE_UNREADABLE"]
        current = audit_sqlite_snapshot(restore_path)
    except (BackupCertificationError, OSError, ValueError, TypeError, sqlite3.Error):
        return ["REMOTE_BACKUP_LOCAL_RESTORE_UNREADABLE"]
    if any(
        current.get(field) != expected.get(field)
        for field in _BACKUP_ARTIFACT_COMPARABLE_FIELDS
    ):
        return ["REMOTE_BACKUP_LOCAL_RESTORE_TAMPERED"]
    try:
        expected_trusted = TrustedRemoteBackupVerification.model_validate(
            backup.get("remote_verification")
        )
        current_trusted = resolve_github_release_backup_verification(
            repository=str(backup.get("remote_repository", "")),
            release_id=int(backup.get("remote_release_id", 0)),
            tag_name=str(backup.get("remote_tag_name", "")),
            expected_assets=backup.get("remote_assets", []),
        )
    except (
        TrustedRemoteBackupVerificationError,
        TypeError,
        ValueError,
    ):
        return ["REMOTE_BACKUP_VERIFICATION_FAILED"]
    if (
        current_trusted.verification_report_sha256
        != expected_trusted.verification_report_sha256
    ):
        return ["REMOTE_BACKUP_REMOTE_IDENTITY_CHANGED"]
    return []


def _backup_artifact_revalidation_reasons(backup: dict[str, Any]) -> list[str]:
    if backup.get("contract_version") == "sqlite_remote_backup_certification_v1":
        return _remote_backup_artifact_revalidation_reasons(backup)
    backup_path = Path(str(backup.get("backup_path", "")))
    expected = backup.get("backup_audit")
    if not isinstance(expected, dict):
        return ["BACKUP_ARTIFACT_UNREADABLE"]
    try:
        if (
            not backup_path.is_absolute()
            or backup_path.is_symlink()
            or not backup_path.is_file()
        ):
            return ["BACKUP_ARTIFACT_UNREADABLE"]
        current = audit_sqlite_snapshot(backup_path)
    except (BackupCertificationError, OSError, ValueError, TypeError, sqlite3.Error):
        return ["BACKUP_ARTIFACT_UNREADABLE"]
    mismatches = [
        field
        for field in _BACKUP_ARTIFACT_COMPARABLE_FIELDS
        if current.get(field) != expected.get(field)
    ]
    if mismatches:
        return ["BACKUP_ARTIFACT_TAMPERED"]
    return []


def _filesystem_birthtime_epoch(path: Path) -> int | None:
    """Read Linux filesystem birth time independently of packet timestamps."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        return None

    executable = next(
        (
            candidate
            for candidate in (Path("/usr/bin/stat"), Path("/bin/stat"))
            if candidate.is_file()
        ),
        None,
    )
    if executable is None:
        return None

    try:
        completed = subprocess.run(
            [
                str(executable),
                "--format=%W",
                "--",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ):
        return None

    raw = completed.stdout.strip()
    if not raw.isdigit():
        return None

    value = int(raw)
    return value if value >= 1 else None


def _remote_backup_freshness_reasons(
    backup: dict[str, Any],
    *,
    now: int,
) -> list[str]:
    started_at = backup.get("backup_started_at")
    completed_at = backup.get("backup_completed_at")
    try:
        trusted = TrustedRemoteBackupVerification.model_validate(
            backup.get("remote_verification")
        )
    except (TypeError, ValueError):
        return ["BACKUP_FRESHNESS_UNPROVEN"]
    if (
        not isinstance(started_at, int)
        or started_at < 1
        or not isinstance(completed_at, int)
        or completed_at < started_at
    ):
        return ["BACKUP_FRESHNESS_UNPROVEN"]
    published_at = trusted.published_at_epoch
    reasons: list[str] = []
    if completed_at > now or published_at > now:
        reasons.append("BACKUP_TIMESTAMP_IN_FUTURE")
    if published_at < completed_at:
        reasons.append("REMOTE_BACKUP_PUBLISHED_BEFORE_COMPLETION")
    elif published_at - completed_at > MAXIMUM_REMOTE_BACKUP_PUBLISH_SKEW_SECONDS:
        reasons.append("REMOTE_BACKUP_PUBLICATION_SKEW_EXCESSIVE")
    if now - completed_at > MAXIMUM_BACKUP_AGE_SECONDS:
        reasons.append("BACKUP_EVIDENCE_STALE")
    if published_at <= now and now - published_at > MAXIMUM_BACKUP_AGE_SECONDS:
        reasons.append("REMOTE_BACKUP_ARTIFACT_STALE")
    return reasons


def _backup_freshness_reasons(
    backup: dict[str, Any],
    *,
    now: int,
) -> list[str]:
    if backup.get("contract_version") == "sqlite_remote_backup_certification_v1":
        return _remote_backup_freshness_reasons(backup, now=now)
    started_at = backup.get("backup_started_at")
    completed_at = backup.get("backup_completed_at")
    backup_path = Path(str(backup.get("backup_path", "")))

    if (
        not isinstance(started_at, int)
        or started_at < 1
        or not isinstance(completed_at, int)
        or completed_at < started_at
    ):
        return ["BACKUP_FRESHNESS_UNPROVEN"]

    birthtime = _filesystem_birthtime_epoch(backup_path)
    if birthtime is None:
        return ["BACKUP_ARTIFACT_BIRTHTIME_UNAVAILABLE"]

    reasons: list[str] = []

    if completed_at > now:
        reasons.append("BACKUP_TIMESTAMP_IN_FUTURE")

    if birthtime > now:
        reasons.append("BACKUP_ARTIFACT_BIRTHTIME_IN_FUTURE")

    if (
        abs(started_at - birthtime)
        > MAXIMUM_BACKUP_START_BIRTHTIME_SKEW_SECONDS
    ):
        reasons.append("BACKUP_TIMESTAMP_ARTIFACT_MISMATCH")

    if completed_at < birthtime:
        reasons.append("BACKUP_COMPLETION_PRECEDES_ARTIFACT_BIRTH")

    # Preserve the claimed-timestamp stale check as an additional diagnostic,
    # but certification freshness is independently bounded by artifact birth.
    if now - completed_at > MAXIMUM_BACKUP_AGE_SECONDS:
        reasons.append("BACKUP_EVIDENCE_STALE")

    if birthtime <= now and now - birthtime > MAXIMUM_BACKUP_AGE_SECONDS:
        reasons.append("BACKUP_ARTIFACT_STALE")

    return reasons

def _backup_reasons(
    backup: dict[str, Any],
    *,
    expected_source_path: str,
    now: int,
) -> list[str]:
    complete = _complete_backup_contract_valid(
        backup,
        expected_source_path=expected_source_path,
    )
    remote = backup.get("contract_version") == "sqlite_remote_backup_certification_v1"
    checks = [
        (not _hash_valid(backup, "certification_sha256"), "BACKUP_CERTIFICATION_HASH_INVALID"),
        (backup.get("status") != "BACKUP_RESTORE_CERTIFIED", "INDEPENDENT_BACKUP_RESTORE_NOT_CERTIFIED"),
        (backup.get("source_failure_domain") == backup.get("destination_failure_domain"), "BACKUP_FAILURE_DOMAIN_NOT_INDEPENDENT"),
        (backup.get("source_path") != expected_source_path, "BACKUP_SOURCE_IDENTITY_MISMATCH"),
        (not complete, "BACKUP_CERTIFICATION_CONTRACT_INVALID"),
    ]
    if remote:
        checks.append(
            (
                backup.get("off_host_separation_enforced") is not True,
                "BACKUP_OFF_HOST_SEPARATION_NOT_ENFORCED",
            )
        )
    else:
        checks.append(
            (
                backup.get("device_separation_enforced") is not True,
                "BACKUP_DEVICE_SEPARATION_NOT_ENFORCED",
            )
        )
    reasons = [reason for failed, reason in checks if failed]
    if not reasons:
        reasons.extend(_backup_freshness_reasons(backup, now=now))
        reasons.extend(_backup_artifact_revalidation_reasons(backup))
    return reasons


def _independent_remote_restore_reasons(
    packet: DeploymentCertificationRequest | ReleaseRecoveryGateRequest,
    backup: dict[str, Any],
) -> list[str]:
    if backup.get("contract_version") != "sqlite_remote_backup_certification_v1":
        return []
    claimed = packet.independent_restore_verification
    if not isinstance(claimed, dict):
        return ["INDEPENDENT_REMOTE_RESTORE_NOT_VERIFIED"]
    try:
        expected = TrustedIndependentRestoreVerification.model_validate(claimed)
    except (TypeError, ValueError):
        return ["INDEPENDENT_REMOTE_RESTORE_EVIDENCE_INVALID"]
    backup_audit = backup.get("backup_audit")
    if not isinstance(backup_audit, dict):
        return ["INDEPENDENT_REMOTE_RESTORE_BACKUP_IDENTITY_INVALID"]
    if (
        expected.repository != backup.get("remote_repository")
        or expected.release_tag != backup.get("remote_tag_name")
        or expected.restore_file_sha256 != backup_audit.get("file_sha256")
        or expected.restore_file_size_bytes != backup_audit.get("file_size_bytes")
        or expected.user_version != backup_audit.get("user_version")
    ):
        return ["INDEPENDENT_REMOTE_RESTORE_BACKUP_IDENTITY_MISMATCH"]
    try:
        trusted_workflow_revision = trusted_independent_restore_workflow_revision(
            expected.repository
        )
    except TrustedIndependentRestoreVerificationError:
        return ["INDEPENDENT_REMOTE_RESTORE_WORKFLOW_REVISION_NOT_TRUSTED"]
    if expected.workflow_revision != trusted_workflow_revision:
        return ["INDEPENDENT_REMOTE_RESTORE_WORKFLOW_REVISION_NOT_TRUSTED"]
    try:
        current = resolve_github_independent_restore_verification(
            repository=expected.repository,
            run_id=expected.run_id,
            release_tag=expected.release_tag,
            expected_plaintext_sha256=str(backup_audit.get("file_sha256", "")),
            expected_plaintext_size_bytes=int(backup_audit.get("file_size_bytes", 0)),
            expected_user_version=int(backup_audit.get("user_version", -1)),
        )
    except (
        TrustedIndependentRestoreVerificationError,
        TypeError,
        ValueError,
    ):
        return ["INDEPENDENT_REMOTE_RESTORE_TRUST_FAILED"]
    if current.verification_report_sha256 != expected.verification_report_sha256:
        return ["INDEPENDENT_REMOTE_RESTORE_IDENTITY_CHANGED"]
    backup_completed_at = backup.get("backup_completed_at")
    if (
        not isinstance(backup_completed_at, int)
        or isinstance(backup_completed_at, bool)
        or backup_completed_at < 1
    ):
        return ["INDEPENDENT_REMOTE_RESTORE_BACKUP_IDENTITY_INVALID"]
    if current.completed_at_epoch < backup_completed_at:
        return ["INDEPENDENT_REMOTE_RESTORE_PREDATES_BACKUP"]
    return []


def _rehearsal_artifact_revalidation_reasons(
    *,
    path: Path,
    expected_audit: Any,
    unreadable_reason: str,
    tampered_reason: str,
) -> list[str]:
    if not isinstance(expected_audit, dict):
        return [unreadable_reason]
    try:
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
        ):
            return [unreadable_reason]
        current_audit = audit_sqlite_snapshot(path)
    except (
        BackupCertificationError,
        OSError,
        ValueError,
        TypeError,
        sqlite3.Error,
    ):
        return [unreadable_reason]

    mismatches = [
        field
        for field in _BACKUP_ARTIFACT_COMPARABLE_FIELDS
        if current_audit.get(field) != expected_audit.get(field)
    ]
    if mismatches:
        return [tampered_reason]
    return []


def _sequential_rehearsal_reasons(
    rehearsal: dict[str, Any],
    *,
    backup: dict[str, Any],
    source_revision: str,
) -> list[str]:
    remote = backup.get("contract_version") == "sqlite_remote_backup_certification_v1"
    backup_audit = backup.get("restore_audit" if remote else "backup_audit", {})
    migration_result = rehearsal.get("migration_result")
    post_migration_audit = rehearsal.get("post_migration_audit")
    rollback_audit = rehearsal.get("rollback_audit")
    working_path = Path(str(rehearsal.get("working_target", "")))
    backup_location = "local_restore_path" if remote else "backup_path"
    backup_parent = Path(str(backup.get(backup_location, ""))).parent
    required = {
        "contract_version",
        "status",
        "source_revision",
        "backup_certification_sha256",
        "baseline_audit_sha256",
        "working_target",
        "migration_result",
        "post_migration_audit",
        "migration_artifact_retained",
        "rollback_audit",
        "rollback_matches_baseline",
        "rollback_artifact_retained",
        "production_migration_authorized",
        "production_deployment_authorized",
        "rehearsal_sha256",
    }
    post_migration_schema_aligned = (
        isinstance(migration_result, dict)
        and isinstance(post_migration_audit, dict)
        and migration_result.get("user_version") == CURRENT_RUNTIME_SCHEMA_VERSION
        and post_migration_audit.get("user_version") == CURRENT_RUNTIME_SCHEMA_VERSION
        and migration_result.get("user_version") == post_migration_audit.get("user_version")
    )
    complete = (
        required.issubset(rehearsal)
        and rehearsal.get("contract_version") == "sqlite_migration_rollback_rehearsal_v2"
        and rehearsal.get("status") == "MIGRATION_AND_ROLLBACK_REHEARSED"
        and isinstance(migration_result, dict)
        and migration_result.get("ok") is True
        and post_migration_schema_aligned
        and _snapshot_audit_valid(post_migration_audit)
        and _snapshot_audit_valid(rollback_audit)
        and _snapshot_audit_valid(backup_audit)
        and _audits_match(backup_audit, rollback_audit)
        and rehearsal.get("baseline_audit_sha256") == backup_audit.get("audit_sha256")
        and rehearsal.get("migration_artifact_retained") is False
        and rehearsal.get("rollback_artifact_retained") is True
        and rehearsal.get("rollback_matches_baseline") is True
        and working_path.is_absolute()
        and working_path.parent == backup_parent
        and rehearsal.get("production_migration_authorized") is False
        and rehearsal.get("production_deployment_authorized") is False
    )
    checks = (
        (not _hash_valid(rehearsal, "rehearsal_sha256"), "MIGRATION_REHEARSAL_HASH_INVALID"),
        (rehearsal.get("status") != "MIGRATION_AND_ROLLBACK_REHEARSED", "MIGRATION_AND_ROLLBACK_NOT_REHEARSED"),
        (rehearsal.get("backup_certification_sha256") != backup.get("certification_sha256"), "REHEARSAL_BACKUP_IDENTITY_MISMATCH"),
        (rehearsal.get("source_revision") != source_revision, "REHEARSAL_REVISION_MISMATCH"),
        (not post_migration_schema_aligned, "POST_MIGRATION_SCHEMA_VERSION_MISMATCH"),
        (not complete, "MIGRATION_REHEARSAL_CONTRACT_INVALID"),
    )
    reasons = [reason for failed, reason in checks if failed]
    if not reasons:
        reasons.extend(
            _rehearsal_artifact_revalidation_reasons(
                path=working_path,
                expected_audit=rollback_audit,
                unreadable_reason="ROLLBACK_REHEARSAL_ARTIFACT_UNREADABLE",
                tampered_reason="ROLLBACK_REHEARSAL_ARTIFACT_TAMPERED",
            )
        )
    return reasons


def _rehearsal_reasons(
    rehearsal: dict[str, Any],
    *,
    backup: dict[str, Any],
    source_revision: str,
) -> list[str]:
    if rehearsal.get("contract_version") == "sqlite_migration_rollback_rehearsal_v2":
        return _sequential_rehearsal_reasons(
            rehearsal, backup=backup, source_revision=source_revision
        )
    remote = backup.get("contract_version") == "sqlite_remote_backup_certification_v1"
    backup_audit = backup.get("restore_audit" if remote else "backup_audit", {})
    rollback_audit = rehearsal.get("rollback_audit")
    migration_result = rehearsal.get("migration_result")
    post_migration_audit = rehearsal.get("post_migration_audit")
    migration_path = Path(str(rehearsal.get("migration_target", "")))
    rollback_path = Path(str(rehearsal.get("rollback_target", "")))
    backup_location = "local_restore_path" if remote else "backup_path"
    backup_parent = Path(str(backup.get(backup_location, ""))).parent
    required = {
        "contract_version",
        "status",
        "source_revision",
        "backup_certification_sha256",
        "baseline_audit_sha256",
        "migration_target",
        "migration_result",
        "post_migration_audit",
        "rollback_target",
        "rollback_audit",
        "rollback_matches_baseline",
        "production_migration_authorized",
        "production_deployment_authorized",
        "rehearsal_sha256",
    }
    post_migration_schema_aligned = (
        isinstance(migration_result, dict)
        and isinstance(post_migration_audit, dict)
        and migration_result.get("user_version") == CURRENT_RUNTIME_SCHEMA_VERSION
        and post_migration_audit.get("user_version") == CURRENT_RUNTIME_SCHEMA_VERSION
        and migration_result.get("user_version") == post_migration_audit.get("user_version")
    )
    complete = (
        required.issubset(rehearsal)
        and rehearsal.get("contract_version") == "sqlite_migration_rollback_rehearsal_v1"
        and isinstance(migration_result, dict)
        and migration_result.get("ok") is True
        and post_migration_schema_aligned
        and _snapshot_audit_valid(post_migration_audit)
        and _snapshot_audit_valid(rollback_audit)
        and _snapshot_audit_valid(backup_audit)
        and _audits_match(backup_audit, rollback_audit)
        and rehearsal.get("baseline_audit_sha256") == backup_audit.get("audit_sha256")
        and rehearsal.get("rollback_matches_baseline") is True
        and migration_path.is_absolute()
        and rollback_path.is_absolute()
        and migration_path != rollback_path
        and migration_path.parent == backup_parent
        and rollback_path.parent == backup_parent
        and rehearsal.get("production_migration_authorized") is False
        and rehearsal.get("production_deployment_authorized") is False
    )
    checks = (
        (not _hash_valid(rehearsal, "rehearsal_sha256"), "MIGRATION_REHEARSAL_HASH_INVALID"),
        (rehearsal.get("status") != "MIGRATION_AND_ROLLBACK_REHEARSED", "MIGRATION_AND_ROLLBACK_NOT_REHEARSED"),
        (rehearsal.get("backup_certification_sha256") != backup.get("certification_sha256"), "REHEARSAL_BACKUP_IDENTITY_MISMATCH"),
        (rehearsal.get("source_revision") != source_revision, "REHEARSAL_REVISION_MISMATCH"),
        (
            isinstance(migration_result, dict)
            and isinstance(post_migration_audit, dict)
            and not post_migration_schema_aligned,
            "POST_MIGRATION_SCHEMA_VERSION_MISMATCH",
        ),
        (not complete, "MIGRATION_REHEARSAL_CONTRACT_INVALID"),
    )
    reasons = [reason for failed, reason in checks if failed]
    if not reasons:
        reasons.extend(
            _rehearsal_artifact_revalidation_reasons(
                path=migration_path,
                expected_audit=post_migration_audit,
                unreadable_reason="MIGRATION_REHEARSAL_ARTIFACT_UNREADABLE",
                tampered_reason="MIGRATION_REHEARSAL_ARTIFACT_TAMPERED",
            )
        )
        reasons.extend(
            _rehearsal_artifact_revalidation_reasons(
                path=rollback_path,
                expected_audit=rollback_audit,
                unreadable_reason="ROLLBACK_REHEARSAL_ARTIFACT_UNREADABLE",
                tampered_reason="ROLLBACK_REHEARSAL_ARTIFACT_TAMPERED",
            )
        )
    return reasons


def _verification_reasons(
    evidence: VerificationEvidence,
    *,
    source_revision: str,
    ci_revision: str,
    tested_image_digest: Any,
    trusted_ci: TrustedCIVerification | None,
    trusted_ci_failure: str | None,
) -> list[str]:
    fields = (
        "backend_tests_passed",
        "frontend_tests_passed",
        "e2e_tests_passed",
        "migration_tests_passed",
        "load_tests_passed",
        "fault_tests_passed",
        "security_tests_passed",
        "secret_scan_passed",
    )
    reasons = [
        field.upper().replace("_PASSED", "_FAILED")
        for field in fields
        if getattr(evidence, field) is not True
    ]
    if evidence.blocker_review_findings != 0:
        reasons.append("BLOCKER_REVIEW_FINDINGS_REMAIN")
    if evidence.source_revision != source_revision or evidence.source_revision != ci_revision:
        reasons.append("VERIFICATION_REVISION_MISMATCH")
    if evidence.tested_image_digest != tested_image_digest:
        reasons.append("VERIFICATION_IMAGE_MISMATCH")

    if trusted_ci is None:
        reasons.append(trusted_ci_failure or "CI_VERIFICATION_TRUST_UNAVAILABLE")
        return reasons
    if trusted_ci.source_revision != source_revision or trusted_ci.source_revision != ci_revision:
        reasons.append("CI_TRUSTED_REVISION_MISMATCH")
    if trusted_ci.tested_image_digest != tested_image_digest:
        reasons.append("CI_TRUSTED_IMAGE_MISMATCH")
    if evidence.verification_report_sha256 != trusted_ci.verification_report_sha256:
        reasons.append("CI_VERIFICATION_REPORT_MISMATCH")
    return reasons


def _runtime_reasons(
    readiness: ReadinessEvidence,
    soak: ShadowSoakEvidence,
    *,
    source_revision: str,
    built_image_digest: Any,
    running_image_digest: Any,
    runtime_fingerprint_sha256: Any,
    now: int,
) -> list[str]:
    duration_seconds = soak.ended_at - soak.started_at
    readiness_age = now - readiness.observed_at
    checks = (
        (
            not all(
                (
                    readiness.livez_ok,
                    readiness.healthz_ok,
                    readiness.readyz_ok,
                    readiness.schema_ready,
                    readiness.database_ready,
                )
            ),
            "RUNTIME_READINESS_INCOMPLETE",
        ),
        (readiness.observed_schema_version != CURRENT_RUNTIME_SCHEMA_VERSION, "RUNTIME_SCHEMA_VERSION_MISMATCH"),
        (readiness.source_revision != source_revision, "READINESS_REVISION_MISMATCH"),
        (readiness.running_image_digest != running_image_digest, "READINESS_IMAGE_MISMATCH"),
        (
            readiness.runtime_fingerprint_sha256 != runtime_fingerprint_sha256,
            "READINESS_RUNTIME_FINGERPRINT_MISMATCH",
        ),
        (readiness.environment != soak.environment, "READINESS_ENVIRONMENT_MISMATCH"),
        (readiness.observed_at > now, "READINESS_OBSERVED_AT_INVALID"),
        (readiness_age > MAXIMUM_READINESS_AGE_SECONDS, "READINESS_EVIDENCE_STALE"),
        (soak.source_revision != source_revision, "SHADOW_SOAK_REVISION_MISMATCH"),
        (soak.built_image_digest != built_image_digest, "SHADOW_SOAK_IMAGE_MISMATCH"),
        (
            soak.runtime_fingerprint_sha256 != runtime_fingerprint_sha256,
            "SHADOW_SOAK_RUNTIME_FINGERPRINT_MISMATCH",
        ),
        (soak.ended_at > now, "SHADOW_SOAK_ENDED_IN_FUTURE"),
        (duration_seconds < MINIMUM_SHADOW_SOAK_SECONDS, "SHADOW_SOAK_DURATION_INSUFFICIENT"),
        (
            soak.request_count < MINIMUM_SHADOW_REQUEST_COUNT,
            "SHADOW_SOAK_TRAFFIC_INSUFFICIENT",
        ),
        (soak.request_error_rate > MAXIMUM_SHADOW_ERROR_RATE, "SHADOW_SOAK_ERROR_RATE_EXCEEDED"),
        (soak.oom_events != 0, "SHADOW_SOAK_OOM_EVENTS_PRESENT"),
        (soak.schema_errors != 0, "SHADOW_SOAK_SCHEMA_ERRORS_PRESENT"),
        (soak.live_order_path_count != 0, "LIVE_ORDER_PATH_PRESENT"),
    )
    return [reason for failed, reason in checks if failed]


def evaluate_deployment_certification(
    request: DeploymentCertificationRequest | dict[str, Any],
    *,
    now: int | None = None,
    github_repository: str | None = None,
    github_run_id: int | None = None,
) -> dict[str, Any]:
    packet = (
        request
        if isinstance(request, DeploymentCertificationRequest)
        else DeploymentCertificationRequest.model_validate(request)
    )
    trusted_ci: TrustedCIVerification | None = None
    trusted_ci_failure: str | None = None
    if github_repository is None and github_run_id is None:
        trusted_ci_failure = "CI_VERIFICATION_TRUST_UNAVAILABLE"
    elif github_repository is None or github_run_id is None:
        trusted_ci_failure = "CI_VERIFICATION_TRUST_CONFIGURATION_INVALID"
    else:
        try:
            trusted_ci = resolve_github_ci_verification(
                repository=github_repository,
                run_id=github_run_id,
                expected_revision=packet.source_revision,
            )
        except TrustedCIVerificationError as error:
            trusted_ci_failure = str(error) or "CI_VERIFICATION_TRUST_FAILED"
    observed_now = int(time.time() if now is None else now)
    if observed_now < 1:
        raise ValueError("certification evaluation time must be a positive UTC epoch")
    provenance = evaluate_deployment_provenance(packet.artifact_provenance)
    backup = packet.backup_certification
    rehearsal = packet.migration_rollback_rehearsal
    independent_restore: TrustedIndependentRestoreVerification | None = None
    if isinstance(packet.independent_restore_verification, dict):
        try:
            independent_restore = TrustedIndependentRestoreVerification.model_validate(
                packet.independent_restore_verification
            )
        except (TypeError, ValueError):
            independent_restore = None
    links = provenance["links"]
    reasons = [
        *_provenance_reasons(packet, provenance),
        *_backup_reasons(
            backup,
            expected_source_path=packet.expected_production_database_path,
            now=observed_now,
        ),
        *_independent_remote_restore_reasons(packet, backup),
        *_rehearsal_reasons(
            rehearsal,
            backup=backup,
            source_revision=packet.source_revision,
        ),
        *_verification_reasons(
            packet.verification,
            source_revision=packet.source_revision,
            ci_revision=packet.ci_revision,
            tested_image_digest=links["tested_image_digest"],
            trusted_ci=trusted_ci,
            trusted_ci_failure=trusted_ci_failure,
        ),
        *_runtime_reasons(
            packet.readiness,
            packet.shadow_soak,
            source_revision=packet.source_revision,
            built_image_digest=links["built_image_digest"],
            running_image_digest=links["running_image_digest"],
            runtime_fingerprint_sha256=links["runtime_fingerprint_sha256"],
            now=observed_now,
        ),
    ]

    validity_deadlines = [
        observed_now + MAXIMUM_REPORT_VALIDITY_SECONDS,
        packet.readiness.observed_at + MAXIMUM_READINESS_AGE_SECONDS,
    ]
    backup_completed_at = backup.get("backup_completed_at")
    if isinstance(backup_completed_at, int) and backup_completed_at >= 1:
        validity_deadlines.append(
            backup_completed_at + MAXIMUM_BACKUP_AGE_SECONDS
        )
    valid_until = min(validity_deadlines)

    body = {
        "contract_version": "deployment_certification_report_v1",
        "evaluated_at": observed_now,
        "valid_until": valid_until,
        "source_revision": packet.source_revision,
        "status": (
            "READY_FOR_EXPLICIT_OWNER_APPROVAL"
            if not reasons
            else "NOT_READY"
        ),
        "blocking_reasons": sorted(set(reasons)),
        "artifact_provenance_sha256": provenance["provenance_sha256"],
        "backup_certification_sha256": backup.get("certification_sha256"),
        "migration_rehearsal_sha256": (
            rehearsal.get("rehearsal_sha256")
            if isinstance(rehearsal, dict)
            else None
        ),
        "trusted_ci_verification_sha256": (
            trusted_ci.verification_report_sha256 if trusted_ci is not None else None
        ),
        "trusted_ci_run_id": trusted_ci.run_id if trusted_ci is not None else None,
        "independent_restore_verification_sha256": (
            independent_restore.verification_report_sha256
            if independent_restore is not None
            else None
        ),
        "independent_restore_run_id": (
            independent_restore.run_id if independent_restore is not None else None
        ),
        "independent_restore_artifact_id": (
            independent_restore.artifact_id if independent_restore is not None else None
        ),
        "independent_restore_workflow_revision": (
            independent_restore.workflow_revision
            if independent_restore is not None
            else None
        ),
        "deployment_allowed": False,
        "migration_allowed": False,
        "telegram_send_allowed": False,
        "feature_promotion_allowed": False,
        "live_trading_allowed": False,
        "required_next_authority": "EXPLICIT_OWNER_APPROVALS",
    }
    return {**body, "report_sha256": canonical_sha256(body)}



def _resolve_release_gate_ci(
    *,
    packet: ReleaseRecoveryGateRequest,
    github_repository: str | None,
    github_run_id: int | None,
) -> tuple[TrustedCIVerification | None, str | None]:
    """Resolve exact CI evidence for the recovery gate without trusting packet claims."""
    if github_repository is None and github_run_id is None:
        return None, "CI_VERIFICATION_TRUST_UNAVAILABLE"
    if github_repository is None or github_run_id is None:
        return None, "CI_VERIFICATION_TRUST_CONFIGURATION_INVALID"
    try:
        trusted = resolve_github_ci_verification(
            repository=github_repository,
            run_id=github_run_id,
            expected_revision=packet.source_revision,
        )
        current_main = resolve_github_current_main_revision(github_repository)
        if current_main != packet.source_revision:
            return None, "CURRENT_MAIN_REVISION_MISMATCH"
        return trusted, None
    except TrustedCIVerificationError as error:
        return None, str(error) or "CI_VERIFICATION_TRUST_FAILED"


def _release_gate_independent_restore(
    packet: ReleaseRecoveryGateRequest,
) -> TrustedIndependentRestoreVerification | None:
    """Parse independent restore identity for report binding; trust is rechecked separately."""
    claimed = packet.independent_restore_verification
    if not isinstance(claimed, dict):
        return None
    try:
        return TrustedIndependentRestoreVerification.model_validate(claimed)
    except (TypeError, ValueError):
        return None


def _release_gate_schema_change_required(backup: dict[str, Any]) -> bool | None:
    """Derive migration need from the certified Production snapshot schema."""
    backup_audit = backup.get("backup_audit")
    if not isinstance(backup_audit, dict):
        return None
    user_version = backup_audit.get("user_version")
    if not isinstance(user_version, int) or isinstance(user_version, bool):
        return None
    return user_version != CURRENT_RUNTIME_SCHEMA_VERSION



def _release_gate_live_database_identity_reasons(
    packet: ReleaseRecoveryGateRequest,
) -> list[str]:
    """Rebind the certificate to the exact live SQLite file at dispatch time."""
    path = Path(packet.expected_production_database_path)
    identity = packet.backup_certification.get("source_identity")
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        return ["PRODUCTION_DATABASE_IDENTITY_UNAVAILABLE"]
    if (
        not isinstance(identity, dict)
        or set(identity) != {"device_id", "inode"}
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in identity.values()
        )
    ):
        return ["BACKUP_SOURCE_IDENTITY_INVALID"]
    try:
        current = path.stat()
    except OSError:
        return ["PRODUCTION_DATABASE_IDENTITY_UNAVAILABLE"]
    if (
        current.st_dev != identity["device_id"]
        or current.st_ino != identity["inode"]
    ):
        return ["BACKUP_SOURCE_IDENTITY_CHANGED"]
    return []


def _release_gate_migration_executable_reasons(
    rehearsal: dict[str, Any],
) -> list[str]:
    """Reject rehearsal output generated by stale migration code."""
    claimed = rehearsal.get("migration_executable_sha256")
    if (
        not isinstance(claimed, str)
        or len(claimed) != 64
        or any(character not in "0123456789abcdef" for character in claimed)
    ):
        return ["MIGRATION_EXECUTABLE_IDENTITY_MISSING"]
    try:
        current = migration_executable_sha256()
    except (MigrationRehearsalError, OSError, ValueError):
        return ["MIGRATION_EXECUTABLE_IDENTITY_UNAVAILABLE"]
    return [] if claimed == current else ["MIGRATION_EXECUTABLE_IDENTITY_MISMATCH"]

def _release_gate_reasons(
    *,
    packet: ReleaseRecoveryGateRequest,
    observed_now: int,
    trusted_ci: TrustedCIVerification | None,
    ci_failure: str | None,
) -> list[str]:
    """Collect fail-closed reasons from authoritative recovery evidence."""
    backup = packet.backup_certification
    rehearsal = packet.migration_rollback_rehearsal
    reasons: list[str] = []
    if backup.get("contract_version") != "sqlite_remote_backup_certification_v1":
        reasons.append("REMOTE_OFF_HOST_BACKUP_REQUIRED")
    reasons.extend(
        _backup_reasons(
            backup,
            expected_source_path=packet.expected_production_database_path,
            now=observed_now,
        )
    )
    reasons.extend(_independent_remote_restore_reasons(packet, backup))
    reasons.extend(_release_gate_live_database_identity_reasons(packet))
    schema_change_required = _release_gate_schema_change_required(backup)
    if schema_change_required is None:
        reasons.append("BACKUP_SCHEMA_VERSION_INVALID")
    elif schema_change_required is True:
        if not isinstance(rehearsal, dict):
            reasons.append("MIGRATION_REHEARSAL_REQUIRED")
        else:
            reasons.extend(
                _rehearsal_reasons(
                    rehearsal,
                    backup=backup,
                    source_revision=packet.source_revision,
                )
            )
            reasons.extend(_release_gate_migration_executable_reasons(rehearsal))
    if trusted_ci is None:
        reasons.append(ci_failure or "CI_VERIFICATION_TRUST_FAILED")
    elif trusted_ci.source_revision != packet.source_revision:
        reasons.append("CI_TRUSTED_REVISION_MISMATCH")
    return reasons


def _release_gate_valid_until(*, backup: dict[str, Any], observed_now: int) -> int:
    """Bound a ready report by both report lifetime and backup freshness."""
    deadlines = [observed_now + MAXIMUM_REPORT_VALIDITY_SECONDS]
    backup_completed_at = backup.get("backup_completed_at")
    if isinstance(backup_completed_at, int) and backup_completed_at >= 1:
        deadlines.append(backup_completed_at + MAXIMUM_BACKUP_AGE_SECONDS)
    return min(deadlines)


def evaluate_release_recovery_gate(
    request: ReleaseRecoveryGateRequest | dict[str, Any],
    *,
    now: int | None = None,
    github_repository: str | None = None,
    github_run_id: int | None = None,
) -> dict[str, Any]:
    """Evaluate the minimal authoritative recovery gate before explicit dispatch."""
    packet = (
        request
        if isinstance(request, ReleaseRecoveryGateRequest)
        else ReleaseRecoveryGateRequest.model_validate(request)
    )
    observed_now = int(time.time() if now is None else now)
    if observed_now < 1:
        raise ValueError("recovery gate evaluation time must be a positive UTC epoch")

    trusted_ci, ci_failure = _resolve_release_gate_ci(
        packet=packet,
        github_repository=github_repository,
        github_run_id=github_run_id,
    )
    independent_restore = _release_gate_independent_restore(packet)
    reasons = _release_gate_reasons(
        packet=packet,
        observed_now=observed_now,
        trusted_ci=trusted_ci,
        ci_failure=ci_failure,
    )
    backup = packet.backup_certification
    rehearsal = packet.migration_rollback_rehearsal

    body = {
        "contract_version": "release_recovery_gate_report_v1",
        "evaluated_at": observed_now,
        "valid_until": _release_gate_valid_until(
            backup=backup,
            observed_now=observed_now,
        ),
        "source_revision": packet.source_revision,
        "status": "READY_FOR_EXPLICIT_DISPATCH" if not reasons else "NOT_READY",
        "blocking_reasons": sorted(set(reasons)),
        "trusted_ci_verification_sha256": (
            trusted_ci.verification_report_sha256 if trusted_ci is not None else None
        ),
        "trusted_ci_run_id": trusted_ci.run_id if trusted_ci is not None else None,
        "tested_backend_image_digest": (
            trusted_ci.tested_image_digest if trusted_ci is not None else None
        ),
        "backup_certification_sha256": backup.get("certification_sha256"),
        "migration_rehearsal_sha256": (
            rehearsal.get("rehearsal_sha256")
            if isinstance(rehearsal, dict)
            else None
        ),
        "independent_restore_verification_sha256": (
            independent_restore.verification_report_sha256
            if independent_restore is not None
            else None
        ),
        "independent_restore_run_id": (
            independent_restore.run_id if independent_restore is not None else None
        ),
        "independent_restore_artifact_id": (
            independent_restore.artifact_id if independent_restore is not None else None
        ),
        "independent_restore_workflow_revision": (
            independent_restore.workflow_revision
            if independent_restore is not None
            else None
        ),
        "deployment_allowed": False,
        "migration_allowed": False,
        "telegram_send_allowed": False,
        "feature_promotion_allowed": False,
        "live_trading_allowed": False,
        "required_next_authority": "EXPLICIT_WORKFLOW_DISPATCH",
    }
    return {**body, "report_sha256": canonical_sha256(body)}
