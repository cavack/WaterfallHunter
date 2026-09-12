"""Evaluate the minimal authoritative recovery gate before Production dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.certify_sqlite_backup import _canonical_absolute_path, _write_report_atomic
from waterfallhunter.core.deployment_certification import evaluate_release_recovery_gate


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise ValueError(f"{label} path must be canonical and absolute")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--production-database", required=True, type=_canonical_absolute_path
    )
    parser.add_argument(
        "--backup-certification", required=True, type=_canonical_absolute_path
    )
    parser.add_argument(
        "--independent-restore-verification",
        required=True,
        type=_canonical_absolute_path,
    )
    parser.add_argument("--migration-rehearsal", type=_canonical_absolute_path)
    parser.add_argument("--report", required=True, type=_canonical_absolute_path)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-run-id", required=True, type=int)
    args = parser.parse_args()

    if args.github_repository != "cavack/WFH-ORG":
        parser.error("EXPECTED_REPOSITORY=cavack/WFH-ORG")
    evidence_paths = [
        args.backup_certification,
        args.independent_restore_verification,
    ]
    if args.migration_rehearsal is not None:
        evidence_paths.append(args.migration_rehearsal)
    if any(path == args.report for path in evidence_paths):
        parser.error("report path must differ from every evidence input")
    if any(path.parent != args.report.parent for path in evidence_paths):
        parser.error("evidence inputs and report must remain in one evidence directory")

    request = {
        "contract_version": "release_recovery_gate_request_v1",
        "source_revision": args.source_revision,
        "expected_production_database_path": str(args.production_database),
        "backup_certification": _load_object(
            args.backup_certification, label="backup certification"
        ),
        "independent_restore_verification": _load_object(
            args.independent_restore_verification,
            label="independent restore verification",
        ),
        "migration_rollback_rehearsal": (
            _load_object(args.migration_rehearsal, label="migration rehearsal")
            if args.migration_rehearsal is not None
            else None
        ),
    }
    if request["backup_certification"].get("remote_repository") != "cavack/WFH-ORG-dr":
        parser.error("EXPECTED_DR_REPOSITORY=cavack/WFH-ORG-dr")
    report = evaluate_release_recovery_gate(
        request,
        github_repository=args.github_repository,
        github_run_id=args.github_run_id,
    )
    _write_report_atomic(
        args.report,
        report,
        allowed_directory=args.report.parent,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "report_sha256": report["report_sha256"],
                "deployment_allowed": False,
                "report": str(args.report),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "READY_FOR_EXPLICIT_DISPATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
