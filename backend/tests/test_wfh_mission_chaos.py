from __future__ import annotations

import json
from pathlib import Path

from scripts import wfh_mission as mission
from wfh_mission_test_support import observations as valid_observations, valid_state, write_required_bundle


def _state(**overrides: object) -> dict[str, object]:
    return valid_state(
        current_phase="PHASE_-1_MISSION_CONTINUITY",
        current_task="M-1.9_CHAOS_RECOVERY_CERTIFICATION",
        next_action="continue chaos certification",
        **overrides,
    )


def _root(tmp_path: Path, **overrides: object) -> Path:
    root = tmp_path / "WFH-ME-V3-20260902"
    root.mkdir()
    return write_required_bundle(root, state=_state(**overrides))


def _observations(**overrides: object) -> dict[str, object]:
    return valid_observations(**overrides)


def test_latest_pointer_cannot_regress_to_older_valid_checkpoint(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")
    mission.create_checkpoint(root, created_at="2026-09-02T15:31:00Z")
    mission.atomic_write_json(root / "LATEST_CHECKPOINT.json", first, allowed_root=root)

    result = mission.load_latest_checkpoint(root)

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "checkpoint_sequence_regression"


def test_live_journal_step_after_checkpoint_still_requires_reconciliation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")
    mission.journal_step_start(
        root,
        task_id="M-1.9",
        step_id="CHAOS-LIVE-STEP",
        action="pytest -q backend/tests",
        expected_state_change="verification evidence",
        pre_step_sha="d" * 40,
        required_capabilities=["pytest"],
        retry_policy="reconcile_before_retry",
        reconciliation_procedure="check process and test artifacts",
        started_at="2026-09-02T15:30:01Z",
    )

    result = mission.resume_guard(root, capabilities={"pytest": "AVAILABLE"})

    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["reason"] == "interrupted_step"
    assert result["interrupted_step"]["step_id"] == "CHAOS-LIVE-STEP"

def test_uncheckpointed_mission_state_change_requires_reconciliation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")
    state = json.loads((root / "MISSION_STATE.json").read_text(encoding="utf-8"))
    state["next_action"] = "new uncheckpointed action"
    mission.atomic_write_json(root / "MISSION_STATE.json", state, allowed_root=root)

    result = mission.resume_guard(root, capabilities={})

    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["reason"] == "uncheckpointed_state_change"
    assert "MISSION_STATE.json" in result["changed_state_files"]


def test_branch_head_drift_is_named(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")

    result = mission.resume_guard(
        root, capabilities={}, **_observations(observed_branch_head="e" * 40)
    )

    assert result["disposition"] == "DRIFT_DETECTED"
    assert {item["scope"] for item in result["drift"]} == {"branch_head"}


def test_registered_branch_and_worktree_drift_are_named(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")

    result = mission.resume_guard(
        root,
        capabilities={},
        **_observations(
            observed_branch="other-branch",
            observed_worktree="/tmp/other-worktree",
        ),
    )

    assert result["disposition"] == "DRIFT_DETECTED"
    assert {item["scope"] for item in result["drift"]} == {"active_branch", "active_worktree"}

def test_checkpoint_hashes_all_durable_control_state_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.atomic_write_json(root / "TASK_GRAPH.json", {"tasks": []}, allowed_root=root)
    mission.atomic_write_json(root / "EVIDENCE_LEDGER.json", {"records": []}, allowed_root=root)
    mission.atomic_write_json(
        root / "BRANCH_REGISTRY.json",
        {
            "contract_version": "wfh_branch_registry_v1",
            "mission_id": "WFH-ME-V3-20260902",
            "records": [
                {
                    "task_id": "M-1.9_CHAOS_RECOVERY_CERTIFICATION",
                    "branch_name": "feat/mission-continuity-v1-20260902",
                    "worktree_path": "/srv/wfh-worktrees/mission-continuity-v1-20260902",
                    "current_sha": "d" * 40,
                    "state": "VERIFYING",
                }
            ],
        },
        allowed_root=root,
    )
    mission.atomic_write_json(
        root / "SCIENTIFIC_STATE.json",
        {
            "contract_version": "wfh_scientific_state_v1",
            "mission_id": "WFH-ME-V3-20260902",
            "final_holdout_opened": False,
            "final_holdout_retired": False,
        },
        allowed_root=root,
    )
    (root / "DECISION_LOG.jsonl").write_text(
        '{"decision_id":"D-1"}\n', encoding="utf-8"
    )

    pointer = mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")
    checkpoint = json.loads((root / pointer["path"]).read_text(encoding="utf-8"))

    assert set(checkpoint["state_files"]) == set(mission.DURABLE_STATE_FILES)
    for name in mission.DURABLE_STATE_FILES:
        assert checkpoint["state_files"][name] == mission._sha256_bytes((root / name).read_bytes())


def test_uncheckpointed_task_graph_change_requires_reconciliation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.atomic_write_json(root / "TASK_GRAPH.json", {"tasks": []}, allowed_root=root)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")
    mission.atomic_write_json(
        root / "TASK_GRAPH.json", {"tasks": [{"task_id": "unexpected"}]}, allowed_root=root
    )

    result = mission.resume_guard(root, capabilities={})

    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["reason"] == "uncheckpointed_state_change"
    assert "TASK_GRAPH.json" in result["changed_state_files"]

def test_dirty_registered_worktree_requires_reconciliation(tmp_path: Path) -> None:
    root = _root(tmp_path, active_worktree_dirty=False)
    mission.create_checkpoint(root, created_at="2026-09-02T15:30:00Z")

    result = mission.resume_guard(
        root,
        observed_branch_head="d" * 40,
        observed_branch="feat/mission-continuity-v1-20260902",
        observed_worktree="/srv/wfh-worktrees/mission-continuity-v1-20260902",
        observed_worktree_dirty=True,
        capabilities={},
    )

    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["reason"] == "uncommitted_worktree_changes"
    assert result["retry_allowed"] is False