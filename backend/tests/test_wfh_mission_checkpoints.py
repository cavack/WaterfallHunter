from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import wfh_mission as mission
from wfh_mission_test_support import valid_state, write_required_bundle


REPO = Path(__file__).resolve().parents[2]


def _state(next_action: str = "run Task 3") -> dict[str, object]:
    return valid_state(
        current_task="M0.3",
        next_action=next_action,
        completed_tasks=["M0.1", "M0.2"],
        do_not_repeat=["resume-intent-contract", "state-core"],
        open_defects=[],
        blocked_tasks=[],
    )


def _init_mission_dir(tmp_path: Path) -> Path:
    root = tmp_path / "WFH-ME-V3-20260902"
    root.mkdir()
    return write_required_bundle(root, state=_state())


def test_checkpoint_hash_mismatch_blocks_resume(tmp_path: Path) -> None:
    root = _init_mission_dir(tmp_path)
    pointer = mission.create_checkpoint(root, created_at="2026-09-02T14:55:00Z")
    checkpoint = root / pointer["path"]
    checkpoint.write_bytes(checkpoint.read_bytes() + b" ")

    result = mission.load_latest_checkpoint(root)

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "checkpoint_hash_mismatch"


def test_checkpoints_use_monotonic_ids(tmp_path: Path) -> None:
    root = _init_mission_dir(tmp_path)

    first = mission.create_checkpoint(root, created_at="2026-09-02T14:55:00Z")
    second = mission.create_checkpoint(root, created_at="2026-09-02T14:56:00Z")

    assert first["checkpoint_id"] == "CP-000001"
    assert second["checkpoint_id"] == "CP-000002"


def test_fresh_python_process_recovers_exact_next_action(tmp_path: Path) -> None:
    root = _init_mission_dir(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T14:55:00Z")

    code = (
        "import json; from pathlib import Path; "
        "from scripts import wfh_mission as m; "
        f"print(json.dumps(m.load_latest_checkpoint(Path({str(root)!r}))))"
    )
    proc = subprocess.run(
        ["python3", "-c", code],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    recovered = json.loads(proc.stdout)
    assert recovered["disposition"] == "RESUME_READY"
    assert recovered["checkpoint"]["mission_id"] == "WFH-ME-V3-20260902"
    assert recovered["checkpoint"]["mission_state"]["current_task"] == "M0.3"
    assert recovered["checkpoint"]["mission_state"]["next_action"] == "run Task 3"


def test_resume_projection_contains_only_continuation_essentials(tmp_path: Path) -> None:
    root = _init_mission_dir(tmp_path)
    pointer = mission.create_checkpoint(root, created_at="2026-09-02T14:55:00Z")
    text = (root / "RESUME.md").read_text(encoding="utf-8")

    assert pointer["checkpoint_id"] in text
    assert "WFH-ME-V3-20260902" in text
    assert "M0.3" in text
    assert "run Task 3" in text
    assert "resume-intent-contract" in text
    assert "feat/mission-continuity-v1-20260902" in text
