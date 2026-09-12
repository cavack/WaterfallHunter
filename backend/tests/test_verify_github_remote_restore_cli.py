from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import verify_github_remote_restore as cli
from waterfallhunter.core.sqlite_backup_certification import BackupCertificationError


def test_report_write_failure_returns_structured_exit_two(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup = tmp_path / "backup-certification.json"
    report = tmp_path / "restore-verification.json"
    backup.write_text(json.dumps({
        "contract_version": "sqlite_remote_backup_certification_v1",
        "remote_repository": "cavack/wfh-dr",
        "remote_tag_name": "wfh-dr-test",
        "backup_audit": {
            "file_sha256": "a" * 64,
            "file_size_bytes": 4096,
            "user_version": 5,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "verify_github_remote_restore.py",
        "--backup-certification", str(backup),
        "--github-repository", "cavack/wfh-dr",
        "--github-run-id", "123",
        "--report", str(report),
    ])
    monkeypatch.setattr(
        cli,
        "resolve_github_independent_restore_verification",
        lambda **_kwargs: SimpleNamespace(
            run_id=123,
            release_tag="wfh-dr-test",
            verification_report_sha256="b" * 64,
            model_dump=lambda mode="python": {"ok": True},
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_report_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BackupCertificationError("REPORT_TARGET_INVALID")
        ),
    )

    assert cli.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "reason": "REPORT_TARGET_INVALID"}
