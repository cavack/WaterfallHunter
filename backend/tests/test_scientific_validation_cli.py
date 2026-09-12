from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_strict_scientific_model import (
    _resolve_workspace_path,
    build_report,
    write_report_atomic,
)
from waterfallhunter.core.scientific_validation import (
    DAY_SECONDS,
    scientific_source_manifest_sha256,
)


def test_cli_helpers_emit_fail_closed_hash_bound_report(tmp_path) -> None:
    payload = {
        "source_dataset_manifest_sha256": scientific_source_manifest_sha256(
            source_revision="b" * 40,
            generated_at=100,
            target_horizon_seconds=DAY_SECONDS,
            rows=(),
        ),
        "source_revision": "b" * 40,
        "generated_at": 100,
        "target_horizon_seconds": DAY_SECONDS,
        "rows": [],
    }

    report = build_report(payload)
    destination = tmp_path / "reports" / "strict-validation.json"
    write_report_atomic(destination, report)

    persisted = json.loads(destination.read_text(encoding="utf-8"))
    assert persisted == report
    assert persisted["promotion_decision"] == "DO_NOT_PROMOTE"
    assert persisted["promotion_allowed"] is False
    assert len(persisted["report_sha256"]) == 64
    assert list(destination.parent.glob("*.partial")) == []


def test_cli_rejects_paths_outside_operator_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    assert _resolve_workspace_path(Path("report.json"), label="output").parent == workspace
    with pytest.raises(ValueError, match="must remain inside"):
        _resolve_workspace_path(tmp_path / "outside.json", label="output")
