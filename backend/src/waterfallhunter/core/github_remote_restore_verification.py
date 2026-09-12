"""Authoritative GitHub Actions proof that an off-host DR release restores."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.github_release_backup_verification import (
    TrustedRemoteBackupVerificationError,
    _gh_executable,
)
from waterfallhunter.core.signal_metadata import canonical_sha256

SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LEGACY_WORKFLOW_PATH = ".github/workflows/restore.yml"
_CANONICAL_WORKFLOW_PATH = ".github/workflows/dr-restore-v2.yml"
_TRUSTED_WORKFLOWS = {
    "cavack/WFH-ORG-dr": {
        "contract_version": "github_actions_remote_restore_verification_v2",
        "name": "WFH Canonical DR Restore v2",
        "path": _CANONICAL_WORKFLOW_PATH,
        "revision": "97da01991cd5f8f5ad56239d75a3602f15ad05f9",
        "artifact_identity": "run_id",
    },
    "cavack/wfh-dr": {
        "contract_version": "github_actions_remote_restore_verification_v1",
        "name": "Verify or restore encrypted DR backup",
        "path": _LEGACY_WORKFLOW_PATH,
        "revision": "add3f01cf3b9f3e55d735294dae99d5a5792b5c2",
        "artifact_identity": "release_tag",
    },
}


class TrustedIndependentRestoreVerificationError(RuntimeError):
    """Raised when independent GitHub Actions restore proof is incomplete."""


class TrustedIndependentRestoreVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal[
        "github_actions_remote_restore_verification_v1",
        "github_actions_remote_restore_verification_v2",
    ]
    github_host: Literal["github.com"]
    repository: str = Field(min_length=3)
    run_id: int = Field(ge=1, strict=True)
    workflow_path: Literal[
        ".github/workflows/restore.yml",
        ".github/workflows/dr-restore-v2.yml",
    ]
    workflow_revision: str = Field(min_length=40, max_length=40)
    release_tag: str = Field(min_length=1)
    artifact_id: int = Field(ge=1, strict=True)
    artifact_name: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_at_epoch: int = Field(ge=1, strict=True)
    restore_file_sha256: str = Field(pattern=SHA256_PATTERN)
    restore_file_size_bytes: int = Field(ge=1, strict=True)
    user_version: int = Field(ge=0, strict=True)
    verification_report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _sealed(self) -> "TrustedIndependentRestoreVerification":
        if not _REPOSITORY.fullmatch(self.repository):
            raise ValueError("independent restore repository invalid")
        if any(character not in "0123456789abcdef" for character in self.workflow_revision):
            raise ValueError("independent restore workflow revision invalid")
        identity = _TRUSTED_WORKFLOWS.get(self.repository)
        if identity is None:
            raise ValueError("independent restore workflow identity not trusted")
        if (
            self.contract_version != identity["contract_version"]
            or self.workflow_path != identity["path"]
        ):
            raise ValueError("independent restore workflow identity invalid")
        expected_artifact_name = (
            f"restore-verification-{self.run_id}"
            if identity["artifact_identity"] == "run_id"
            else f"restore-verification-{self.release_tag}"
        )
        if self.artifact_name != expected_artifact_name:
            raise ValueError("independent restore artifact identity invalid")
        material = self.model_dump(mode="python")
        expected = material.pop("verification_report_sha256")
        if expected != canonical_sha256(material):
            raise ValueError("independent restore verification hash mismatch")
        return self


def _trusted_independent_restore_workflow(repository: str) -> dict[str, str]:
    identity = _TRUSTED_WORKFLOWS.get(repository)
    if identity is None:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_WORKFLOW_IDENTITY_NOT_TRUSTED"
        )
    return identity


def trusted_independent_restore_workflow_revision(repository: str) -> str:
    """Return the reviewed DR workflow revision trusted for one repository."""
    return _trusted_independent_restore_workflow(repository)["revision"]


def _trusted_gh_executable() -> str:
    try:
        return _gh_executable()
    except TrustedRemoteBackupVerificationError as error:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_GITHUB_CLI_UNTRUSTED"
        ) from error


def _epoch(value: Any) -> int:
    if not isinstance(value, str) or not value:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_COMPLETION_TIME_INVALID"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_COMPLETION_TIME_INVALID"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    result = int(parsed.timestamp())
    if result < 1:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_COMPLETION_TIME_INVALID"
        )
    return result


def _gh_json(endpoint: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["GH_HOST"] = "github.com"
    try:
        completed = subprocess.run(
            [_trusted_gh_executable(), "api", "--hostname", "github.com", endpoint],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_GITHUB_API_FAILED"
        ) from error
    if not isinstance(payload, dict):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_GITHUB_API_INVALID"
        )
    return payload


def _download_report(*, repository: str, run_id: int, artifact_name: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["GH_HOST"] = "github.com"
    with tempfile.TemporaryDirectory(prefix="wfh-independent-restore-") as temporary:
        directory = Path(temporary)
        try:
            subprocess.run(
                [
                    _trusted_gh_executable(),
                    "run",
                    "download",
                    str(run_id),
                    "--repo",
                    repository,
                    "--name",
                    artifact_name,
                    "--dir",
                    str(directory),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
                env=environment,
            )
            report_path = directory / "restore-report.json"
            if report_path.is_symlink() or not report_path.is_file():
                raise TrustedIndependentRestoreVerificationError(
                    "INDEPENDENT_RESTORE_REPORT_MISSING"
                )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except TrustedIndependentRestoreVerificationError:
            raise
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            raise TrustedIndependentRestoreVerificationError(
                "INDEPENDENT_RESTORE_REPORT_DOWNLOAD_FAILED"
            ) from error
    if not isinstance(payload, dict):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_REPORT_INVALID"
        )
    return payload


def resolve_github_independent_restore_verification(
    *,
    repository: str,
    run_id: int,
    release_tag: str,
    expected_plaintext_sha256: str,
    expected_plaintext_size_bytes: int,
    expected_user_version: int,
) -> TrustedIndependentRestoreVerification:
    """Query, download, and seal one exact successful independent restore run."""
    if (
        not _REPOSITORY.fullmatch(repository)
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not isinstance(release_tag, str)
        or not release_tag
        or re.fullmatch(SHA256_PATTERN, expected_plaintext_sha256) is None
        or not isinstance(expected_plaintext_size_bytes, int)
        or isinstance(expected_plaintext_size_bytes, bool)
        or expected_plaintext_size_bytes < 1
        or not isinstance(expected_user_version, int)
        or isinstance(expected_user_version, bool)
        or expected_user_version < 0
    ):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_REQUEST_INVALID"
        )

    trusted_workflow = _trusted_independent_restore_workflow(repository)
    trusted_workflow_revision = trusted_workflow["revision"]
    run = _gh_json(f"repos/{repository}/actions/runs/{run_id}")
    workflow_revision = run.get("head_sha")
    if (
        run.get("id") != run_id
        or run.get("name") != trusted_workflow["name"]
        or run.get("path") != trusted_workflow["path"]
        or run.get("head_branch") != "main"
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != repository
        or not isinstance(workflow_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", workflow_revision) is None
    ):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_RUN_NOT_TRUSTED"
        )
    if workflow_revision != trusted_workflow_revision:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_WORKFLOW_REVISION_NOT_TRUSTED"
        )

    artifact_name = (
        f"restore-verification-{run_id}"
        if trusted_workflow["artifact_identity"] == "run_id"
        else f"restore-verification-{release_tag}"
    )
    artifact_payload = _gh_json(f"repos/{repository}/actions/runs/{run_id}/artifacts")
    artifacts = artifact_payload.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_ARTIFACT_SET_INVALID"
        )
    artifact = artifacts[0]
    digest = artifact.get("digest") if isinstance(artifact, dict) else None
    artifact_id = artifact.get("id") if isinstance(artifact, dict) else None
    if (
        not isinstance(artifact, dict)
        or artifact.get("name") != artifact_name
        or artifact.get("expired") is not False
        or not isinstance(artifact_id, int)
        or isinstance(artifact_id, bool)
        or artifact_id < 1
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or re.fullmatch(SHA256_PATTERN, digest.removeprefix("sha256:")) is None
    ):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_ARTIFACT_INVALID"
        )

    report = _download_report(
        repository=repository,
        run_id=run_id,
        artifact_name=artifact_name,
    )
    if (
        report.get("ok") is not True
        or report.get("status") != "RESTORE_VERIFIED"
        or report.get("integrity_check") != "ok"
        or report.get("foreign_key_violation_count") != 0
        or report.get("file_sha256") != expected_plaintext_sha256
        or report.get("file_size_bytes") != expected_plaintext_size_bytes
        or report.get("user_version") != expected_user_version
    ):
        raise TrustedIndependentRestoreVerificationError(
            "INDEPENDENT_RESTORE_REPORT_MISMATCH"
        )

    body = {
        "contract_version": trusted_workflow["contract_version"],
        "github_host": "github.com",
        "repository": repository,
        "run_id": run_id,
        "workflow_path": trusted_workflow["path"],
        "workflow_revision": workflow_revision,
        "release_tag": release_tag,
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_sha256": digest.removeprefix("sha256:"),
        "completed_at_epoch": _epoch(run.get("updated_at")),
        "restore_file_sha256": report["file_sha256"],
        "restore_file_size_bytes": report["file_size_bytes"],
        "user_version": report["user_version"],
    }
    return TrustedIndependentRestoreVerification.model_validate(
        {**body, "verification_report_sha256": canonical_sha256(body)}
    )
