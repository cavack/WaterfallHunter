#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_INVARIANTS = {
    "live_order_placement": "FORBIDDEN",
    "live_trading_enabled": False,
    "missing_evidence_directional_imputation": "FORBIDDEN",
    "entry_ready_minimum": 78.0,
    "forming_minimum": 55.0,
    "anti_chase_atr": 1.2,
    "persistence_before_notification": True,
    "immutable_signal_provenance": True,
    "frontend_decision_logic_authority": "BACKEND_ONLY",
}


EXPECTED_V2_SKILL_SYSTEM_ROUTE = [
    "chief_orchestrator",
    "capability_scout",
    "skill_system_curator",
    "adversarial_prompt_tester",
    "regression_lead",
]
EXPECTED_V2_ROLE_SKILLS = {
    "skill_system_curator": ["skill-system-curator"],
}


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Council manifest must be a JSON object")
    return value

def _role_index(manifest: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    roles = manifest.get("roles")
    if not isinstance(roles, list):
        errors.append("manifest roles must be a list")
        return {}

    index: dict[str, dict[str, Any]] = {}
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("id"), str):
            errors.append("every role must be an object with a string id")
            continue
        role_id = role["id"]
        if role_id in index:
            errors.append(f"duplicate role id: {role_id}")
            continue
        index[role_id] = role
    return index


