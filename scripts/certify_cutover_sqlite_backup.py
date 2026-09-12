#!/usr/bin/env python3
"""Create a same-host cutover backup while preserving the original live volume.

This is not an independent disaster-recovery certificate. It exists only for a
controlled host-layout cutover where the source production volume remains
untouched until post-cutover certification succeeds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WFH_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WFH_REPOSITORY_ROOT))
sys.path.insert(0, str(WFH_REPOSITORY_ROOT / "backend" / "src"))

from scripts.certify_sqlite_backup import _canonical_absolute_path, _write_report_atomic
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.core.sqlite_backup_certification import (
    BackupCertificationError,
    create_certified_backup,
)


def _revision(value: str) -> str:
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise argparse.ArgumentTypeError("source revision must be a 40-character lowercase Git SHA")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a certified local cutover backup; source volume must remain preserved until post-cutover certification.")
    parser.add_argument("--source", required=True, type=_canonical_absolute_path)
    parser.add_argument("--backup", required=True, type=_canonical_absolute_path)
    parser.add_argument("--restore-target", required=True, type=_canonical_absolute_path)
    parser.add_argument("--report", required=True, type=_canonical_absolute_path)
    parser.add_argument("--source-revision", required=True, type=_revision)
    args = parser.parse_args()
    if args.report.parent != args.backup.parent or args.restore_target.parent != args.backup.parent:
        parser.error("backup, restore target, and report must share one canonical cutover directory")
    try:
        core = create_certified_backup(
            source=args.source,
            backup=args.backup,
            restore_target=args.restore_target,
            source_failure_domain="legacy-production-volume",
            destination_failure_domain="canonical-host-cutover-backup",
            enforce_distinct_device=False,
        )
        audit = core["backup_audit"]
        body = {
            "certificate_type": "waterfallhunter_db_backup_v1",
            "status": "PASS",
            "source_revision": args.source_revision,
            "backup_path": str(args.backup),
            "sha256": audit["file_sha256"],
            "file_size_bytes": audit["file_size_bytes"],
            "integrity_check": audit["integrity_check"],
            "user_version": audit["user_version"],
            "schema_sha256": audit["schema_sha256"],
            "device_separation_enforced": False,
            "independent_disaster_recovery": False,
            "source_volume_preserved_until_post_cutover": True,
            "restore_target": str(args.restore_target),
            "sqlite_backup_certification": core,
        }
        report = {**body, "certificate_sha256": canonical_sha256(body)}
        _write_report_atomic(args.report, report, allowed_directory=args.backup.parent)
    except (BackupCertificationError, OSError) as error:
        print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({
        "ok": True,
        "status": report["status"],
        "report": str(args.report),
        "sha256": report["sha256"],
        "source_volume_preserved_until_post_cutover": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
