from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import wfh_mission as mission


def _state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "contract_version": mission.MISSION_CONTRACT,
        "mission_id": "WFH-ME-V3-20260902",
        "mission_name": "Model Excellence v3",
        "project": "TWFH",
        "repository": "cavack/WFH-ORG",
        "baseline_main_sha": "a" * 40,
        "current_main_sha": "b" * 40,
        "production_sha": "c" * 40,
        "current_phase": "M-1",
        "current_task": "M-1.9",
        "next_action": "continue continuity hardening",
        "active_branch_head": "d" * 40,
        "active_branch": "feat/mission-continuity-v1-20260902",
        "active_worktree": "/srv/wfh-worktrees/mission-continuity-v1-20260902",
        "active_worktree_dirty": False,
        "required_capabilities": [],
        "required_capability_states": {},
        "next_action_preconditions": [],
    }
    state.update(overrides)
    return state


def _root(tmp_path: Path, **overrides: object) -> Path:
    root = tmp_path / "WFH-ME-V3-20260902"
    root.mkdir()
    payload = _state(**overrides)
    mission.atomic_write_json(root / "MISSION_STATE.json", payload, allowed_root=root)
    mission.atomic_write_json(root / "TASK_GRAPH.json", {"tasks": []}, allowed_root=root)
    mission.atomic_write_json(root / "EVIDENCE_LEDGER.json", {"records": []}, allowed_root=root)
    mission.atomic_write_json(
        root / "BRANCH_REGISTRY.json",
        {
            "contract_version": "wfh_branch_registry_v1",
            "mission_id": "WFH-ME-V3-20260902",
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
            "mission_id": "WFH-ME-V3-20260902",
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


def _observations(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "observed_main_sha": "b" * 40,
        "observed_production_sha": "c" * 40,
        "observed_branch_head": "d" * 40,
        "observed_branch": "feat/mission-continuity-v1-20260902",
        "observed_worktree": "/srv/wfh-worktrees/mission-continuity-v1-20260902",
        "observed_worktree_dirty": False,
    }
    values.update(overrides)
    return values


def test_atomic_write_fsyncs_parent_after_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "mission"
    root.mkdir()
    events: list[str] = []
    original_replace = mission.os.replace
    monkeypatch.setattr(mission.os, "replace", lambda src, dst: (original_replace(src, dst), events.append("replace"))[0])
    monkeypatch.setattr(mission, "_fsync_directory", lambda path: events.append(f"fsync:{Path(path).name}"), raising=False)
    mission.atomic_write_json(root / "state.json", {"ok": True}, allowed_root=root)
    assert events == ["replace", "fsync:mission"]


def test_absent_durable_file_appearing_requires_reconciliation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root, created_at="2026-09-02T16:30:00Z")
    mission.atomic_write_json(root / "BRANCH_REGISTRY.json", {"records": []}, allowed_root=root)
    result = mission.resume_guard(root, **_observations(), capabilities={})
    assert result["disposition"] == "RECONCILIATION_REQUIRED"
    assert result["reason"] == "uncheckpointed_state_change"
    assert "BRANCH_REGISTRY.json" in result["changed_state_files"]


def test_checkpoint_rejects_invalid_graph_and_evidence(tmp_path: Path) -> None:
    root = _root(tmp_path)
    tasks = [{"task_id": f"T{i}", "state": "IN_PROGRESS", "parents": []} for i in range(4)]
    mission.atomic_write_json(root / "TASK_GRAPH.json", {"tasks": tasks}, allowed_root=root)
    mission.atomic_write_json(
        root / "EVIDENCE_LEDGER.json",
        {"records": [{"evidence_id": "E-1", "classification": "MAYBE"}]},
        allowed_root=root,
    )
    with pytest.raises(ValueError):
        mission.create_checkpoint(root)
    assert not (root / "LATEST_CHECKPOINT.json").exists()


def test_active_pointer_mission_mismatch_is_rejected(tmp_path: Path) -> None:
    control = tmp_path / "control"
    mission_dir = control / "mission-b"
    mission_dir.mkdir(parents=True)
    mission.atomic_write_json(mission_dir / "MISSION_STATE.json", _state(mission_id="MISSION-B"), allowed_root=mission_dir)
    mission.atomic_write_json(control / "ACTIVE_MISSION.json", {"mission_id": "MISSION-A", "mission_path": "mission-b"}, allowed_root=control)
    with pytest.raises(ValueError, match="mission identity"):
        mission.resolve_active_mission(control)


def test_github_comment_listing_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_request(repository: str, endpoint: str, **kwargs):
        calls.append(endpoint)
        if "&page=1" in endpoint:
            return [{"body": str(i)} for i in range(100)]
        if "&page=2" in endpoint:
            return [{"body": "last"}]
        raise AssertionError(endpoint)

    monkeypatch.setattr(mission, "_github_request", fake_request)
    comments = mission._github_list_issue_comments("cavack/WFH-ORG", 113, "token")
    assert len(comments) == 101
    assert any("&page=1" in call for call in calls)
    assert any("&page=2" in call for call in calls)


def test_sync_rejects_swapped_issue_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root)
    monkeypatch.setattr(mission, "_github_token", lambda: "token")
    monkeypatch.setattr(
        mission,
        "_github_get_issue",
        lambda repository, issue_number, token: {"title": "[MISSION] wrong", "body": "<!-- wfh-mission-state:v1 mission=WFH-ME-V3-20260902 -->"},
        raising=False,
    )
    with pytest.raises(ValueError, match="identity"):
        mission.sync_github(root, repository="cavack/WFH-ORG", pointer_issue=114, mission_issue=113)


