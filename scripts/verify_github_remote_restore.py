#!/usr/bin/env python3
"""Seal authoritative GitHub Actions proof for one encrypted DR restore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WFH_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WFH_REPOSITORY_ROOT))
sys.path.insert(0, str(WFH_REPOSITORY_ROOT / "backend" / "src"))

from scripts.certify_sqlite_backup import _canonical_absolute_path, _write_report_atomic
from waterfallhunter.core.sqlite_backup_certification import BackupCertificationError
from waterfallhunter.core.github_remote_restore_verification import (
    TrustedIndependentRestoreVerificationError,
    resolve_github_independent_restore_verification,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an exact private-DR GitHub Actions restore run."
    )
    parser.add_argument(
        "--backup-certification", required=True, type=_canonical_absolute_path
    )
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-run-id", required=True, type=int)
    parser.add_argument("--report", required=True, type=_canonical_absolute_path)
    args = parser.parse_args()
    if args.report.parent != args.backup_certification.parent:
        parser.error("independent restore report must remain beside backup evidence")
    try:
        backup = json.loads(args.backup_certification.read_text(encoding="utf-8"))
        if (
            not isinstance(backup, dict)
            or backup.get("contract_version") != "sqlite_remote_backup_certification_v1"
            or backup.get("remote_repository") != args.github_repository
            or not isinstance(backup.get("backup_audit"), dict)
        ):
            raise TrustedIndependentRestoreVerificationError(
                "REMOTE_BACKUP_CERTIFICATION_INVALID"
            )
        audit = backup["backup_audit"]
        verification = resolve_github_independent_restore_verification(
            repository=args.github_repository,
            run_id=args.github_run_id,
            release_tag=str(backup.get("remote_tag_name", "")),
            expected_plaintext_sha256=str(audit.get("file_sha256", "")),
            expected_plaintext_size_bytes=int(audit.get("file_size_bytes", 0)),
            expected_user_version=int(audit.get("user_version", -1)),
        )
        _write_report_atomic(
            args.report,
            verification.model_dump(mode="python"),
            allowed_directory=args.backup_certification.parent,
        )
    except (
        TrustedIndependentRestoreVerificationError,
        BackupCertificationError,
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as error:
        print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({
        "ok": True,
        "status": "INDEPENDENT_RESTORE_VERIFIED",
        "github_run_id": verification.run_id,
        "release_tag": verification.release_tag,
        "verification_report_sha256": verification.verification_report_sha256,
        "report": str(args.report),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
