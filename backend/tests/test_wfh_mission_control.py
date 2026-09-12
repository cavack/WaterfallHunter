from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts/wfh_mission.py"


def _mission():
    assert MODULE.exists(), "mission-control module is not implemented yet"
    spec = importlib.util.spec_from_file_location("wfh_mission_under_test", MODULE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_state() -> dict[str, object]:
    return {
        "contract_version": "wfh_mission_continuity_v1",
        "mission_id": "WFH-ME-V3-20260902",
        "project": "TWFH",
        "repository": "cavack/WFH-ORG",
        "baseline_main_sha": "a" * 40,
        "current_main_sha": "b" * 40,
        "current_phase": "M0",
        "current_task": "M0.2",
        "next_action": "continue Task 2",
    }


def test_mission_state_rejects_missing_mission_id() -> None:
    mission = _mission()
    state = _base_state()
    state.pop("mission_id")

    errors = mission.validate_mission_state(state)

    assert any("mission_id" in error for error in errors)


def test_mission_state_rejects_malformed_git_sha() -> None:
    mission = _mission()
    state = _base_state()
    state["current_main_sha"] = "not-a-sha"

    errors = mission.validate_mission_state(state)

    assert any("current_main_sha" in error for error in errors)


def test_task_graph_rejects_more_than_three_in_progress_workstreams() -> None:
    mission = _mission()
    graph = {
        "contract_version": "wfh_mission_task_graph_v1",
        "tasks": [
            {"task_id": f"T{i}", "state": "IN_PROGRESS", "parents": []}
            for i in range(4)
        ],
    }

    errors = mission.validate_task_graph(graph)

    assert any("three" in error.lower() or "3" in error for error in errors)


def test_child_complete_requires_parent_consumable_handoff() -> None:
    mission = _mission()
    graph = {
        "contract_version": "wfh_mission_task_graph_v1",
        "tasks": [
            {"task_id": "P", "state": "IN_PROGRESS", "parents": []},
            {"task_id": "C", "state": "COMPLETE", "parents": ["P"]},
        ],
    }

    errors = mission.validate_task_graph(graph)

    assert any("handoff" in error.lower() and "C" in error for error in errors)


def test_evidence_ledger_rejects_unknown_classification() -> None:
    mission = _mission()
    ledger = {
        "contract_version": "wfh_evidence_ledger_v1",
        "records": [
            {
                "evidence_id": "F-0001",
                "classification": "CERTAINLY_TRUE",
                "statement": "bad taxonomy",
            }
        ],
    }

    errors = mission.validate_evidence_ledger(ledger)

    assert any("classification" in error.lower() for error in errors)


def test_atomic_write_cleans_temp_file_after_replace_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mission = _mission()
    target = tmp_path / "MISSION_STATE.json"
    target.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(src: object, dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(mission.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        mission.atomic_write_json(target, {"new": True}, allowed_root=tmp_path)

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob(".*.tmp-*"))


def test_scientific_state_holdout_open_is_monotonic() -> None:
    mission = _mission()
    current = {
        "contract_version": "wfh_scientific_state_v1",
        "final_holdout_opened": True,
        "final_holdout_retired": False,
    }

    with pytest.raises(ValueError, match="holdout"):
        mission.update_scientific_state(current, {"final_holdout_opened": False})


def test_scientific_state_can_retire_opened_holdout_without_resetting_it() -> None:
    mission = _mission()
    current = {
        "contract_version": "wfh_scientific_state_v1",
        "final_holdout_opened": True,
        "final_holdout_retired": False,
    }

    updated = mission.update_scientific_state(current, {"final_holdout_retired": True})

    assert updated["final_holdout_opened"] is True
    assert updated["final_holdout_retired"] is True
