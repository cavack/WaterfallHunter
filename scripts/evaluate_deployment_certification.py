#!/usr/bin/env python3
"""Evaluate a Phase 7 evidence packet without authorizing deployment."""

from __future__ import annotations

import argparse
import json

from scripts.certify_sqlite_backup import _canonical_absolute_path, _write_report_atomic
from waterfallhunter.core.deployment_certification import (
    evaluate_deployment_certification,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=_canonical_absolute_path)
    parser.add_argument("--report", required=True, type=_canonical_absolute_path)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-run-id", required=True, type=int)
    args = parser.parse_args()
    if args.input == args.report:
        parser.error("input and report paths must be different")
    if args.input.parent != args.report.parent:
        parser.error("input and report must remain in the same evidence directory")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("deployment certification input must be an object")
    report = evaluate_deployment_certification(
        payload,
        github_repository=args.github_repository,
        github_run_id=args.github_run_id,
    )
    _write_report_atomic(
        args.report,
        report,
        allowed_directory=args.input.parent,
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
    return 0 if report["status"] == "READY_FOR_EXPLICIT_OWNER_APPROVAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
