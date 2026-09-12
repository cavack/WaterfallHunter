from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import wfh_mission as mission

MISSION_ID = "WFH-ME-V3-20260902"
BRANCH = "feat/mission-continuity-v1-20260902"
WORKTREE = "/srv/wfh-worktrees/mission-continuity-v1-20260902"


def valid_state(**overrides: object) -> dict[str, object]:
    required_capabilities = list(overrides.get("required_capabilities", []))
    minimums = overrides.get("required_capability_states")
    if minimums is None:
        minimums = {name: "AVAILABLE" for name in required_capabilities}
    state: dict[str, object] = {
        "contract_version": mission.MISSION_CONTRACT,
        "mission_id": MISSION_ID,
        "mission_name": "Model Excellence v3",
        "project": mission.CANONICAL_PROJECT,
        "repository": mission.CANONICAL_REPOSITORY,
        "baseline_main_sha": "a" * 40,
        "current_main_sha": "b" * 40,
        "production_sha": "c" * 40,
        "current_phase": "M0",
        "current_task": "M0.1",
        "next_action": "continue mission",
        "active_branch_head": "d" * 40,
        "active_branch": BRANCH,
        "active_worktree": WORKTREE,
        "active_worktree_dirty": False,
        "required_capabilities": required_capabilities,
        "required_capability_states": minimums,
        "next_action_preconditions": [],
    }
    state.update(overrides)
    return state


def write_required_bundle(
    root: Path,
    *,
    state: dict[str, object] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> Path:
    payload = state or valid_state()
    mission_id = str(payload["mission_id"])
    mission.atomic_write_json(root / "MISSION_STATE.json", payload, allowed_root=root)
    mission.atomic_write_json(
        root / "TASK_GRAPH.json", {"tasks": tasks or []}, allowed_root=root
    )
    mission.atomic_write_json(
        root / "EVIDENCE_LEDGER.json", {"records": evidence or []}, allowed_root=root
    )
    mission.atomic_write_json(
        root / "BRANCH_REGISTRY.json",
        {
            "contract_version": "wfh_branch_registry_v1",
            "mission_id": mission_id,
            "records": [
                {
                    "task_id": str(payload["current_task"]),
                    "branch_name": str(payload["active_branch"]),
                    "worktree_path": str(payload["active_worktree"]),
                    "current_sha": str(payload["active_branch_head"]),
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
            "mission_id": mission_id,
            "final_holdout_opened": False,
            "final_holdout_retired": False,
        },
        allowed_root=root,
    )
    mission.atomic_write_json(
        root / "STEP_JOURNAL.json",
        {"contract_version": "wfh_step_journal_v1", "steps": []},
        allowed_root=root,
    )
    (root / "DECISION_LOG.jsonl").write_text("", encoding="utf-8")
    return root


def observations(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "observed_main_sha": "b" * 40,
        "observed_production_sha": "c" * 40,
        "observed_branch_head": "d" * 40,
        "observed_branch": BRANCH,
        "observed_worktree": WORKTREE,
        "observed_worktree_dirty": False,
    }
    values.update(overrides)
    return values
