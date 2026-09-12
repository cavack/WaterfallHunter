from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts import wfh_council as council


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / ".agents" / "wfh-council" / "manifest.json"


def test_manifest_is_valid_and_preserves_core_safety_contract() -> None:
    manifest = council.load_manifest(MANIFEST)

    assert council.validate_manifest(REPO, manifest) == []
    assert manifest["routes"]["model_optimization"][0] == "chief_orchestrator"
    assert manifest["production_authority_role"] == "release_certifier"
    assert manifest["protected_invariants"]["live_order_placement"] == "FORBIDDEN"
    assert manifest["protected_invariants"]["live_trading_enabled"] is False


def test_manifest_role_ids_are_unique() -> None:
    manifest = council.load_manifest(MANIFEST)
    ids = [role["id"] for role in manifest["roles"]]
    assert len(ids) == len(set(ids))

def test_manifest_rejects_missing_canonical_skill() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["roles"][0]["skills"] = ["invented-skill"]

    errors = council.validate_manifest(REPO, broken)

    assert any("invented-skill" in error and "missing canonical skill" in error for error in errors)


def test_model_route_has_required_owners_in_order() -> None:
    manifest = council.load_manifest(MANIFEST)
    route = manifest["routes"]["model_optimization"]

    assert route.index("market_evidence_forensics") < route.index("strategy_owner")
    assert route.index("strategy_owner") < route.index("quant_validation_lead")
    assert route.index("quant_validation_lead") < route.index("false_positive_hunter")
    assert route.index("false_negative_hunter") < route.index("regression_lead")
    assert route[-1] == "release_certifier"


def test_only_release_certifier_has_production_authority() -> None:
    manifest = council.load_manifest(MANIFEST)
    privileged = [role["id"] for role in manifest["roles"] if role.get("production_authority")]

    assert privileged == ["release_certifier"]

def test_route_task_returns_role_skill_packets() -> None:
    manifest = council.load_manifest(MANIFEST)

    packets = council.route_task(manifest, "model_optimization")
    roles = [packet["role"] for packet in packets]

    assert roles[0] == "chief_orchestrator"
    assert roles.index("strategy_owner") < roles.index("quant_validation_lead")
    assert roles[-1] == "release_certifier"
    assert packets[2]["skills"] == ["strategy-score-lifecycle"]


def test_route_task_rejects_unknown_task_type() -> None:
    manifest = council.load_manifest(MANIFEST)

    try:
        council.route_task(manifest, "invented_route")
    except KeyError as exc:
        assert "invented_route" in str(exc)
    else:
        raise AssertionError("unknown route must fail")

