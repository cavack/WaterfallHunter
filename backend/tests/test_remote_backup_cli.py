from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

from scripts import certify_remote_sqlite_backup as cli


def _key_file(path: Path) -> None:
    path.write_text(base64.b64encode(b"k" * 32).decode("ascii") + "\n", encoding="ascii")
    os.chmod(path, 0o600)


def test_key_loader_is_restricted_to_trusted_recovery_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    monkeypatch.setattr(cli, "TRUSTED_KEY_ROOT", trusted)
    inside = trusted / "wfh-dr-aes256.key"
    outside = tmp_path / "outside.key"
    _key_file(inside)
    _key_file(outside)
    original_stat = Path.stat

    def root_owned_stat(path: Path, *, follow_symlinks: bool = True):
        result = original_stat(path, follow_symlinks=follow_symlinks)
        if path == inside:
            values = list(result)
            values[4] = 0
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", root_owned_stat)
    assert cli._load_key(inside) == b"k" * 32
    with pytest.raises(cli.RemoteBackupCLIError, match="REMOTE_BACKUP_KEY_FILE_INVALID"):
        cli._load_key(outside)


def test_cleanup_never_unlinks_outside_staging_directory(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"operator-owned")

    with pytest.raises(cli.RemoteBackupCLIError, match="REMOTE_BACKUP_CLEANUP_PATH_INVALID"):
        cli._safe_unlink_staging_artifact(
            outside,
            staging_dir=staging,
            allowed_names={"restored.db"},
        )

    assert outside.read_bytes() == b"operator-owned"


def test_release_create_timeout_cleans_only_the_owned_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    marker = "wfh-backup-run:owned-timeout"

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/git/matching-refs/tags/wfh-dr-timeout-test"):
            return "[]"
        if arguments[:2] == ("release", "create"):
            raise cli.RemoteBackupCLIError("SIMULATED_CREATE_TIMEOUT")
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/releases/tags/wfh-dr-timeout-test"):
            return json.dumps({
                "id": 123,
                "tag_name": "wfh-dr-timeout-test",
                "draft": True,
                "body": cli._release_notes(marker),
            })
        return ""

    monkeypatch.setattr(cli, "_gh", fake_gh)
    with pytest.raises(cli.RemoteBackupCLIError, match="SIMULATED_CREATE_TIMEOUT"):
        cli._publish_release_assets(
            repository="cavack/WFH-ORG-dr",
            tag_name="wfh-dr-timeout-test",
            upload_paths=[Path("/tmp/part.enc")],
            ownership_marker=marker,
        )

    assert any(
        call[:5] == (
            "api",
            "--method",
            "DELETE",
            "--hostname",
            "github.com",
        )
        and call[-1] == "repos/cavack/WFH-ORG-dr/releases/123"
        for call in calls
    )


def test_owned_draft_lookup_falls_back_when_tag_endpoint_hides_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "wfh-backup-run:hidden-draft"
    calls: list[tuple[str, ...]] = []

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == (
            "api",
            "repos/cavack/WFH-ORG-dr/releases/tags/hidden-draft",
        ):
            raise cli.RemoteBackupCLIError("REMOTE_BACKUP_GITHUB_COMMAND_FAILED:404")
        if arguments[:2] == (
            "api",
            "repos/cavack/WFH-ORG-dr/releases?per_page=100&page=1",
        ):
            return json.dumps([
                {
                    "id": 123,
                    "tag_name": "hidden-draft",
                    "draft": True,
                    "body": cli._release_notes(marker),
                }
            ])
        raise AssertionError(f"unexpected gh call: {arguments!r}")

    monkeypatch.setattr(cli, "_gh", fake_gh)

    assert cli._owned_draft_release_id(
        repository="cavack/WFH-ORG-dr",
        tag_name="hidden-draft",
        ownership_marker=marker,
    ) == 123
    assert any("releases?per_page=100&page=1" in call[-1] for call in calls)


