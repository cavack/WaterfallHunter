"""Fail-closed verification for encrypted off-host GitHub release backups."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256

SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GH_CANDIDATES = (Path("/usr/bin/gh"), Path("/usr/local/bin/gh"))
_GITHUB_API_VERSION = "2026-03-10"


class TrustedRemoteBackupVerificationError(RuntimeError):
    """Raised when authoritative remote-backup evidence cannot be proven."""


class TrustedRemoteBackupVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["github_release_backup_verification_v1"] = (
        "github_release_backup_verification_v1"
    )
    github_host: Literal["github.com"]
    repository: str = Field(min_length=3)
    release_id: int = Field(ge=1, strict=True)
    tag_name: str = Field(min_length=1)
    private_repository: Literal[True]
    published_at_epoch: int = Field(ge=1, strict=True)
    asset_ids: dict[str, int]
    asset_sha256: dict[str, str]
    verification_report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _sealed(self) -> "TrustedRemoteBackupVerification":
        if not _REPOSITORY.fullmatch(self.repository):
            raise ValueError("remote backup repository invalid")
        if set(self.asset_ids) != set(self.asset_sha256) or not self.asset_ids:
            raise ValueError("remote backup asset set invalid")
        if any(value < 1 for value in self.asset_ids.values()):
            raise ValueError("remote backup asset id invalid")
        if any(re.fullmatch(SHA256_PATTERN, value) is None for value in self.asset_sha256.values()):
            raise ValueError("remote backup asset digest invalid")
        material = self.model_dump(mode="python")
        expected = material.pop("verification_report_sha256")
        if expected != canonical_sha256(material):
            raise ValueError("remote backup verification hash mismatch")
        return self


def _path_has_extended_access_acl(path: Path) -> bool:
    try:
        os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError as error:
        unsupported_or_absent = {
            errno.ENODATA,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
        }
        enoattr = getattr(errno, "ENOATTR", None)
        if enoattr is not None:
            unsupported_or_absent.add(enoattr)
        if error.errno in unsupported_or_absent:
            return False
        return True
    return True


def _trusted_path_component(path: Path, *, directory: bool) -> bool:
    try:
        stat_result = path.lstat()
    except OSError:
        return False
    if (
        stat_result.st_uid != 0
        or stat_result.st_mode & 0o022 != 0
        or stat.S_ISLNK(stat_result.st_mode)
        or _path_has_extended_access_acl(path)
    ):
        return False
    return (
        stat.S_ISDIR(stat_result.st_mode)
        if directory
        else stat.S_ISREG(stat_result.st_mode)
    )


def _trusted_executable_path(candidate: Path) -> bool:
    if not candidate.is_absolute():
        return False
    if any(
        not _trusted_path_component(parent, directory=True)
        for parent in candidate.parents
    ):
        return False
    if not _trusted_path_component(candidate, directory=False):
        return False
    try:
        return candidate.lstat().st_mode & 0o111 != 0
    except OSError:
        return False


def _gh_executable() -> str:
    for candidate in _GH_CANDIDATES:
        if _trusted_executable_path(candidate):
            return str(candidate)
    raise TrustedRemoteBackupVerificationError("GITHUB_CLI_UNAVAILABLE_OR_UNTRUSTED")


def _gh_json(endpoint: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                _gh_executable(),
                "api",
                "--hostname",
                "github.com",
                "-H",
                f"X-GitHub-Api-Version: {_GITHUB_API_VERSION}",
                endpoint,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_GITHUB_API_FAILED"
        ) from error
    if not isinstance(payload, dict):
        raise TrustedRemoteBackupVerificationError("REMOTE_BACKUP_GITHUB_API_INVALID")
    return payload


def _epoch(value: Any) -> int:
    if not isinstance(value, str) or not value:
        raise TrustedRemoteBackupVerificationError("REMOTE_BACKUP_PUBLISHED_AT_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_PUBLISHED_AT_INVALID"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    result = int(parsed.timestamp())
    if result < 1:
        raise TrustedRemoteBackupVerificationError("REMOTE_BACKUP_PUBLISHED_AT_INVALID")
    return result


def _validate_request_identity(
    *, repository: str, release_id: int, tag_name: str, expected_assets: list[dict[str, Any]]
) -> None:
    if (
        not _REPOSITORY.fullmatch(repository)
        or not isinstance(release_id, int)
        or isinstance(release_id, bool)
        or release_id < 1
        or not isinstance(tag_name, str)
        or not tag_name.strip()
        or not expected_assets
    ):
        raise TrustedRemoteBackupVerificationError("REMOTE_BACKUP_IDENTITY_INVALID")


def _require_private_repository(repository: str) -> None:
    repo = _gh_json(f"repos/{repository}")
    if (
        repo.get("full_name") != repository
        or repo.get("private") is not True
        or repo.get("archived") is True
    ):
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_REPOSITORY_NOT_PRIVATE"
        )


def _require_immutable_releases(repository: str) -> None:
    settings = _gh_json(f"repos/{repository}/immutable-releases")
    if settings.get("enabled") is not True:
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_IMMUTABLE_RELEASES_REQUIRED"
        )


def _require_release(repository: str, release_id: int, tag_name: str) -> dict[str, Any]:
    release = _gh_json(f"repos/{repository}/releases/{release_id}")
    if (
        release.get("id") != release_id
        or release.get("tag_name") != tag_name
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_RELEASE_NOT_TRUSTED"
        )
    if release.get("immutable") is not True:
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_RELEASE_NOT_IMMUTABLE"
        )
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TrustedRemoteBackupVerificationError("REMOTE_BACKUP_ASSETS_INVALID")
    return release


def _normalize_expected_assets(
    expected_assets: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected_by_name: dict[str, dict[str, Any]] = {}
    for item in expected_assets:
        if not isinstance(item, dict):
            raise TrustedRemoteBackupVerificationError(
                "REMOTE_BACKUP_EXPECTED_ASSET_INVALID"
            )
        name = item.get("name")
        asset_id = item.get("id")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        valid = (
            isinstance(name, str)
            and bool(name)
            and isinstance(asset_id, int)
            and not isinstance(asset_id, bool)
            and asset_id >= 1
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size >= 1
            and isinstance(digest, str)
            and re.fullmatch(SHA256_PATTERN, digest) is not None
            and name not in expected_by_name
        )
        if not valid:
            raise TrustedRemoteBackupVerificationError(
                "REMOTE_BACKUP_EXPECTED_ASSET_INVALID"
            )
        expected_by_name[name] = item
    return expected_by_name


def _verified_asset_maps(
    *, release: dict[str, Any], expected_by_name: dict[str, dict[str, Any]]
) -> tuple[dict[str, int], dict[str, str]]:
    assets = release["assets"]
    actual_by_name = {
        str(item.get("name")): item
        for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(actual_by_name) != set(expected_by_name):
        raise TrustedRemoteBackupVerificationError(
            "REMOTE_BACKUP_ASSET_SET_MISMATCH"
        )
    asset_ids: dict[str, int] = {}
    asset_sha256: dict[str, str] = {}
    for name, expected in expected_by_name.items():
        actual = actual_by_name[name]
        if (
            actual.get("id") != expected["id"]
            or actual.get("state") != "uploaded"
            or actual.get("size") != expected["size_bytes"]
            or actual.get("digest") != f"sha256:{expected['sha256']}"
        ):
            raise TrustedRemoteBackupVerificationError(
                "REMOTE_BACKUP_ASSET_MISMATCH"
            )
        asset_ids[name] = int(actual["id"])
        asset_sha256[name] = str(expected["sha256"])
    return asset_ids, asset_sha256


def resolve_github_release_backup_verification(
    *,
    repository: str,
    release_id: int,
    tag_name: str,
    expected_assets: list[dict[str, Any]],
) -> TrustedRemoteBackupVerification:
    """Prove one private github.com release asset set by exact IDs, sizes and digests."""
    _validate_request_identity(
        repository=repository,
        release_id=release_id,
        tag_name=tag_name,
        expected_assets=expected_assets,
    )
    _require_private_repository(repository)
    _require_immutable_releases(repository)
    release = _require_release(repository, release_id, tag_name)
    expected_by_name = _normalize_expected_assets(expected_assets)
    asset_ids, asset_sha256 = _verified_asset_maps(
        release=release, expected_by_name=expected_by_name
    )
    body = {
        "contract_version": "github_release_backup_verification_v1",
        "github_host": "github.com",
        "repository": repository,
        "release_id": release_id,
        "tag_name": tag_name,
        "private_repository": True,
        "published_at_epoch": _epoch(release.get("published_at")),
        "asset_ids": dict(sorted(asset_ids.items())),
        "asset_sha256": dict(sorted(asset_sha256.items())),
    }
    return TrustedRemoteBackupVerification.model_validate(
        {
            **body,
            "verification_report_sha256": canonical_sha256(body),
        }
    )