def test_declared_authority_is_not_an_observed_capability_state() -> None:
    with pytest.raises(ValueError, match="capability state"):
        mission._parse_capabilities(["github_connector=READ_WRITE_REPO"])


def test_ready_resume_requires_checkpointed_observations(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root)
    result = mission.resume_guard(root, capabilities={})
    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "required_observation_unavailable"
    assert set(result["missing_observations"]) >= {
        "repository_main",
        "production_revision",
        "branch_head",
        "active_branch",
        "active_worktree",
        "worktree_cleanliness",
    }


def test_resume_projection_uses_checkpointed_journal_operation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.journal_step_start(
        root,
        task_id="M-1.9",
        step_id="STEP-42",
        action="run exact-head verification",
        expected_state_change="evidence only",
        pre_step_sha="d" * 40,
        required_capabilities=["pytest"],
        retry_policy="reconcile_before_retry",
        reconciliation_procedure="inspect process and artifacts",
    )
    mission.create_checkpoint(root)
    text = (root / "RESUME.md").read_text(encoding="utf-8")
    assert "STEP-42" in text
    assert "run exact-head verification" in text


def test_journal_mutations_enter_mission_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _root(tmp_path)
    events: list[str] = []

    @contextmanager
    def fake_lock(_root: Path):
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(mission, "_mission_lock", fake_lock, raising=False)
    mission.journal_step_start(
        root,
        task_id="M-1.9",
        step_id="LOCK-1",
        action="test lock",
        expected_state_change="journal",
        pre_step_sha="d" * 40,
        required_capabilities=[],
        retry_policy="reconcile_before_retry",
        reconciliation_procedure="inspect journal",
    )
    mission.journal_step_complete(root, "LOCK-1")
    assert events == ["enter", "exit", "enter", "exit"]


def test_checkpoint_rejects_state_missing_resume_observations(tmp_path: Path) -> None:
    root = _root(tmp_path)
    state = json.loads((root / "MISSION_STATE.json").read_text(encoding="utf-8"))
    for key in (
        "production_sha",
        "active_branch_head",
        "active_branch",
        "active_worktree",
        "active_worktree_dirty",
    ):
        state.pop(key, None)
    mission.atomic_write_json(root / "MISSION_STATE.json", state, allowed_root=root)

    with pytest.raises(ValueError, match="production_sha|active_branch"):
        mission.create_checkpoint(root)