def test_owned_draft_lookup_retries_until_new_draft_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "wfh-backup-run:eventual-draft"
    collection_reads = 0

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        nonlocal collection_reads
        if arguments[:2] == (
            "api",
            "repos/cavack/WFH-ORG-dr/releases/tags/eventual-draft",
        ):
            raise cli.RemoteBackupCLIError("REMOTE_BACKUP_GITHUB_COMMAND_FAILED:404")
        if arguments[:2] == (
            "api",
            "repos/cavack/WFH-ORG-dr/releases?per_page=100&page=1",
        ):
            collection_reads += 1
            if collection_reads == 1:
                return "[]"
            return json.dumps([
                {
                    "id": 777,
                    "tag_name": "eventual-draft",
                    "draft": True,
                    "body": cli._release_notes(marker),
                }
            ])
        raise AssertionError(f"unexpected gh call: {arguments!r}")

    monkeypatch.setattr(cli, "_gh", fake_gh)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    assert cli._owned_draft_release_id(
        repository="cavack/WFH-ORG-dr",
        tag_name="eventual-draft",
        ownership_marker=marker,
    ) == 777
    assert collection_reads == 2


def test_release_create_failure_never_deletes_a_preexisting_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/git/matching-refs/tags/existing-dr"):
            return json.dumps([{"ref": "refs/tags/existing-dr"}])
        if arguments[:2] == ("release", "create"):
            raise cli.RemoteBackupCLIError("REMOTE_BACKUP_GITHUB_COMMAND_FAILED:already_exists")
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/releases/tags/existing-dr"):
            return json.dumps({
                "id": 77,
                "tag_name": "existing-dr",
                "draft": True,
                "body": "wfh-backup-run:some-other-invocation",
            })
        return ""

    monkeypatch.setattr(cli, "_gh", fake_gh)
    with pytest.raises(cli.RemoteBackupCLIError, match="already_exists"):
        cli._publish_release_assets(
            repository="cavack/WFH-ORG-dr",
            tag_name="existing-dr",
            upload_paths=[Path("/tmp/part.enc")],
            ownership_marker="wfh-backup-run:this-invocation",
        )

    assert not any("DELETE" in call for call in calls)


def test_upload_failure_cleans_the_owned_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    marker = "wfh-backup-run:publish-failure"

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/git/matching-refs/tags/publish-failure"):
            return "[]"
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/releases/tags/publish-failure"):
            return json.dumps({
                "id": 456,
                "tag_name": "publish-failure",
                "draft": True,
                "body": cli._release_notes(marker),
            })
        if arguments[:2] == ("release", "upload"):
            raise cli.RemoteBackupCLIError("SIMULATED_UPLOAD_FAILURE")
        return ""

    monkeypatch.setattr(cli, "_gh", fake_gh)
    with pytest.raises(cli.RemoteBackupCLIError, match="SIMULATED_UPLOAD_FAILURE"):
        cli._publish_release_assets(
            repository="cavack/WFH-ORG-dr",
            tag_name="publish-failure",
            upload_paths=[Path("/tmp/part.enc")],
            ownership_marker=marker,
        )

    assert any(
        call[-1] == "repos/cavack/WFH-ORG-dr/releases/456" and "DELETE" in call
        for call in calls
    )


