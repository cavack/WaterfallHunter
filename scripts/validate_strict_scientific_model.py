#!/usr/bin/env python3
"""Generate an immutable Phase 6 STRICT scientific-validation report."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from waterfallhunter.core.scientific_validation import (
    ScientificValidationRequest,
    validate_strict_scientific_evidence,
)


def _resolve_workspace_path(candidate: Path, *, label: str) -> Path:
    workspace = Path.cwd().resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"{label} path must remain inside {workspace}")
    return resolved


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    request = ScientificValidationRequest.model_validate(payload)
    return validate_strict_scientific_evidence(request)


def write_report_atomic(destination: Path, report: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".partial",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate STRICT evidence without enabling promotion or trading."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    input_path = _resolve_workspace_path(args.input, label="input")
    output_path = _resolve_workspace_path(args.output, label="output")
    if input_path == output_path:
        parser.error("input and output paths must be different")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scientific validation input must be a JSON object")
    report = build_report(payload)
    write_report_atomic(output_path, report)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "report_sha256": report["report_sha256"],
                "evidence_gate_status": report["evidence_gate_status"],
                "promotion_decision": report["promotion_decision"],
                "promotion_allowed": False,
            },
            sort_keys=True,
        )
    )
    return 0 if report["evidence_gate_status"] == "COMPLETE_FOR_OWNER_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