def _validate_skills(repo_root: Path, roles: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for role_id, role in roles.items():
        skills = role.get("skills")
        if not isinstance(skills, list) or not skills:
            errors.append(f"role {role_id}: skills must be a non-empty list")
            continue
        for skill in skills:
            if not isinstance(skill, str):
                errors.append(f"role {role_id}: skill names must be strings")
                continue
            canonical = repo_root / "skills" / "waterfallhunter" / skill / "SKILL.md"
            adapter = repo_root / ".agents" / "skills" / skill / "SKILL.md"
            if not canonical.is_file():
                errors.append(f"role {role_id}: missing canonical skill {skill}")
            if not adapter.is_file():
                errors.append(f"role {role_id}: missing discovery adapter {skill}")
    return errors

def _validate_routes(manifest: dict[str, Any], roles: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    routes = manifest.get("routes")
    if not isinstance(routes, dict) or not routes:
        return ["manifest routes must be a non-empty object"]

    for route_name, route in routes.items():
        if not isinstance(route, list) or not route:
            errors.append(f"route {route_name}: must be a non-empty role list")
            continue
        for role_id in route:
            if role_id not in roles:
                errors.append(f"route {route_name}: unknown role {role_id}")
    return errors


def _validate_role_reachability(
    manifest: dict[str, Any], roles: dict[str, dict[str, Any]]
) -> list[str]:
    routes = manifest.get("routes")
    if not isinstance(routes, dict):
        return []
    referenced = {
        role_id
        for route in routes.values()
        if isinstance(route, list)
        for role_id in route
        if isinstance(role_id, str)
    }
    return [
        f"role {role_id}: unreachable because no Council route references it"
        for role_id in sorted(set(roles) - referenced)
    ]


def _validate_production_authority(
    manifest: dict[str, Any], roles: dict[str, dict[str, Any]]
) -> list[str]:
    authority = manifest.get("production_authority_role")
    if not isinstance(authority, str) or authority not in roles:
        return ["production_authority_role must name an existing role"]
    privileged = [role_id for role_id, role in roles.items() if role.get("production_authority") is True]
    if privileged != [authority]:
        return [
            "production authority must be exclusive to "
            f"{authority}; privileged roles={privileged}"
        ]
    return []


def _validate_invariants(manifest: dict[str, Any]) -> list[str]:
    invariants = manifest.get("protected_invariants")
    if not isinstance(invariants, dict):
        return ["protected_invariants must be an object"]
    errors: list[str] = []
    for key, expected in EXPECTED_INVARIANTS.items():
        if invariants.get(key) != expected:
            errors.append(f"protected invariant {key} must equal {expected!r}")
    return errors

def _validate_tool_inventory(manifest: dict[str, Any]) -> list[str]:
    tools = manifest.get("tools")
    if not isinstance(tools, dict):
        return ["manifest tools must be an object"]
    errors: list[str] = []
    for key in ("required", "optional"):
        values = tools.get(key)
        if not isinstance(values, list):
            errors.append(f"tools.{key} must be a list of strings")
            continue
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value:
                errors.append(f"tools.{key}[{index}] must be a non-empty string")
    return errors



def _declared_tool_ids(manifest: dict[str, Any]) -> list[str]:
    tools = manifest.get("tools")
    if not isinstance(tools, dict):
        return []
    declared: list[str] = []
    for key in ("required", "optional"):
        values = tools.get(key, [])
        if isinstance(values, list):
            declared.extend(value for value in values if isinstance(value, str))
    return declared


def _validate_capability_record(capability_id: str, capability: Any) -> list[str]:
    if not isinstance(capability, dict):
        return [f"capability {capability_id}: must be an object"]
    allowed_authority = {
        "AVAILABLE", "AUTHORIZED_READ", "AUTHORIZED_WRITE", "READ_WRITE_REPO",
        "UNAVAILABLE", "BLOCKED",
    }
    errors: list[str] = []
    if capability.get("authority") not in allowed_authority:
        errors.append(f"capability {capability_id}: invalid authority")
    if type(capability.get("required")) is not bool:
        errors.append(f"capability {capability_id}: required must be boolean")
    if capability.get("production_mutation") is not False:
        errors.append(
            f"capability {capability_id}: production mutation is forbidden in Council capability declarations"
        )
    if not isinstance(capability.get("evidence_role"), str) or not capability.get("evidence_role"):
        errors.append(f"capability {capability_id}: evidence_role must be a non-empty string")
    return errors


def _validate_capabilities(manifest: dict[str, Any]) -> list[str]:
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        return ["manifest capabilities must be a non-empty object"]
    errors = [
        f"declared tool {tool_id}: missing capability record"
        for tool_id in _declared_tool_ids(manifest)
        if tool_id not in capabilities
    ]
    for capability_id, capability in capabilities.items():
        errors.extend(_validate_capability_record(capability_id, capability))
    return errors


def _validate_v2_required_contract(
    manifest: dict[str, Any], roles: dict[str, dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    for role_id, expected_skills in EXPECTED_V2_ROLE_SKILLS.items():
        role = roles.get(role_id)
        if role is None:
            errors.append(f"required Council v2 role {role_id} is missing")
        elif role.get("skills") != expected_skills:
            errors.append(
                f"required Council v2 role {role_id} skills must equal {expected_skills}"
            )
    routes = manifest.get("routes")
    route = routes.get("skill_system_audit") if isinstance(routes, dict) else None
    if route != EXPECTED_V2_SKILL_SYSTEM_ROUTE:
        errors.append(
            "required Council v2 route skill_system_audit must match the canonical order"
        )
    return errors



def validate_manifest(repo_root: Path, manifest: dict[str, Any]) -> list[str]:
    repo_root = repo_root.resolve()
    errors: list[str] = []
    if manifest.get("contract_version") != "wfh_agent_council_v2":
        errors.append("contract_version must be wfh_agent_council_v2")

    roles = _role_index(manifest, errors)
    errors.extend(_validate_skills(repo_root, roles))
    errors.extend(_validate_routes(manifest, roles))
    errors.extend(_validate_role_reachability(manifest, roles))
    errors.extend(_validate_v2_required_contract(manifest, roles))
    errors.extend(_validate_production_authority(manifest, roles))
    errors.extend(_validate_invariants(manifest))
    errors.extend(_validate_tool_inventory(manifest))
    errors.extend(_validate_capabilities(manifest))

    routes = manifest.get("routes")
    model_route = routes.get("model_optimization", []) if isinstance(routes, dict) else []
    required = [
        "chief_orchestrator",
        "market_evidence_forensics",
        "strategy_owner",
        "quant_validation_lead",
        "false_positive_hunter",
        "false_negative_hunter",
        "regression_lead",
        "release_certifier",
    ]
    if model_route != required:
        errors.append("model_optimization route must match the canonical Council order")
    return errors

def route_task(manifest: dict[str, Any], task_type: str) -> list[dict[str, Any]]:
    routes = manifest.get("routes")
    if not isinstance(routes, dict) or task_type not in routes:
        raise KeyError(f"unknown Council route: {task_type}")

    role_map = {
        role["id"]: role
        for role in manifest.get("roles", [])
        if isinstance(role, dict) and isinstance(role.get("id"), str)
    }
    packets: list[dict[str, Any]] = []
    for role_id in routes[task_type]:
        role = role_map.get(role_id)
        if role is None:
            raise ValueError(f"route references unknown role: {role_id}")
        packets.append(
            {
                "role": role_id,
                "skills": list(role.get("skills", [])),
                "production_authority": role.get("production_authority") is True,
            }
        )
    return packets


def _git_output(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
    ).strip()

LOCAL_CAPABILITY_COMMANDS = {
    "git": "git",
    "python": "python3",
    "docker": "docker",
    "playwright": "playwright",
    "pytest": "pytest",
}
CAPABILITY_STATUSES = {
    "AVAILABLE", "AUTHORIZED_READ", "AUTHORIZED_WRITE", "UNAVAILABLE", "BLOCKED"
}
READY_CAPABILITY_STATUSES = {"AVAILABLE", "AUTHORIZED_READ", "AUTHORIZED_WRITE"}
AUTHORITY_ALLOWED_STATUSES = {
    "AVAILABLE": {"AVAILABLE", "UNAVAILABLE", "BLOCKED"},
    "AUTHORIZED_READ": {"AUTHORIZED_READ", "UNAVAILABLE", "BLOCKED"},
    "AUTHORIZED_WRITE": {"AUTHORIZED_READ", "AUTHORIZED_WRITE", "UNAVAILABLE", "BLOCKED"},
    "READ_WRITE_REPO": {"AUTHORIZED_READ", "AUTHORIZED_WRITE", "UNAVAILABLE", "BLOCKED"},
    "UNAVAILABLE": {"UNAVAILABLE", "BLOCKED"},
    "BLOCKED": {"UNAVAILABLE", "BLOCKED"},
}


def _doctor_manifest(repo_root: Path, manifest: dict[str, Any] | None) -> dict[str, Any]:
    if manifest is not None:
        return manifest
    return load_manifest(repo_root / ".agents" / "wfh-council" / "manifest.json")


def _validate_supplied_capability_statuses(
    manifest: dict[str, Any], supplied: dict[str, str]
) -> None:
    capabilities = manifest.get("capabilities", {})
    for capability_id, status in supplied.items():
        record = capabilities.get(capability_id)
        if not isinstance(record, dict):
            raise ValueError(f"unknown capability: {capability_id}")
        authority = record.get("authority")
        allowed = AUTHORITY_ALLOWED_STATUSES.get(str(authority), set())
        if status not in allowed:
            raise ValueError(
                f"capability {capability_id}: status {status} exceeds declared authority {authority}"
            )


def _detected_capability_status(capability_id: str) -> tuple[str, str | None]:
    command = LOCAL_CAPABILITY_COMMANDS.get(capability_id)
    if command is None:
        return "UNAVAILABLE", None
    path = shutil.which(command)
    return ("AVAILABLE" if path else "UNAVAILABLE"), path


def _capability_inventory(
    manifest: dict[str, Any], supplied: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None]]:
    capabilities: dict[str, dict[str, Any]] = {}
    paths: dict[str, str | None] = {}
    for capability_id, record in manifest["capabilities"].items():
        detected_status, path = _detected_capability_status(capability_id)
        status = supplied.get(capability_id, detected_status)
        capabilities[capability_id] = {
            "status": status,
            "required": record["required"],
            "declared_authority": record["authority"],
            "evidence_role": record["evidence_role"],
        }
        paths[capability_id] = path
    return capabilities, paths


def _tool_inventory(
    manifest: dict[str, Any],
    capabilities: dict[str, dict[str, Any]],
    paths: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    return {
        tool_id: {
            "status": capabilities[tool_id]["status"],
            "path": paths.get(tool_id),
        }
        for tool_id in _declared_tool_ids(manifest)
        if tool_id in capabilities
    }


def _repo_identity(repo_root: Path) -> tuple[str, str | None, str | None]:
    try:
        git_sha = _git_output(repo_root, "rev-parse", "HEAD")
        branch = _git_output(repo_root, "branch", "--show-current") or None
    except (OSError, subprocess.CalledProcessError):
        return "UNAVAILABLE", None, None
    return "AVAILABLE", git_sha, branch


def doctor(
    repo_root: Path,
    *,
    manifest: dict[str, Any] | None = None,
    capability_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    manifest = _doctor_manifest(repo_root, manifest)
    supplied = capability_statuses or {}
    _validate_supplied_capability_statuses(manifest, supplied)

    repo_status, git_sha, branch = _repo_identity(repo_root)
    capabilities, paths = _capability_inventory(manifest, supplied)
    tools = _tool_inventory(manifest, capabilities, paths)
    missing_required = sorted(
        capability_id
        for capability_id, record in capabilities.items()
        if record["required"] and record["status"] not in READY_CAPABILITY_STATUSES
    )
    ready = repo_status == "AVAILABLE" and not missing_required
    return {
        "contract_version": "wfh_council_doctor_v2",
        "status": "READY" if ready else "BLOCKED",
        "repo": {
            "status": repo_status,
            "path": str(repo_root),
            "git_sha": git_sha,
            "branch": branch,
        },
        "tools": tools,
        "capabilities": capabilities,
        "missing_required": missing_required,
    }


DATASET_AUDIT_ARTIFACT = "DATASET_AUDIT.json"
OOS_VALIDATION_ARTIFACT = "OOS_VALIDATION.json"
BEST_DEVELOPMENT_CONFIG_ARTIFACT = "BEST_DEVELOPMENT_CONFIG.json"
PRODUCTION_VS_CHALLENGERS_ARTIFACT = "PRODUCTION_VS_CHALLENGERS.json"
GATE_REJECTION_FUNNEL_ARTIFACT = "GATE_REJECTION_FUNNEL.json"
OUTCOME_INTEGRITY_ARTIFACT = "OUTCOME_INTEGRITY.json"

RESEARCH_ARTIFACTS = (
    DATASET_AUDIT_ARTIFACT,
    OOS_VALIDATION_ARTIFACT,
    BEST_DEVELOPMENT_CONFIG_ARTIFACT,
    PRODUCTION_VS_CHALLENGERS_ARTIFACT,
    GATE_REJECTION_FUNNEL_ARTIFACT,
    OUTCOME_INTEGRITY_ARTIFACT,
)

EXPECTED_RESEARCH_CONTRACTS = {
    DATASET_AUDIT_ARTIFACT: "DATASET_AUDIT.v1",
    OOS_VALIDATION_ARTIFACT: "OOS_VALIDATION.v1",
    BEST_DEVELOPMENT_CONFIG_ARTIFACT: "BEST_DEVELOPMENT_CONFIG.v1",
    PRODUCTION_VS_CHALLENGERS_ARTIFACT: "PRODUCTION_VS_CHALLENGERS.v1",
    GATE_REJECTION_FUNNEL_ARTIFACT: "GATE_REJECTION_FUNNEL.v1",
    OUTCOME_INTEGRITY_ARTIFACT: "OUTCOME_INTEGRITY.v1",
}


def _artifact_contract_blockers(
    values: dict[str, dict[str, Any] | None],
    artifact_status: dict[str, dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    for name, expected_contract in EXPECTED_RESEARCH_CONTRACTS.items():
        status = artifact_status.get(name, {})
        value = values.get(name) or {}
        if status.get("status") != "AVAILABLE":
            blockers.append(f"invalid_research_artifact:{name}")
        elif value.get("contract") != expected_contract:
            blockers.append(f"invalid_research_artifact_contract:{name}")
    return blockers


def _present_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _research_provenance_blockers(
    dataset: dict[str, Any],
    oos: dict[str, Any],
    outcome: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    dataset_hash = dataset.get("data_sha256")
    oos_dataset_hash = oos.get("dataset_sha256")
    oos_outcome_hash = oos.get("outcome_cache_sha256")
    outcome_hash = outcome.get("cache_sha256")

    if not _present_sha(dataset_hash):
        blockers.append("missing_dataset_provenance")
    if not _present_sha(oos_dataset_hash):
        blockers.append("missing_oos_dataset_provenance")
    elif _present_sha(dataset_hash) and oos_dataset_hash != dataset_hash:
        blockers.append("dataset_provenance_mismatch")
    if not _present_sha(oos_outcome_hash):
        blockers.append("missing_oos_outcome_provenance")
    if not _present_sha(outcome_hash):
        blockers.append("missing_outcome_cache_provenance")
    elif _present_sha(oos_outcome_hash) and outcome_hash != oos_outcome_hash:
        blockers.append("outcome_provenance_mismatch")
    return blockers


def _strict_zero_count(value: Any) -> bool:
    return type(value) is int and value == 0


def _research_semantic_integrity_blockers(
    dataset: dict[str, Any],
    outcome: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if not str(dataset.get("evidence_tier") or "").startswith("TIER_1"):
        blockers.append("insufficient_evidence_tier")
    if not _strict_zero_count(outcome.get("causal_entry_before_observation_count")):
        blockers.append("causal_outcome_integrity_failed")
    if not _strict_zero_count(outcome.get("duplicate_snapshot_ids")):
        blockers.append("duplicate_outcome_snapshots")
    return blockers


def _research_integrity_blockers(
    values: dict[str, dict[str, Any] | None],
    artifact_status: dict[str, dict[str, Any]],
) -> list[str]:
    dataset = values.get(DATASET_AUDIT_ARTIFACT) or {}
    oos = values.get(OOS_VALIDATION_ARTIFACT) or {}
    outcome = values.get(OUTCOME_INTEGRITY_ARTIFACT) or {}
    return [
        *_artifact_contract_blockers(values, artifact_status),
        *_research_provenance_blockers(dataset, oos, outcome),
        *_research_semantic_integrity_blockers(dataset, outcome),
    ]


def _read_research_artifact(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not path.is_file():
        return None, {"status": "MISSING_ARTIFACT"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, {"status": "INVALID_ARTIFACT", "error": str(exc)}
    if not isinstance(value, dict):
        return None, {"status": "INVALID_ARTIFACT", "error": "root must be a JSON object"}
    return value, {"status": "AVAILABLE", "contract": value.get("contract")}


def summarize_research_evidence(research_dir: Path) -> dict[str, Any]:
    research_dir = research_dir.resolve()
    values: dict[str, dict[str, Any] | None] = {}
    artifact_status: dict[str, dict[str, Any]] = {}
    for name in RESEARCH_ARTIFACTS:
        value, status = _read_research_artifact(research_dir / name)
        values[name] = value
        artifact_status[name] = status

    dataset = values[DATASET_AUDIT_ARTIFACT] or {}
    oos = values[OOS_VALIDATION_ARTIFACT] or {}
    champion = values[BEST_DEVELOPMENT_CONFIG_ARTIFACT] or {}
    comparison = values[PRODUCTION_VS_CHALLENGERS_ARTIFACT] or {}
    funnel = values[GATE_REJECTION_FUNNEL_ARTIFACT] or {}
    outcome = values[OUTCOME_INTEGRITY_ARTIFACT] or {}

    blockers = _research_integrity_blockers(values, artifact_status)
    span = dataset.get("span_days")
    if not isinstance(span, (int, float)) or span < 42.0:
        blockers.append("insufficient_promotion_span")

    accepted_oos = {"PASSED", "VALIDATED", "COMPLETE"}
    if oos.get("status") not in accepted_oos:
        blockers.append("insufficient_oos_evidence")

    if champion.get("promotion_allowed") is not True:
        blockers.append("no_scientific_champion")
    if comparison.get("promotion_allowed") is not True:
        blockers.append("production_promotion_not_allowed")
    if outcome.get("net_cost_adjusted_r_available") is not True:
        blockers.append("missing_net_cost_adjusted_r")

    disposition = "PROMOTION_EVIDENCE_CANDIDATE" if not blockers else "NO_PROMOTION_EVIDENCE"

    return {
        "contract_version": "wfh_council_research_snapshot_v1",
        "research_dir": str(research_dir),
        "promotion_disposition": disposition,
        "blockers": blockers,
        "artifacts": artifact_status,
        "dataset": _research_dataset_summary(dataset),
        "oos": _research_oos_summary(oos),
        "champion": _research_champion_summary(champion),
        "production_comparison": _research_comparison_summary(comparison),
        "gate_funnel": _research_funnel_summary(funnel),
        "outcome_integrity": _research_outcome_summary(outcome),
    }


def _select(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source.get(key) for key in keys if key in source}


def _research_dataset_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "evidence_tier", "data_sha256", "rows", "episodes", "symbols",
        "span_days", "fresh_production_like_rows", "production_like_episodes",
        "fresh_tier2_strict_rows", "tier2_strict_episodes", "limitations",
    )


def _research_oos_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "status", "reason", "dataset_sha256", "outcome_cache_sha256",
        "maximum_complete_outcomes_any_stage2_config", "span_days", "required_properties",
    )


def _research_champion_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "status", "promotion_allowed", "reasons",
        "maximum_complete_outcomes_any_stage2_config", "best_observed_safety_preserving_by_entry_count",
    )


def _research_comparison_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "production", "development_profiles", "promotion_allowed", "why_not",
    )


def _research_funnel_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "anti_chase_atr", "episodes_total", "episodes_with_prelate_row",
        "episodes_without_fresh_prelate_row", "episodes_ever_passing_gate_prelate",
        "episodes_reaching_threshold", "prelate_max_readiness", "most_common_best_row_failure_sets",
    )


def _research_outcome_summary(source: dict[str, Any]) -> dict[str, Any]:
    return _select(
        source,
        "contract", "cache_sha256", "label_contract", "rows", "outcome_complete",
        "unavailable", "unavailable_reasons", "outcomes", "net_cost_adjusted_r_available",
        "causal_entry_before_observation_count", "duplicate_snapshot_ids", "limitations",
    )



def build_snapshot(
    repo_root: Path,
    manifest: dict[str, Any],
    *,
    research_dir: Path | None = None,
    production_revision: str | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    repo = doctor(repo_root, manifest=manifest)["repo"]
    research = (
        summarize_research_evidence(research_dir)
        if research_dir is not None
        else {"promotion_disposition": "UNAVAILABLE", "artifacts": {}}
    )
    production_fact = {
        "classification": "UNVERIFIED_CLAIM" if production_revision else "UNAVAILABLE",
        "value": production_revision,
    }
    unknowns: list[str] = []
    if not production_revision:
        unknowns.append("production_revision")
    repo_classification = "VERIFIED_FACT" if repo.get("status") == "AVAILABLE" else "UNAVAILABLE"
    if repo_classification == "UNAVAILABLE":
        unknowns.append("repo_identity")
    return {
        "contract_version": "wfh_council_snapshot_v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repo": {**repo, "classification": repo_classification},
        "runtime": {
            "production_revision": production_fact,
            "live_trading_enabled": {"classification": "POLICY_ASSERTION", "value": False},
        },
        "research": research,
        "protected_invariants": manifest["protected_invariants"],
        "readiness": {
            "research_promotion_disposition": research.get("promotion_disposition", "UNAVAILABLE"),
            "repo_matches_claimed_production_revision": bool(
                production_revision and repo.get("git_sha") == production_revision
            ),
        },
        "unknowns": unknowns,
    }

def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    elif isinstance(value, list):
        for item in value:
            print(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}={item}")
    else:
        print(value)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_capability_statuses(values: list[str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"capability status must be NAME=STATUS: {value}")
        name, status = value.split("=", 1)
        name = name.strip()
        status = status.strip()
        if not name or status not in CAPABILITY_STATUSES:
            raise ValueError(f"invalid capability status: {value}")
        statuses[name] = status
    return statuses

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WaterfallHunter Senior Agent Council")
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    sub = parser.add_subparsers(dest="command", required=True)

    validate_cmd = sub.add_parser("validate", help="validate the Council contract")
    validate_cmd.add_argument("--json", action="store_true")

    route_cmd = sub.add_parser("route", help="resolve a Council route")
    route_cmd.add_argument("task_type")
    route_cmd.add_argument("--json", action="store_true")

    doctor_cmd = sub.add_parser("doctor", help="inspect local and explicitly supplied connected capabilities")
    doctor_cmd.add_argument("--capability", action="append", default=[], metavar="NAME=STATUS")
    doctor_cmd.add_argument("--json", action="store_true")

    research_cmd = sub.add_parser("research-snapshot", help="summarize existing research evidence")
    research_cmd.add_argument("--research-dir", type=Path, required=True)
    research_cmd.add_argument("--json", action="store_true")

    snapshot_cmd = sub.add_parser("snapshot", help="emit a safe repository/runtime/research snapshot")
    snapshot_cmd.add_argument("--research-dir", type=Path)
    snapshot_cmd.add_argument("--production-revision")
    snapshot_cmd.add_argument("--json", action="store_true")
    return parser


def _load_cli_manifest(repo_root: Path, as_json: bool) -> tuple[dict[str, Any] | None, int]:
    manifest_path = repo_root / ".agents" / "wfh-council" / "manifest.json"
    try:
        return load_manifest(manifest_path), 0
    except (OSError, ValueError) as exc:
        _emit({"status": "INVALID", "errors": [str(exc)]}, as_json)
        return None, 2


def _run_validate(repo_root: Path, manifest: dict[str, Any], args: argparse.Namespace) -> int:
    errors = validate_manifest(repo_root, manifest)
    _emit({"status": "VALID" if not errors else "INVALID", "errors": errors}, args.json)
    return 0 if not errors else 2


def _run_route(manifest: dict[str, Any], args: argparse.Namespace) -> int:
    try:
        route = route_task(manifest, args.task_type)
    except (KeyError, ValueError) as exc:
        _emit({"status": "INVALID", "error": str(exc)}, args.json)
        return 2
    _emit({"status": "OK", "task_type": args.task_type, "route": route}, args.json)
    return 0


def _run_doctor(repo_root: Path, manifest: dict[str, Any], args: argparse.Namespace) -> int:
    try:
        capability_statuses = _parse_capability_statuses(args.capability)
    except ValueError as exc:
        _emit({"status": "INVALID", "error": str(exc)}, args.json)
        return 2
    try:
        result = doctor(
            repo_root, manifest=manifest, capability_statuses=capability_statuses
        )
    except ValueError as exc:
        _emit({"status": "INVALID", "error": str(exc)}, args.json)
        return 2
    _emit(result, args.json)
    return 0 if result["status"] == "READY" else 3


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    manifest, error_code = _load_cli_manifest(repo_root, getattr(args, "json", False))
    if manifest is None:
        return error_code
    if args.command == "validate":
        return _run_validate(repo_root, manifest, args)
    if args.command == "route":
        return _run_route(manifest, args)
    if args.command == "doctor":
        return _run_doctor(repo_root, manifest, args)
    if args.command == "research-snapshot":
        _emit(summarize_research_evidence(args.research_dir), args.json)
        return 0
    if args.command == "snapshot":
        _emit(
            build_snapshot(
                repo_root, manifest, research_dir=args.research_dir,
                production_revision=args.production_revision,
            ),
            args.json,
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
