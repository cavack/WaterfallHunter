from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import waterfallhunter.core.github_release_backup_verification as remote


def _asset(name: str, asset_id: int, digest: str, size: int) -> dict:
    return {
        "id": asset_id,
        "name": name,
        "state": "uploaded",
        "size": size,
        "digest": f"sha256:{digest}",
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    private: bool = True,
    digest: str = "a" * 64,
    immutable: bool = True,
) -> None:
    def fake(endpoint: str) -> dict:
        if endpoint == "repos/cavack/wfh-dr":
            return {"full_name": "cavack/wfh-dr", "private": private, "archived": False}
        if endpoint == "repos/cavack/wfh-dr/immutable-releases":
            return {"enabled": True, "enforced_by_owner": False}
        return {
            "id": 77,
            "tag_name": "wfh-dr-test",
            "draft": False,
            "prerelease": False,
            "immutable": immutable,
            "published_at": "2026-08-28T22:00:00Z",
            "assets": [_asset("part-000.enc", 101, digest, 1234)],
        }
    monkeypatch.setattr(remote, "_gh_json", fake)


def test_private_release_assets_are_authoritatively_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    report = remote.resolve_github_release_backup_verification(
        repository="cavack/wfh-dr",
        release_id=77,
        tag_name="wfh-dr-test",
        expected_assets=[{"name": "part-000.enc", "id": 101, "size_bytes": 1234, "sha256": "a" * 64}],
    )
    assert report.github_host == "github.com"
    assert report.private_repository is True
    assert report.release_id == 77
    assert report.asset_sha256 == {"part-000.enc": "a" * 64}
    assert report.published_at_epoch > 0


def test_remote_backup_verification_rejects_public_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, private=False)
    with pytest.raises(remote.TrustedRemoteBackupVerificationError, match="REMOTE_BACKUP_REPOSITORY_NOT_PRIVATE"):
        remote.resolve_github_release_backup_verification(
            repository="cavack/wfh-dr", release_id=77, tag_name="wfh-dr-test",
            expected_assets=[{"name": "part-000.enc", "id": 101, "size_bytes": 1234, "sha256": "a" * 64}],
        )


def test_remote_backup_verification_rejects_asset_digest_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, digest="b" * 64)
    with pytest.raises(remote.TrustedRemoteBackupVerificationError, match="REMOTE_BACKUP_ASSET_MISMATCH"):
        remote.resolve_github_release_backup_verification(
            repository="cavack/wfh-dr", release_id=77, tag_name="wfh-dr-test",
            expected_assets=[{"name": "part-000.enc", "id": 101, "size_bytes": 1234, "sha256": "a" * 64}],
        )


class _FakePath:
    def __init__(self, value: str, entries: dict[str, tuple[int, int]]) -> None:
        self.value = value
        self.entries = entries

    def stat(self, *_args, **_kwargs):
        uid, mode = self.entries[self.value]
        return SimpleNamespace(st_uid=uid, st_mode=mode)

    def lstat(self):
        return self.stat()

    def is_file(self) -> bool:
        return stat.S_ISREG(self.stat().st_mode)

    def is_symlink(self) -> bool:
        return stat.S_ISLNK(self.lstat().st_mode)

    def is_absolute(self) -> bool:
        return self.value.startswith("/")

    @property
    def parents(self) -> tuple["_FakePath", ...]:
        return tuple(
            _FakePath(str(parent), self.entries)
            for parent in Path(self.value).parents
        )

    def __fspath__(self) -> str:
        return self.value

    def __str__(self) -> str:
        return self.value


def _install_fake_trusted_gh_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    writable_parent: bool = False,
) -> _FakePath:
    entries = {
        "/": (0, stat.S_IFDIR | 0o755),
        "/trusted": (0, stat.S_IFDIR | 0o755),
        "/trusted/bin": (
            0,
            stat.S_IFDIR | (0o777 if writable_parent else 0o755),
        ),
        "/trusted/bin/gh": (0, stat.S_IFREG | 0o755),
    }
    candidate = _FakePath("/trusted/bin/gh", entries)
    monkeypatch.setattr(remote, "_GH_CANDIDATES", (candidate,))
    return candidate