def test_main_reports_published_release_when_remote_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.db"
    staging = tmp_path / "staging"
    restore = staging / "restored.db"
    report = staging / "report.json"
    key = tmp_path / "key"
    staging.mkdir()
    source.touch()
    key.touch()
    monkeypatch.setattr(sys, "argv", [
        "certify_remote_sqlite_backup.py",
        "--source", str(source),
        "--staging-dir", str(staging),
        "--restore-target", str(restore),
        "--report", str(report),
        "--key-file", str(key),
        "--remote-repository", "cavack/WFH-ORG-dr",
        "--release-tag", "published-before-verification",
        "--source-failure-domain", "production-vda1",
        "--destination-failure-domain", "github.com:cavack/WFH-ORG-dr",
    ])
    monkeypatch.setattr(cli, "_assert_canonical_application_origin", lambda: None)
    monkeypatch.setattr(cli, "_validated_layout", lambda *_args: (
        staging / "remote-staging-backup.db",
        staging / "bundle",
        staging / "download",
    ))
    monkeypatch.setattr(cli, "_load_key", lambda _path: b"k" * 32)
    monkeypatch.setattr(cli, "_assert_private_repository", lambda _repository: None)
    monkeypatch.setattr(cli, "_prepare_encrypted_snapshot", lambda **_kwargs: {
        "upload_paths": [staging / "manifest.json"],
        "local_assets": {},
    })
    monkeypatch.setattr(cli, "_publish_release_assets", lambda **_kwargs: 123)
    monkeypatch.setattr(
        cli,
        "_verify_published_remote",
        lambda **_kwargs: (_ for _ in ()).throw(
            cli.RemoteBackupCLIError("SIMULATED_REMOTE_VERIFICATION_FAILURE")
        ),
    )
    monkeypatch.setattr(cli, "_cleanup_staging", lambda **_kwargs: None)

    assert cli.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["published_remote_release_preserved"] is True


