#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

DELETE = "DELETE_AFTER_CERTIFICATION"
KEEP = "KEEP"
PROTECTED = "PROTECTED"
REVIEW = "REVIEW"

DOCKER_BIN = "/usr/bin/docker"
PRODUCTION_PROJECT_DIR = Path("/srv/waterfallhunter/app")
PRODUCTION_ENV_FILE = Path("/etc/waterfallhunter/waterfallhunter.env")
PRODUCTION_OVERRIDE = Path("/srv/waterfallhunter/runtime/production-volumes.override.yml")
PRODUCTION_COMPOSE_PROJECT = "waterfallhunter"
WFH_OCI_SOURCE = "https://github.com/cavack/WFH-ORG"

CANONICAL_PATHS = {
    Path("/srv/waterfallhunter"),
    Path("/srv/waterfallhunter/app"),
    Path("/srv/waterfallhunter/data"),
    Path("/srv/waterfallhunter/backups"),
    Path("/srv/waterfallhunter/runtime"),
    Path("/etc/waterfallhunter"),
    Path("/etc/waterfallhunter/waterfallhunter.env"),
}
PROTECTED_PREFIXES = (
    Path("/root/.codex"), Path("/root/.vscode-server"),
    Path("/root/.vscode-server-insiders"), Path("/root/.claude"),
    Path("/root/.gemini"), Path("/root/.ssh"), Path("/home"),
)
EXACT_LEGACY_PATHS = {
    Path("/srv/wfh-worktrees"), Path("/srv/wfh-loq-dev"),
    Path("/srv/wfh-releases"), Path("/srv/wfh-release-backups"),
    Path("/srv/wfh-operator-scripts"), Path("/srv/wfh-agent-package.zip"),
    Path("/srv/waterfallhunter_backup.tar.gz"), Path("/root/github/wfh"),
    Path("/root/github/wfh-quarantine"), Path("/root/github/wfh-rewrite.git"),
    Path("/root/github/wfh-verify"), Path("/root/github/wfh-backup.git"),
}
LEGACY_ROOT_GLOBS = (
    "/root/wfh-*", "/root/codex-wfh-*", "/root/pr*-wfh-*",
)


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def classify_path(path: Path) -> tuple[str, str]:
    path = Path(os.path.abspath(os.fspath(path)))
    if path in CANONICAL_PATHS:
        return KEEP, "canonical WaterfallHunter path"
    for protected in PROTECTED_PREFIXES:
        if path == protected or _within(path, protected):
            return PROTECTED, "general host/developer tooling is outside cleanup scope"
    if path in EXACT_LEGACY_PATHS:
        return DELETE, "known legacy WaterfallHunter artifact"
    if path.parent == Path("/root") and (path.name.startswith("wfh-") or path.name.startswith("codex-wfh-")):
        return DELETE, "known legacy WaterfallHunter root artifact"
    if path.parent == Path("/srv") and path.name.startswith("wfh-"):
        return DELETE, "known legacy WaterfallHunter srv artifact"
    return REVIEW, "not on canonical cleanup allowlist"


_CANONICAL_DOCKER_NAMES = {
    "container": {"waterfall-backend", "waterfall-frontend", "waterfall-watchdog", "waterfall-prometheus", "waterfall-grafana"},
    "volume": {"waterfallhunter_data", "waterfallhunter_prometheus_data", "waterfallhunter_grafana_data", "waterfallhunter_alertmanager_data"},
    "network": {"waterfallhunter_edge", "waterfallhunter_application", "waterfallhunter_egress", "waterfallhunter_alerting"},
    "image": {
        "waterfallhunter-waterfall-backend:latest",
        "waterfallhunter-frontend:latest",
        "waterfallhunter-watchdog:latest",
    },
}
_CANONICAL_DOCKER_SERVICES = {
    "waterfall-backend", "frontend", "watchdog", "prometheus", "grafana", "alertmanager",
}