def test_checkpoint_requires_every_contract_durable_state_file(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "BRANCH_REGISTRY.json").unlink()

    with pytest.raises(ValueError, match="BRANCH_REGISTRY.json"):
        mission.create_checkpoint(root)


def test_resume_requires_capability_authorization_level(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        required_capabilities=["github_connector"],
        required_capability_states={"github_connector": "AUTHORIZED_WRITE"},
    )
    mission.create_checkpoint(root)

    result = mission.resume_guard(
        root,
        **_observations(),
        capabilities={"github_connector": "AUTHORIZED_READ"},
    )

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "required_capability_unavailable"
    assert result["insufficient_capabilities"] == ["github_connector"]


@pytest.mark.parametrize(
    "tasks",
    [
        [
            {"task_id": "A", "state": "READY", "parents": []},
            {"task_id": "A", "state": "READY", "parents": []},
        ],
        [{"task_id": "A", "state": "READY", "parents": ["MISSING"]}],
        [
            {"task_id": "A", "state": "READY", "parents": ["B"]},
            {"task_id": "B", "state": "READY", "parents": ["A"]},
        ],
    ],
    ids=["duplicate", "dangling", "cycle"],
)
def test_checkpoint_rejects_invalid_task_dependency_graph(
    tmp_path: Path, tasks: list[dict[str, object]]
) -> None:
    root = _root(tmp_path)
    mission.atomic_write_json(root / "TASK_GRAPH.json", {"tasks": tasks}, allowed_root=root)

    with pytest.raises(ValueError, match="task graph|task .*parent|duplicate|cycle"):
        mission.create_checkpoint(root)


def test_resume_guard_holds_mission_lock_for_entire_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    mission.create_checkpoint(root)
    events: list[str] = []

    @contextmanager
    def fake_lock(_root: Path):
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(mission, "_mission_lock", fake_lock)
    result = mission.resume_guard(root, **_observations(), capabilities={})

    assert result["disposition"] == "RESUME_READY"
    assert events == ["enter", "exit"]


def test_github_pointer_is_not_published_before_checkpoint_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    pointer = mission.create_checkpoint(root)
    state = json.loads((root / "MISSION_STATE.json").read_text(encoding="utf-8"))
    marker = f"<!-- wfh-mission-state:v1 mission={state['mission_id']} -->"
    pointer_marker = f"<!-- wfh-mission-pointer:v1 mission={state['mission_id']} -->"
    monkeypatch.setattr(mission, "_github_token", lambda: "token")
    monkeypatch.setattr(
        mission,
        "_github_get_issue",
        lambda repository, issue_number, token: (
            {"title": "[MISSION][POINTER] TWFH Active Mission", "body": pointer_marker}
            if issue_number == 114
            else {"title": f"[MISSION] {state['mission_id']} — Model Excellence v3", "body": marker}
        ),
    )
    edits: list[int] = []
    monkeypatch.setattr(
        mission,
        "_github_edit_issue",
        lambda repository, issue_number, body, token: edits.append(issue_number),
    )
    monkeypatch.setattr(mission, "_github_list_issue_comments", lambda *args: [])

    def fail_comment(*args, **kwargs):
        raise RuntimeError("simulated comment failure")

    monkeypatch.setattr(mission, "_github_comment_issue", fail_comment)

    with pytest.raises(RuntimeError, match="simulated comment failure"):
        mission.sync_github(
            root,
            repository="cavack/WFH-ORG",
            pointer_issue=114,
            mission_issue=113,
        )

    assert 114 not in edits
    assert pointer["checkpoint_id"]


def test_mission_issue_title_is_derived_from_mission_state(tmp_path: Path) -> None:
    root = _root(tmp_path, mission_name="Successor Mission")
    pointer = mission.create_checkpoint(root)
    loaded = mission.load_latest_checkpoint(root)

    rendered = mission.render_mission_issue(loaded["checkpoint"], pointer)

    assert f"# {loaded['checkpoint']['mission_id']} — Successor Mission" in rendered


