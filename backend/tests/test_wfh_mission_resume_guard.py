from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import wfh_mission as mission
from wfh_mission_test_support import observations, valid_state, write_required_bundle


REPO = Path(__file__).resolve().parents[2]


def _state(**overrides: object) -> dict[str, object]:
    return valid_state(
        current_task="M0.4",
        next_action="reconcile interrupted step",
        **overrides,
    )


def _mission_dir(tmp_path: Path, **overrides: object) -> Path:
    root = tmp_path / "WFH-ME-V3-20260902"
    root.mkdir()
    return write_required_bundle(root, state=_state(**overrides))


def test_abrupt_stop_requires_reconciliation_in_fresh_process(tmp_path: Path) -> None:
    root = _mission_dir(tmp_path)
    mission.journal_step_start(
        root,
        task_id="M0.4",
        step_id="S-001",
        action="pytest -q backend/tests",
        expected_state_change="verification evidence only",
        pre_step_sha="d" * 40,
        required_capabilities=["pytest"],
        retry_policy="reconcile_before_retry",
        reconciliation_procedure="check process, git status, and test artifacts",
        started_at="2026-09-02T15:00:00Z",
    )
    mission.create_checkpoint(root, created_at="2026-09-02T15:00:01Z")

    code = (
        "import json; from pathlib import Path; from scripts import wfh_mission as m; "
        f"print(json.dumps(m.resume_guard(Path({str(root)!r}), capabilities={{'pytest':'AVAILABLE'}})))"
    )
    proc = subprocess.run(["python3", "-c", code], cwd=REPO, text=True, capture_output=True)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["interrupted_step"]["step_id"] == "S-001"
    assert result["interrupted_step"]["action"] == "pytest -q backend/tests"
    assert result["retry_allowed"] is False


def test_main_revision_drift_is_named_and_blocks_ready_resume(tmp_path: Path) -> None:
    root = _mission_dir(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:01:00Z")

    result = mission.resume_guard(
        root,
        capabilities={},
        **observations(observed_main_sha="e" * 40),
    )

    assert result["disposition"] == "DRIFT_DETECTED"
    assert result["drift"] == [
        {"scope": "repository_main", "expected": "b" * 40, "observed": "e" * 40}
    ]


def test_production_revision_drift_is_named(tmp_path: Path) -> None:
    root = _mission_dir(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:02:00Z")

    result = mission.resume_guard(
        root,
        capabilities={},
        **observations(observed_production_sha="f" * 40),
    )

    assert result["disposition"] == "DRIFT_DETECTED"
    assert result["drift"] == [
        {"scope": "production_revision", "expected": "c" * 40, "observed": "f" * 40}
    ]


def test_required_unavailable_capability_blocks_without_guessing(tmp_path: Path) -> None:
    root = _mission_dir(tmp_path, required_capabilities=["github_connector"])
    mission.create_checkpoint(root, created_at="2026-09-02T15:03:00Z")

    result = mission.resume_guard(
        root,
        capabilities={"github_connector": "UNAVAILABLE"},
        **observations(),
    )

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "required_capability_unavailable"
    assert result["unavailable_capabilities"] == ["github_connector"]


def test_completed_journal_step_allows_ready_resume(tmp_path: Path) -> None:
    root = _mission_dir(tmp_path)
    mission.journal_step_start(
        root,
        task_id="M0.4",
        step_id="S-002",
        action="git diff --check",
        expected_state_change="none",
        pre_step_sha="d" * 40,
        required_capabilities=["git"],
        retry_policy="safe_after_reconcile",
        reconciliation_procedure="inspect git status",
        started_at="2026-09-02T15:04:00Z",
    )
    mission.journal_step_complete(root, "S-002", completed_at="2026-09-02T15:04:01Z")
    mission.create_checkpoint(root, created_at="2026-09-02T15:04:02Z")

    result = mission.resume_guard(
        root,
        capabilities={"git": "AVAILABLE"},
        **observations(),
    )

    assert result["disposition"] == "RESUME_READY"