def test_gh_executable_rejects_writable_parent_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_trusted_gh_path(monkeypatch, writable_parent=True)

    with pytest.raises(
        remote.TrustedRemoteBackupVerificationError,
        match="GITHUB_CLI_UNAVAILABLE_OR_UNTRUSTED",
    ):
        remote._gh_executable()


def test_gh_executable_skips_non_executable_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        "/": (0, stat.S_IFDIR | 0o755),
        "/trusted": (0, stat.S_IFDIR | 0o755),
        "/trusted/bin": (0, stat.S_IFDIR | 0o755),
        "/trusted/sbin": (0, stat.S_IFDIR | 0o755),
        "/trusted/bin/gh": (0, stat.S_IFREG | 0o644),
        "/trusted/sbin/gh": (0, stat.S_IFREG | 0o755),
    }
    non_executable = _FakePath("/trusted/bin/gh", entries)
    executable = _FakePath("/trusted/sbin/gh", entries)
    monkeypatch.setattr(remote, "_GH_CANDIDATES", (non_executable, executable))

    def no_acl(*_args, **_kwargs):
        raise OSError(errno.ENODATA, "no data")

    monkeypatch.setattr(os, "getxattr", no_acl)
    assert remote._gh_executable() == "/trusted/sbin/gh"


def test_gh_executable_rejects_parent_with_extended_access_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_trusted_gh_path(monkeypatch)

    def fake_getxattr(path, name, *, follow_symlinks=True):
        assert name == "system.posix_acl_access"
        assert follow_symlinks is False
        if str(path) == "/trusted/bin":
            return b"extended-acl"
        raise OSError(errno.ENODATA, "no data")

    monkeypatch.setattr(os, "getxattr", fake_getxattr)
    with pytest.raises(
        remote.TrustedRemoteBackupVerificationError,
        match="GITHUB_CLI_UNAVAILABLE_OR_UNTRUSTED",
    ):
        remote._gh_executable()


def test_github_api_is_pinned_to_github_dot_com(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    class Result:
        stdout = '{"ok": true}'

    def fake_run(arguments, **_kwargs):
        observed.extend(str(value) for value in arguments)
        return Result()

    monkeypatch.setattr(remote, "_gh_executable", lambda: "/usr/bin/gh")
    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    assert remote._gh_json("repos/cavack/wfh-dr") == {"ok": True}
    assert observed == [
        "/usr/bin/gh",
        "api",
        "--hostname",
        "github.com",
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        "repos/cavack/wfh-dr",
    ]


def test_remote_backup_verification_requires_immutable_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(endpoint: str) -> dict:
        if endpoint == "repos/cavack/wfh-dr":
            return {"full_name": "cavack/wfh-dr", "private": True, "archived": False}
        if endpoint == "repos/cavack/wfh-dr/immutable-releases":
            return {"enabled": False, "enforced_by_owner": False}
        return {
            "id": 77,
            "tag_name": "wfh-dr-test",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-08-28T22:00:00Z",
            "assets": [_asset("part-000.enc", 101, "a" * 64, 1234)],
        }

    monkeypatch.setattr(remote, "_gh_json", fake)
    with pytest.raises(
        remote.TrustedRemoteBackupVerificationError,
        match="REMOTE_BACKUP_IMMUTABLE_RELEASES_REQUIRED",
    ):
        remote.resolve_github_release_backup_verification(
            repository="cavack/wfh-dr",
            release_id=77,
            tag_name="wfh-dr-test",
            expected_assets=[{
                "name": "part-000.enc",
                "id": 101,
                "size_bytes": 1234,
                "sha256": "a" * 64,
            }],
        )


def test_remote_backup_verification_rejects_mutable_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, immutable=False)
    with pytest.raises(
        remote.TrustedRemoteBackupVerificationError,
        match="REMOTE_BACKUP_RELEASE_NOT_IMMUTABLE",
    ):
        remote.resolve_github_release_backup_verification(
            repository="cavack/wfh-dr",
            release_id=77,
            tag_name="wfh-dr-test",
            expected_assets=[{
                "name": "part-000.enc",
                "id": 101,
                "size_bytes": 1234,
                "sha256": "a" * 64,
            }],
        )