def test_doctor_reports_repo_identity_and_optional_unavailable(monkeypatch) -> None:
    def fake_git_output(repo_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        if args == ("branch", "--show-current"):
            return "test-branch"
        raise AssertionError(args)

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        if name == "python3":
            return "/usr/bin/python3"
        return None

    monkeypatch.setattr(council, "_git_output", fake_git_output)
    monkeypatch.setattr(council.shutil, "which", fake_which)
    result = council.doctor(REPO)

    assert result["repo"]["git_sha"] == "abc123"
    assert result["repo"]["branch"] == "test-branch"
    assert result["repo"]["status"] == "AVAILABLE"
    assert result["tools"]["git"]["status"] == "AVAILABLE"
    assert result["tools"]["python"]["status"] == "AVAILABLE"
    assert result["tools"]["coderabbit"]["status"] == "UNAVAILABLE"
    assert result["status"] == "READY"

def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_research_snapshot_refuses_sparse_development_evidence(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "DATASET_AUDIT.json",
        {
            "contract": "DATASET_AUDIT.v1",
            "evidence_tier": "TIER_2_DEVELOPMENT_GRADE_NOT_PROMOTION_EVIDENCE",
            "data_sha256": "abc",
            "rows": 204338,
            "episodes": 773,
            "symbols": 281,
            "span_days": 15.9,
            "limitations": ["Tier-2 causal reconstruction is development-only"],
        },
    )
    _write_json(
        tmp_path / "OOS_VALIDATION.json",
        {
            "contract": "OOS_VALIDATION.v1",
            "status": "BLOCKED_NOT_RUN_AS_SELECTION_EVIDENCE",
            "maximum_complete_outcomes_any_stage2_config": 2,
            "reason": "insufficient promotion evidence",
        },
    )
    _write_json(
        tmp_path / "BEST_DEVELOPMENT_CONFIG.json",
        {
            "contract": "BEST_DEVELOPMENT_CONFIG.v1",
            "status": "NO_SCIENTIFIC_CHAMPION",
            "promotion_allowed": False,
            "maximum_complete_outcomes_any_stage2_config": 2,
            "reasons": ["Tier-2 dataset only"],
        },
    )
    _write_json(
        tmp_path / "PRODUCTION_VS_CHALLENGERS.json",
        {"contract": "PRODUCTION_VS_CHALLENGERS.v1", "promotion_allowed": False},
    )
    _write_json(
        tmp_path / "OUTCOME_INTEGRITY.json",
        {
            "contract": "OUTCOME_INTEGRITY.v1",
            "outcome_complete": 210,
            "unavailable": 580,
            "net_cost_adjusted_r_available": False,
            "limitations": ["gross_r is not net"],
        },
    )

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
    assert "insufficient_oos_evidence" in summary["blockers"]
    assert "insufficient_promotion_span" in summary["blockers"]
    assert "missing_net_cost_adjusted_r" in summary["blockers"]
    assert summary["dataset"]["span_days"] == 15.9
    assert summary["dataset"]["data_sha256"] == "abc"

def test_research_snapshot_marks_missing_artifacts_without_guessing(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "DATASET_AUDIT.json",
        {"contract": "DATASET_AUDIT.v1", "span_days": 60.0},
    )

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
    assert summary["artifacts"]["OOS_VALIDATION.json"]["status"] == "MISSING_ARTIFACT"
    assert "insufficient_oos_evidence" in summary["blockers"]

def test_research_snapshot_cli_emits_json(tmp_path: Path, capsys) -> None:
    _write_json(
        tmp_path / "DATASET_AUDIT.json",
        {"contract": "DATASET_AUDIT.v1", "span_days": 10.0},
    )

    rc = council.main(
        [
            "--repo-root",
            str(REPO),
            "research-snapshot",
            "--research-dir",
            str(tmp_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert output["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"

def test_council_docs_cover_every_role_and_tool_class() -> None:
    manifest = council.load_manifest(MANIFEST)
    council_doc = (REPO / ".agents/wfh-council/COUNCIL.md").read_text(encoding="utf-8")
    tools_doc = (REPO / ".agents/wfh-council/TOOLS.md").read_text(encoding="utf-8")

    for role in manifest["roles"]:
        assert role["id"] in council_doc
    for tool_class in [*manifest["tools"]["required"], *manifest["tools"]["optional"]]:
        assert tool_class in tools_doc


def test_research_registry_is_preregisterable_and_falsifiable() -> None:
    text = (REPO / ".agents/wfh-council/RESEARCH.md").read_text(encoding="utf-8")

    for label in [
        "mechanism:",
        "point_in_time_requirement:",
        "falsifier:",
        "promotion_gate:",
    ]:
        assert label in text
    assert "order flow" in text.lower()
    assert "basis" in text.lower()
    assert "regime" in text.lower()


def test_snapshot_separates_repository_and_production_revision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        council,
        "doctor",
        lambda repo_root, **kwargs: {
            "repo": {
                "status": "AVAILABLE",
                "path": str(repo_root),
                "git_sha": "repo-sha-456",
                "branch": "test",
            }
        },
    )
    _write_json(
        tmp_path / "DATASET_AUDIT.json",
        {"contract": "DATASET_AUDIT.v1", "span_days": 10.0},
    )
    manifest = council.load_manifest(MANIFEST)

    snapshot = council.build_snapshot(
        REPO,
        manifest,
        research_dir=tmp_path,
        production_revision="production-sha-123",
    )

    assert snapshot["repo"]["git_sha"] != "production-sha-123"
    assert snapshot["repo"]["classification"] == "VERIFIED_FACT"
    assert snapshot["runtime"]["production_revision"] == {
        "classification": "UNVERIFIED_CLAIM",
        "value": "production-sha-123",
    }
    assert snapshot["readiness"]["repo_matches_claimed_production_revision"] is False
    assert snapshot["research"]["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"


def test_snapshot_marks_unknown_runtime_and_preserves_policy_assertion() -> None:
    manifest = council.load_manifest(MANIFEST)

    snapshot = council.build_snapshot(REPO, manifest)

    assert snapshot["runtime"]["production_revision"] == {
        "classification": "UNAVAILABLE",
        "value": None,
    }
    assert "production_revision" in snapshot["unknowns"]
    assert snapshot["runtime"]["live_trading_enabled"] == {
        "classification": "POLICY_ASSERTION",
        "value": False,
    }
    assert snapshot["protected_invariants"]["entry_ready_minimum"] == 78.0
    assert snapshot["protected_invariants"]["anti_chase_atr"] == 1.2


def test_snapshot_cli_emits_stable_json(capsys) -> None:
    rc = council.main(
        ["--repo-root", str(REPO), "snapshot", "--production-revision", "prod-456", "--json"]
    )
    output = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert output["contract_version"] == "wfh_council_snapshot_v1"
    assert output["runtime"]["production_revision"] == {"classification": "UNVERIFIED_CLAIM", "value": "prod-456"}
    assert output["generated_at"].endswith("Z")


def test_snapshot_marks_repo_identity_unavailable_when_doctor_cannot_resolve_git(monkeypatch) -> None:
    manifest = council.load_manifest(MANIFEST)
    unavailable_repo = {
        "status": "UNAVAILABLE",
        "path": str(REPO),
        "git_sha": None,
        "branch": None,
    }
    monkeypatch.setattr(
        council,
        "doctor",
        lambda repo_root, **kwargs: {"repo": unavailable_repo},
    )

    snapshot = council.build_snapshot(REPO, manifest)

    assert snapshot["repo"]["classification"] == "UNAVAILABLE"
    assert "repo_identity" in snapshot["unknowns"]


def test_validate_manifest_handles_non_mapping_routes_without_crashing() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["routes"] = []

    errors = council.validate_manifest(REPO, broken)

    assert "manifest routes must be a non-empty object" in errors
    assert any("model_optimization route" in error for error in errors)


def test_validate_cli_reports_invalid_for_non_mapping_routes(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    manifest_dir = repo / ".agents/wfh-council"
    manifest_dir.mkdir(parents=True)
    broken = council.load_manifest(MANIFEST)
    broken["routes"] = []
    _write_json(manifest_dir / "manifest.json", broken)

    rc = council.main(["--repo-root", str(repo), "validate", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert output["status"] == "INVALID"


def _write_promotion_candidate_artifacts(root: Path) -> None:
    dataset_hash = "dataset-sha"
    outcome_hash = "outcome-sha"
    _write_json(root / "DATASET_AUDIT.json", {
        "contract": "DATASET_AUDIT.v1", "evidence_tier": "TIER_1_PROMOTION_GRADE",
        "data_sha256": dataset_hash, "span_days": 60.0,
    })
    _write_json(root / "OOS_VALIDATION.json", {
        "contract": "OOS_VALIDATION.v1", "status": "VALIDATED",
        "dataset_sha256": dataset_hash, "outcome_cache_sha256": outcome_hash,
    })
    _write_json(root / "BEST_DEVELOPMENT_CONFIG.json", {
        "contract": "BEST_DEVELOPMENT_CONFIG.v1", "status": "SCIENTIFIC_CHAMPION",
        "promotion_allowed": True,
    })
    _write_json(root / "PRODUCTION_VS_CHALLENGERS.json", {
        "contract": "PRODUCTION_VS_CHALLENGERS.v1", "promotion_allowed": True,
    })
    _write_json(root / "GATE_REJECTION_FUNNEL.json", {
        "contract": "GATE_REJECTION_FUNNEL.v1", "episodes_total": 100,
    })
    _write_json(root / "OUTCOME_INTEGRITY.json", {
        "contract": "OUTCOME_INTEGRITY.v1", "cache_sha256": outcome_hash,
        "net_cost_adjusted_r_available": True,
        "causal_entry_before_observation_count": 0, "duplicate_snapshot_ids": 0,
    })


def test_research_snapshot_requires_valid_contracts_and_provenance(tmp_path: Path) -> None:
    _write_promotion_candidate_artifacts(tmp_path)
    dataset_path = tmp_path / "DATASET_AUDIT.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset["contract"] = "DATASET_AUDIT.v999"
    _write_json(dataset_path, dataset)

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
    assert "invalid_research_artifact_contract:DATASET_AUDIT.json" in summary["blockers"]


def test_research_snapshot_requires_matching_provenance_hashes(tmp_path: Path) -> None:
    _write_promotion_candidate_artifacts(tmp_path)
    oos_path = tmp_path / "OOS_VALIDATION.json"
    oos = json.loads(oos_path.read_text(encoding="utf-8"))
    oos["dataset_sha256"] = "different-dataset"
    _write_json(oos_path, oos)

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
    assert "dataset_provenance_mismatch" in summary["blockers"]


def test_research_snapshot_can_identify_structurally_valid_candidate(tmp_path: Path) -> None:
    _write_promotion_candidate_artifacts(tmp_path)

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "PROMOTION_EVIDENCE_CANDIDATE"
    assert summary["blockers"] == []


def test_council_skill_resolution_uses_canonical_precedence() -> None:
    text = (REPO / ".agents/wfh-council/COUNCIL.md").read_text(encoding="utf-8")

    assert "skills/waterfallhunter" in text
    assert ".agents/skills" in text
    assert "canonical" in text.lower()
    assert "precedence" in text.lower()
    assert "read completely" in text.lower()


def test_manifest_inventory_covers_declared_operational_capabilities() -> None:
    manifest = council.load_manifest(MANIFEST)
    optional = set(manifest["tools"]["optional"])

    assert {"grafana", "alertmanager", "pytest", "market_data_connectors"} <= optional


def test_research_registry_covers_remaining_challenger_families() -> None:
    text = (REPO / ".agents/wfh-council/RESEARCH.md").read_text(encoding="utf-8").lower()

    assert "liquidity" in text
    assert "cascade" in text
    assert "relative weakness" in text
    assert "regime" in text
    assert "attention" in text


def test_research_snapshot_rejects_boolean_outcome_integrity_counts(tmp_path: Path) -> None:
    _write_promotion_candidate_artifacts(tmp_path)
    outcome_path = tmp_path / "OUTCOME_INTEGRITY.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    outcome["causal_entry_before_observation_count"] = False
    outcome["duplicate_snapshot_ids"] = False
    _write_json(outcome_path, outcome)

    summary = council.summarize_research_evidence(tmp_path)

    assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
    assert "causal_outcome_integrity_failed" in summary["blockers"]
    assert "duplicate_outcome_snapshots" in summary["blockers"]


def test_council_v2_requires_skill_system_curator_and_audit_route() -> None:
    manifest = council.load_manifest(MANIFEST)
    roles = {role["id"]: role for role in manifest["roles"]}

    assert manifest["contract_version"] == "wfh_agent_council_v2"
    assert roles["skill_system_curator"]["skills"] == ["skill-system-curator"]
    assert roles["adversarial_prompt_tester"]["production_authority"] is False
    assert manifest["routes"]["skill_system_audit"] == [
        "chief_orchestrator",
        "capability_scout",
        "skill_system_curator",
        "adversarial_prompt_tester",
        "regression_lead",
    ]


def test_council_v2_capability_authority_is_explicit() -> None:
    manifest = council.load_manifest(MANIFEST)
    capabilities = manifest["capabilities"]

    assert capabilities["github_connector"]["authority"] == "READ_WRITE_REPO"
    assert capabilities["github_connector"]["production_mutation"] is False
    assert capabilities["remote_desktop_commander_mcp"]["authority"] == "AUTHORIZED_WRITE"
    assert capabilities["remote_desktop_commander_mcp"]["production_mutation"] is False


def test_council_v2_rejects_capability_with_production_mutation() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["capabilities"]["github_connector"]["production_mutation"] = True

    errors = council.validate_manifest(REPO, broken)

    assert any("production mutation" in error.lower() for error in errors)


def test_doctor_reports_external_capability_authorization_without_guessing(monkeypatch) -> None:
    monkeypatch.setattr(council, "_git_output", lambda repo_root, *args: "abc" if args == ("rev-parse", "HEAD") else "branch")
    monkeypatch.setattr(council.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None)

    result = council.doctor(
        REPO,
        capability_statuses={
            "github_connector": "AUTHORIZED_WRITE",
            "remote_desktop_commander_mcp": "AUTHORIZED_WRITE",
            "web_research": "AUTHORIZED_READ",
        },
    )

    assert result["capabilities"]["github_connector"]["status"] == "AUTHORIZED_WRITE"
    assert result["capabilities"]["remote_desktop_commander_mcp"]["status"] == "AUTHORIZED_WRITE"
    assert result["capabilities"]["web_research"]["status"] == "AUTHORIZED_READ"
    assert result["capabilities"]["coderabbit"]["status"] == "UNAVAILABLE"


def test_council_v2_capabilities_cover_declared_tools() -> None:
    manifest = council.load_manifest(MANIFEST)
    declared = set(manifest["tools"]["required"]) | set(manifest["tools"]["optional"])
    assert declared <= set(manifest["capabilities"])


def test_council_v2_rejects_declared_tool_without_capability_record() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    del broken["capabilities"]["grafana"]

    errors = council.validate_manifest(REPO, broken)

    assert any("grafana" in error and "capability" in error.lower() for error in errors)


def test_doctor_cli_accepts_explicit_connected_capability_status(monkeypatch, capsys) -> None:
    def fake_git_output(repo_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        if args == ("branch", "--show-current"):
            return "test-branch"
        raise AssertionError(args)

    monkeypatch.setattr(council, "_git_output", fake_git_output)
    monkeypatch.setattr(
        council.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None,
    )

    rc = council.main([
        "--repo-root", str(REPO),
        "doctor",
        "--capability", "github_connector=AUTHORIZED_WRITE",
        "--capability", "web_research=AUTHORIZED_READ",
        "--json",
    ])
    output = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert output["capabilities"]["github_connector"]["status"] == "AUTHORIZED_WRITE"
    assert output["capabilities"]["web_research"]["status"] == "AUTHORIZED_READ"


def test_skill_system_audit_route_invokes_capability_scout() -> None:
    manifest = council.load_manifest(MANIFEST)
    route = manifest["routes"]["skill_system_audit"]
    assert "capability_scout" in route
    assert route.index("capability_scout") < route.index("adversarial_prompt_tester")


def test_external_research_route_invokes_research_librarian() -> None:
    manifest = council.load_manifest(MANIFEST)
    assert "external_research" in manifest["routes"]
    route = manifest["routes"]["external_research"]
    assert "research_librarian" in route
    assert route.index("research_librarian") < route.index("quant_validation_lead")


def test_manifest_rejects_unreachable_role() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["routes"] = {
        name: [role for role in route if role != "capability_scout"]
        for name, route in broken["routes"].items()
    }
    errors = council.validate_manifest(REPO, broken)
    assert any("capability_scout" in error and "unreachable" in error.lower() for error in errors)


def test_doctor_reports_every_declared_capability(monkeypatch) -> None:
    manifest = council.load_manifest(MANIFEST)
    monkeypatch.setattr(council, "_git_output", lambda repo_root, *args: "abc" if args == ("rev-parse", "HEAD") else "branch")
    monkeypatch.setattr(council.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None)
    result = council.doctor(
        REPO,
        manifest=manifest,
        capability_statuses={"playwright": "AVAILABLE", "pytest": "AVAILABLE"},
    )
    assert set(result["capabilities"]) == set(manifest["capabilities"])
    assert result["capabilities"]["playwright"]["status"] == "AVAILABLE"
    assert result["capabilities"]["pytest"]["status"] == "AVAILABLE"


def test_doctor_honors_manifest_required_flags(monkeypatch) -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["capabilities"]["prometheus"]["required"] = True
    monkeypatch.setattr(council, "_git_output", lambda repo_root, *args: "abc" if args == ("rev-parse", "HEAD") else "branch")
    monkeypatch.setattr(council.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None)
    result = council.doctor(REPO, manifest=broken)
    assert result["status"] == "BLOCKED"
    assert "prometheus" in result["missing_required"]


def test_doctor_rejects_authority_escalation(monkeypatch) -> None:
    manifest = council.load_manifest(MANIFEST)
    monkeypatch.setattr(council, "_git_output", lambda repo_root, *args: "abc" if args == ("rev-parse", "HEAD") else "branch")
    monkeypatch.setattr(council.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None)
    try:
        council.doctor(
            REPO,
            manifest=manifest,
            capability_statuses={"web_research": "AUTHORIZED_WRITE"},
        )
    except ValueError as exc:
        assert "web_research" in str(exc)
        assert "authority" in str(exc).lower()
    else:
        raise AssertionError("doctor must reject capability authority escalation")


def test_project_router_exports_every_council_route() -> None:
    manifest = council.load_manifest(MANIFEST)
    router = (REPO / "docs/chatgpt-project/00-WFH-CHATGPT-ROUTER-v2.md").read_text(encoding="utf-8")
    for route_id, roles in manifest["routes"].items():
        expected = f"- `{route_id}`: `{' → '.join(roles)}`"
        assert expected in router


def test_manifest_requires_canonical_skill_system_curator_role() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["roles"] = [role for role in broken["roles"] if role["id"] != "skill_system_curator"]
    broken["routes"]["skill_system_audit"] = [role for role in broken["routes"]["skill_system_audit"] if role != "skill_system_curator"]
    errors = council.validate_manifest(REPO, broken)
    assert any("skill_system_curator" in error and "required" in error.lower() for error in errors)


def test_manifest_requires_canonical_skill_system_audit_route() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    del broken["routes"]["skill_system_audit"]
    errors = council.validate_manifest(REPO, broken)
    assert any("skill_system_audit" in error and "required" in error.lower() for error in errors)


def test_manifest_rejects_non_list_tool_inventory() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["tools"]["required"] = "git"
    errors = council.validate_manifest(REPO, broken)
    assert any("tools.required" in error and "list" in error.lower() for error in errors)


def test_manifest_rejects_non_string_tool_inventory_entry() -> None:
    manifest = council.load_manifest(MANIFEST)
    broken = copy.deepcopy(manifest)
    broken["tools"]["optional"] = [*broken["tools"]["optional"], 42]
    errors = council.validate_manifest(REPO, broken)
    assert any("tools.optional" in error and "string" in error.lower() for error in errors)


def test_doctor_cli_rejects_unknown_capability_name(monkeypatch, capsys) -> None:
    monkeypatch.setattr(council, "_git_output", lambda repo_root, *args: "abc" if args == ("rev-parse", "HEAD") else "branch")
    monkeypatch.setattr(council.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "python3"} else None)
    rc = council.main([
        "--repo-root", str(REPO), "doctor",
        "--capability", "typo=AUTHORIZED_READ", "--json",
    ])
    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["status"] == "INVALID"
    assert "unknown capability" in output["error"]
