#!/usr/bin/env python3
"""Rehearse canonical migration and rollback using a certified backup clone."""

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
from waterfallhunter.core.migration_rehearsal import (
    MigrationRehearsalError,
    rehearse_migration_and_rollback,
    rehearse_migration_and_rollback_sequential,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-certification", required=True, type=_canonical_absolute_path)
    parser.add_argument("--migration-target", type=_canonical_absolute_path)
    parser.add_argument("--rollback-target", type=_canonical_absolute_path)
    parser.add_argument("--sequential-working-target", type=_canonical_absolute_path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--report", required=True, type=_canonical_absolute_path)
    args = parser.parse_args()
    if args.report.parent != args.backup_certification.parent:
        parser.error("rehearsal report must remain beside the independent backup")
    sequential = args.sequential_working_target is not None
    paired = args.migration_target is not None or args.rollback_target is not None
    if sequential and paired:
        parser.error("sequential target cannot be combined with migration/rollback targets")
    if not sequential and (args.migration_target is None or args.rollback_target is None):
        parser.error("migration-target and rollback-target are required unless sequential mode is used")
    try:
        certification = json.loads(
            args.backup_certification.read_text(encoding="utf-8")
        )
        if not isinstance(certification, dict):
            raise MigrationRehearsalError("BACKUP_CERTIFICATION_INVALID")
        if certification.get("certificate_type") == "waterfallhunter_db_backup_v1":
            claimed = str(certification.get("certificate_sha256", ""))
            material = {key: value for key, value in certification.items() if key != "certificate_sha256"}
            if claimed != canonical_sha256(material):
                raise MigrationRehearsalError("CUTOVER_BACKUP_CERTIFICATION_HASH_MISMATCH")
            if certification.get("status") != "PASS" or certification.get("source_revision") != args.source_revision:
                raise MigrationRehearsalError("CUTOVER_BACKUP_CERTIFICATION_IDENTITY_MISMATCH")
            if certification.get("device_separation_enforced") is not False or certification.get("source_volume_preserved_until_post_cutover") is not True:
                raise MigrationRehearsalError("CUTOVER_BACKUP_SAFETY_CONTRACT_INVALID")
            wrapped = certification.get("sqlite_backup_certification")
            if not isinstance(wrapped, dict):
                raise MigrationRehearsalError("CUTOVER_BACKUP_CORE_CERTIFICATION_MISSING")
            backup_audit = wrapped.get("backup_audit")
            if not isinstance(backup_audit, dict):
                raise MigrationRehearsalError("CUTOVER_BACKUP_CORE_CERTIFICATION_MISMATCH")
            if (
                certification.get("backup_path") != wrapped.get("backup_path")
                or certification.get("sha256") != backup_audit.get("file_sha256")
            ):
                raise MigrationRehearsalError("CUTOVER_BACKUP_CORE_CERTIFICATION_MISMATCH")
            certification = wrapped
        if sequential:
            report = rehearse_migration_and_rollback_sequential(
                backup_certification=certification,
                working_target=args.sequential_working_target,
                source_revision=args.source_revision,
            )
        else:
            report = rehearse_migration_and_rollback(
                backup_certification=certification,
                migration_target=args.migration_target,
                rollback_target=args.rollback_target,
                source_revision=args.source_revision,
            )
        _write_report_atomic(
            args.report,
            report,
            allowed_directory=args.backup_certification.parent,
        )
    except (MigrationRehearsalError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "rehearsal_sha256": report["rehearsal_sha256"],
                "report": str(args.report),
                "production_migration_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
