from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import wfh_mission as mission
from wfh_mission_test_support import valid_state, write_required_bundle


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/wfh_mission.py"


def _state() -> dict[str, object]:
    return valid_state(current_task="M0.5", next_action="continue Task 5")


def _control_root(tmp_path: Path) -> Path:
    control = tmp_path / "mission-control"
    mission_dir = control / "WFH-ME-V3-20260902"
    mission_dir.mkdir(parents=True)
    write_required_bundle(mission_dir, state=_state())
    mission.create_checkpoint(mission_dir, created_at="2026-09-02T15:10:00Z")
    mission.atomic_write_json(
        control / "ACTIVE_MISSION.json",
        {
            "contract_version": "wfh_active_mission_v1",
            "mission_id": "WFH-ME-V3-20260902",
            "mission_path": "WFH-ME-V3-20260902",
        },
        allowed_root=control,
    )
    return control


def _run(control: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WFH_MISSION_CONTROL_ROOT"] = str(control)
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _resume_observation_args() -> tuple[str, ...]:
    return (
        "--observed-main-sha", "b" * 40,
        "--observed-production-sha", "c" * 40,
        "--observed-branch-head", "d" * 40,
        "--observed-branch", "feat/mission-continuity-v1-20260902",
        "--observed-worktree", "/srv/wfh-worktrees/mission-continuity-v1-20260902",
        "--observed-worktree-status", "clean",
    )


def test_cli_rejects_noncanonical_resume_phrase(tmp_path: Path) -> None:
    control = _control_root(tmp_path)
    proc = _run(control, "resume", "--intent", "ادامه پروژه", "--json")

    assert proc.returncode != 0
    output = json.loads(proc.stdout)
    assert output["reason"] == "resume_intent_mismatch"


def test_cli_canonical_phrase_returns_json_from_active_mission(tmp_path: Path) -> None:
    control = _control_root(tmp_path)
    proc = _run(control, "resume", "--intent", "ادامه کار گروهی", "--json", *_resume_observation_args())

    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert output["disposition"] == "RESUME_READY"
    assert output["checkpoint"]["mission_id"] == "WFH-ME-V3-20260902"
    assert output["checkpoint"]["mission_state"]["next_action"] == "continue Task 5"


def test_cli_normalizes_unicode_whitespace_for_canonical_phrase(tmp_path: Path) -> None:
    control = _control_root(tmp_path)
    proc = _run(control, "resume", "--intent", "  ادامه\u00a0کار\u2003گروهی  ", "--json", *_resume_observation_args())

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["disposition"] == "RESUME_READY"


def test_cli_corrupt_checkpoint_pointer_returns_nonzero(tmp_path: Path) -> None:
    control = _control_root(tmp_path)
    (control / "WFH-ME-V3-20260902" / "LATEST_CHECKPOINT.json").write_text(
        "{not-json}\n", encoding="utf-8"
    )

    proc = _run(control, "resume", "--intent", "ادامه کار گروهی", "--json", *_resume_observation_args())

    assert proc.returncode != 0
    output = json.loads(proc.stdout)
    assert output["disposition"] == "RESUME_BLOCKED"