def _is_canonical_docker_resource(kind: str, name: str, labels: dict[str, str]) -> bool:
    project = labels.get("com.docker.compose.project", "")
    service = labels.get("com.docker.compose.service", "")
    named = name in _CANONICAL_DOCKER_NAMES.get(kind, set())
    if named and (kind == "image" or project == "waterfallhunter"):
        return True
    return (
        kind == "container"
        and project == "waterfallhunter"
        and service in _CANONICAL_DOCKER_SERVICES
    )


def _is_wfh_docker_resource(kind: str, name: str, labels: dict[str, str]) -> bool:
    project = labels.get("com.docker.compose.project", "")
    if kind == "image" and labels.get("org.opencontainers.image.source") == WFH_OCI_SOURCE:
        return True
    return (
        name.startswith(("waterfall", "wfh-"))
        or "waterfallhunter" in name.lower()
        or project.startswith("waterfall")
        or bool(re.fullmatch(r"[0-9a-f]{40}", project))
        or (kind == "image" and name.startswith(("wfh-", "waterfallhunter-")))
    )


def classify_docker_resource(
    kind: str,
    name: str,
    labels: dict[str, str] | None = None,
    *,
    protected_volume_names: set[str] | None = None,
    protected_network_names: set[str] | None = None,
) -> tuple[str, str]:
    labels = labels or {}
    if kind == "volume" and name in (protected_volume_names or set()):
        return KEEP, "volume referenced by active Production Compose topology"
    if kind == "network" and name in (protected_network_names or set()):
        return KEEP, "network referenced by active Production Compose topology"
    if _is_canonical_docker_resource(kind, name, labels):
        return KEEP, "canonical WaterfallHunter Docker resource"
    if _is_wfh_docker_resource(kind, name, labels):
        return DELETE, "legacy/orphan WaterfallHunter Docker resource"
    return REVIEW, "Docker resource not proven WaterfallHunter-owned"


def _size_bytes(path: Path) -> int:
    try:
        result = subprocess.run(["du", "-sb", str(path)], text=True, capture_output=True, check=False)
        if result.returncode == 0:
            return int(result.stdout.split()[0])
    except Exception:
        pass
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _path_entries() -> list[dict[str, object]]:
    candidates = set(EXACT_LEGACY_PATHS) | CANONICAL_PATHS
    for pattern in LEGACY_ROOT_GLOBS:
        candidates.update(Path("/").glob(pattern.lstrip("/")))
    entries: list[dict[str, object]] = []
    for path in sorted(candidates, key=lambda p: str(p)):
        if not path.exists() and not path.is_symlink():
            continue
        disposition, reason = classify_path(path)
        try:
            mtime = path.lstat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append({
            "type": "path", "path_or_resource": str(path),
            "size_bytes": _size_bytes(path), "mtime": mtime,
            "disposition": disposition, "reason": reason,
        })
    return entries


def _production_compose_command(base: Path) -> list[str]:
    command = [
        DOCKER_BIN, "compose", "--project-name", PRODUCTION_COMPOSE_PROJECT,
        "--env-file", str(PRODUCTION_ENV_FILE), "-f", str(base),
    ]
    if PRODUCTION_OVERRIDE.is_file():
        command.extend(("-f", str(PRODUCTION_OVERRIDE)))
    command.extend(("config", "--no-env-resolution", "--format", "json"))
    return command


def _resolved_compose_resource_names(config: dict[str, object]) -> dict[str, set[str]]:
    resolved: dict[str, set[str]] = {"volume": set(), "network": set()}
    for kind, section_name in (("volume", "volumes"), ("network", "networks")):
        section = config.get(section_name)
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            spec = value if isinstance(value, dict) else {}
            explicit_name = spec.get("name")
            if isinstance(explicit_name, str) and explicit_name:
                resolved[kind].add(explicit_name)
            elif spec.get("external") is True:
                resolved[kind].add(str(key))
            else:
                resolved[kind].add(f"{PRODUCTION_COMPOSE_PROJECT}_{key}")
    return resolved