def test_checkpoint_rejects_secret_bearing_mission_state(tmp_path: Path) -> None:
    root = _root(tmp_path, api_key="DO_NOT_SERIALIZE")

    with pytest.raises(ValueError, match="secret|credential"):
        mission.create_checkpoint(root)


def test_checkpoint_rejects_noncanonical_project_repository_identity(tmp_path: Path) -> None:
    root = _root(tmp_path, project="OTHER", repository="other/repo")

    with pytest.raises(ValueError, match="canonical|project|repository"):
        mission.create_checkpoint(root)


def test_resume_blocks_unmet_structured_next_action_precondition(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        model_excellence_phase0_blocked=False,
        next_action_preconditions=[
            {
                "kind": "state_equals",
                "field": "model_excellence_phase0_blocked",
                "expected": True,
            }
        ],
    )
    mission.create_checkpoint(root)

    result = mission.resume_guard(root, **_observations(), capabilities={})

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "next_action_precondition_unmet"


def test_nested_secret_bearing_state_is_rejected(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        integration={"telegram": {"telegram_bot_token": "DO_NOT_SERIALIZE"}},
    )

    with pytest.raises(ValueError, match="secret|credential"):
        mission.create_checkpoint(root)


def test_write_authorized_capability_satisfies_write_requirement(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        required_capabilities=["github_connector"],
        required_capability_states={"github_connector": "AUTHORIZED_WRITE"},
    )
    mission.create_checkpoint(root)

    result = mission.resume_guard(
        root,
        **_observations(),
        capabilities={"github_connector": "AUTHORIZED_WRITE"},
    )

    assert result["disposition"] == "RESUME_READY"


def test_observation_precondition_can_require_checkpoint_value(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        next_action_preconditions=[
            {
                "kind": "observation_equals",
                "scope": "repository_main",
                "expected": "checkpoint",
            }
        ],
    )
    mission.create_checkpoint(root)

    ready = mission.resume_guard(root, **_observations(), capabilities={})
    drifted = mission.resume_guard(
        root,
        **_observations(observed_main_sha="e" * 40),
        capabilities={},
    )

    assert ready["disposition"] == "RESUME_READY"
    assert drifted["disposition"] == "RESUME_BLOCKED"
    assert drifted["reason"] == "next_action_precondition_unmet"


def test_checkpoint_rejects_branch_registry_mismatch_with_active_state(tmp_path: Path) -> None:
    root = _root(tmp_path)
    mission.atomic_write_json(
        root / "BRANCH_REGISTRY.json",
        {
            "contract_version": "wfh_branch_registry_v1",
            "mission_id": "WFH-ME-V3-20260902",
            "records": [
                {
                    "task_id": "M-1.9",
                    "branch_name": "other-branch",
                    "worktree_path": "/tmp/other-worktree",
                    "current_sha": "e" * 40,
                    "state": "VERIFYING",
                }
            ],
        },
        allowed_root=root,
    )

    with pytest.raises(ValueError, match="active branch|active worktree|active branch head"):
        mission.create_checkpoint(root)


def test_resume_rejects_checkpoint_missing_current_durable_file_digest(tmp_path: Path) -> None:
    root = _root(tmp_path)
    pointer = mission.create_checkpoint(root, created_at="2026-09-02T18:30:00Z")
    checkpoint_path = root / pointer["path"]
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["state_files"].pop(mission.STEP_JOURNAL_FILE)
    payload = mission._canonical_json_bytes(checkpoint)
    checkpoint_path.write_bytes(payload)
    pointer["sha256"] = mission._sha256_bytes(payload)
    mission.atomic_write_json(root / "LATEST_CHECKPOINT.json", pointer, allowed_root=root)

    result = mission.resume_guard(root, **_observations(), capabilities={})

    assert result["disposition"] == "RESUME_BLOCKED"
    assert result["reason"] == "checkpoint_state_file_contract_incomplete"
    assert mission.STEP_JOURNAL_FILE in result["missing_checkpoint_state_files"]