def test_gh_commands_pin_github_dot_com_host(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_env: dict[str, str] = {}

    class Result:
        stdout = "{}"

    def fake_run(_arguments, **kwargs):
        observed_env.update(kwargs.get("env") or {})
        return Result()

    monkeypatch.setattr(cli, "_trusted_gh_executable", lambda: "/test-bin/gh")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._gh("api", "user")
    assert observed_env.get("GH_HOST") == "github.com"


def test_gh_failure_includes_bounded_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(arguments, **_kwargs):
        raise cli.subprocess.CalledProcessError(
            1,
            arguments,
            stderr="authentication failed\n" + ("x" * 2000),
        )

    monkeypatch.setattr(cli, "_trusted_gh_executable", lambda: "/test-bin/gh")
    monkeypatch.setattr(cli.subprocess, "run", fail_run)
    with pytest.raises(cli.RemoteBackupCLIError, match="authentication failed") as captured:
        cli._gh("api", "user")
    assert len(str(captured.value)) < 700


def test_publish_failure_preserves_preexisting_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    marker = "wfh-backup-run:preexisting-tag"

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/git/matching-refs/tags/preexisting-tag"):
            return json.dumps([{"ref": "refs/tags/preexisting-tag"}])
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/releases/tags/preexisting-tag"):
            return json.dumps({
                "id": 999,
                "tag_name": "preexisting-tag",
                "draft": True,
                "body": cli._release_notes(marker),
            })
        if arguments[:2] == ("release", "upload"):
            raise cli.RemoteBackupCLIError("SIMULATED_UPLOAD_FAILURE")
        return ""

    monkeypatch.setattr(cli, "_gh", fake_gh)
    with pytest.raises(cli.RemoteBackupCLIError, match="SIMULATED_UPLOAD_FAILURE"):
        cli._publish_release_assets(
            repository="cavack/WFH-ORG-dr",
            tag_name="preexisting-tag",
            upload_paths=[Path("/tmp/part.enc")],
            ownership_marker=marker,
        )

    assert any(call[-1] == "repos/cavack/WFH-ORG-dr/releases/999" for call in calls if "DELETE" in call)
    assert not any(call[-1] == "repos/cavack/WFH-ORG-dr/git/refs/tags/preexisting-tag" for call in calls if "DELETE" in call)


def test_lost_publish_response_is_reported_as_preserved_remote_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.db"
    staging = tmp_path / "staging"
    restore = staging / "restored.db"
    report = staging / "report.json"
    key = tmp_path / "key"
    staging.mkdir()
    source.touch()
    key.touch()
    monkeypatch.setattr(sys, "argv", [
        "certify_remote_sqlite_backup.py",
        "--source", str(source),
        "--staging-dir", str(staging),
        "--restore-target", str(restore),
        "--report", str(report),
        "--key-file", str(key),
        "--remote-repository", "cavack/WFH-ORG-dr",
        "--release-tag", "publish-state-unknown",
        "--source-failure-domain", "production-vda1",
        "--destination-failure-domain", "github.com:cavack/WFH-ORG-dr",
    ])
    monkeypatch.setattr(cli, "_assert_canonical_application_origin", lambda: None)
    monkeypatch.setattr(cli, "_validated_layout", lambda *_args: (
        staging / "remote-staging-backup.db",
        staging / "bundle",
        staging / "download",
    ))
    monkeypatch.setattr(cli, "_load_key", lambda _path: b"k" * 32)
    monkeypatch.setattr(cli, "_assert_private_repository", lambda _repository: None)
    monkeypatch.setattr(cli, "_prepare_encrypted_snapshot", lambda **_kwargs: {
        "upload_paths": [staging / "manifest.json"],
        "local_assets": {},
    })
    monkeypatch.setattr(
        cli,
        "_publish_release_assets",
        lambda **_kwargs: (_ for _ in ()).throw(
            cli.RemoteBackupPublicationStateUncertain("REMOTE_BACKUP_PUBLICATION_STATE_UNCERTAIN")
        ),
    )
    monkeypatch.setattr(cli, "_cleanup_staging", lambda **_kwargs: None)

    assert cli.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["published_remote_release_preserved"] is True



def test_publish_edit_lost_response_becomes_uncertain_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "wfh-backup-run:lost-publish"
    release_reads = 0

    def fake_gh(*arguments: str, timeout: int = 120) -> str:
        nonlocal release_reads
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/git/matching-refs/tags/lost-publish"):
            return "[]"
        if arguments[:2] == ("api", "repos/cavack/WFH-ORG-dr/releases/tags/lost-publish"):
            release_reads += 1
            return json.dumps({
                "id": 321,
                "tag_name": "lost-publish",
                "draft": release_reads == 1,
                "body": cli._release_notes(marker),
            })
        if arguments[:2] == ("release", "edit"):
            raise cli.RemoteBackupCLIError("SIMULATED_LOST_PUBLISH_RESPONSE")
        return ""

    monkeypatch.setattr(cli, "_gh", fake_gh)
    with pytest.raises(
        cli.RemoteBackupCLIError,
        match="REMOTE_BACKUP_PUBLICATION_STATE_UNCERTAIN",
    ):
        cli._publish_release_assets(
            repository="cavack/WFH-ORG-dr",
            tag_name="lost-publish",
            upload_paths=[Path("/tmp/part.enc")],
            ownership_marker=marker,
        )


@pytest.mark.parametrize("origin", [
    "https://github.com/cavack/wfh.git",
    "https://github.com/cavack/sd.git",
    "https://github.com/cavack/WFH-ORG-dr.git",
])
def test_backup_publisher_rejects_wrong_application_origin(monkeypatch, origin):
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *_a, **_k: origin)
    with pytest.raises(cli.RemoteBackupCLIError, match="EXPECTED_REPOSITORY"):
        cli._assert_canonical_application_origin()


@pytest.mark.parametrize("repository", ["cavack/wfh-dr", "cavack/sd"])
def test_backup_publisher_rejects_wrong_dr_before_staging(monkeypatch, repository):
    from types import SimpleNamespace

    class Parser:
        def parse_args(self):
            return SimpleNamespace(remote_repository=repository)

        def error(self, message):
            raise ValueError(message)

    monkeypatch.setattr(cli, "_build_parser", Parser)
    with pytest.raises(ValueError, match="EXPECTED_DR_REPOSITORY"):
        cli.main()