def _production_compose_resource_names() -> dict[str, set[str]]:
    """Resolve host-owned volumes/networks from the effective Production Compose model."""
    base = PRODUCTION_PROJECT_DIR / "docker-compose.yml"
    if not base.is_file() or not PRODUCTION_ENV_FILE.is_file():
        return {"volume": set(), "network": set()}
    environment = os.environ.copy()
    environment["WFH_ENV_FILE"] = str(PRODUCTION_ENV_FILE)
    result = subprocess.run(
        _production_compose_command(base),
        cwd=PRODUCTION_PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError("unable to resolve active Production Compose topology")
    try:
        config = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("invalid Production Compose topology output") from exc
    if not isinstance(config, dict):
        raise RuntimeError("invalid Production Compose topology object")
    return _resolved_compose_resource_names(config)


def _production_compose_volume_names() -> set[str]:
    return _production_compose_resource_names()["volume"]


def _docker_json(command: list[str]) -> list[dict[str, object]]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown Docker error").strip()[:500]
        raise RuntimeError(f"Docker inventory enumeration failed: {detail}")
    rows: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue
    return rows


def _docker_entries() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    protected_resources = _production_compose_resource_names()
    protected_volume_names = protected_resources["volume"]
    protected_network_names = protected_resources["network"]
    specs = {
        "container": ["docker", "ps", "-a", "--format", "{{json .}}"],
        "volume": ["docker", "volume", "ls", "--format", "{{json .}}"],
        "network": ["docker", "network", "ls", "--format", "{{json .}}"],
        "image": ["docker", "image", "ls", "--format", "{{json .}}"],
    }
    for kind, command in specs.items():
        for row in _docker_json(command):
            if kind == "image":
                repository = str(row.get("Repository") or "")
                tag = str(row.get("Tag") or "")
                image_id = str(row.get("ID") or "").removeprefix("sha256:")
                if repository and tag and repository != "<none>" and tag != "<none>":
                    name = f"{repository}:{tag}"
                elif re.fullmatch(r"[0-9a-f]{12,64}", image_id):
                    name = image_id
                else:
                    name = ""
            else:
                name = str(row.get("Names") or row.get("Name") or "")
            if not name:
                continue
            inspect = subprocess.run(["docker", f"{kind}", "inspect", name] if kind not in {"container", "image"} else ["docker", "image", "inspect", name] if kind == "image" else ["docker", "inspect", name], text=True, capture_output=True, check=False)
            labels: dict[str, str] = {}
            if inspect.returncode == 0:
                try:
                    obj = json.loads(inspect.stdout)[0]
                    if kind in {"container", "image"}:
                        labels = obj.get("Config", {}).get("Labels", {})
                    else:
                        labels = obj.get("Labels", {})
                    labels = labels or {}
                except (ValueError, IndexError, TypeError):
                    labels = {}
            disposition, reason = classify_docker_resource(
                kind, name, labels,
                protected_volume_names=protected_volume_names,
                protected_network_names=protected_network_names,
            )
            if disposition != REVIEW or "waterfall" in name.lower() or labels.get("com.docker.compose.project"):
                entries.append({
                    "type": f"docker-{kind}", "path_or_resource": name,
                    "size_bytes": 0, "mtime": 0.0, "disposition": disposition,
                    "reason": reason, "labels": labels,
                })
    return entries


def build_inventory() -> dict[str, object]:
    return {
        "inventory_version": "waterfallhunter_host_inventory_v1",
        "generated_at": time.time(),
        "entries": _path_entries() + _docker_entries(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory WaterfallHunter host artifacts without deleting anything.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = build_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for entry in inventory["entries"]:
        counts[entry["disposition"]] = counts.get(entry["disposition"], 0) + 1
    print(json.dumps({"output": str(args.output), "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
