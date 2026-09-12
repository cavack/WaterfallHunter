#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPOSITORY_ROOT / "backend" / "src"))
from audit_host_inventory import (  # noqa: E402
    DELETE,
    EXACT_LEGACY_PATHS,
    PROTECTED,
    _production_compose_resource_names,
    classify_docker_resource,
    classify_path,
)
from waterfallhunter.core.signal_metadata import canonical_sha256  # noqa: E402
from waterfallhunter.core.sqlite_backup_certification import (  # noqa: E402
    BackupCertificationError,
    audit_sqlite_snapshot,
)
from verify_production_cutover import (  # noqa: E402
    CANONICAL_ENV_FILE as PRODUCTION_ENV_FILE,
    CANONICAL_PROJECT_DIR as PRODUCTION_PROJECT_DIR,
    release_evidence_snapshot,
)

DOCKER_BIN = "/usr/bin/docker"
CANONICAL_RUNTIME_DIR = Path("/srv/waterfallhunter/runtime")
MAX_RELEASE_CERTIFICATE_AGE_SECONDS = 900
CERTIFIED_BACKUP_ROOTS = (
    Path("/srv/waterfallhunter/backups"),
    Path("/srv/wfh-release-backups"),
)
_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_IMAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}$")
_EXACT_LEGACY_BY_TEXT = {str(path): path for path in EXACT_LEGACY_PATHS}


def _validated_input_file(path: Path) -> Path:
    candidate = Path(path)
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or candidate.resolve(strict=False) != candidate
        or not candidate.is_file()
    ):
        raise ValueError(f"input must be a canonical absolute regular file: {candidate}")
    return candidate


def _load(path: Path) -> dict[str, object]:
    safe_path = _validated_input_file(path)
    # safe_path is canonical, absolute, non-symlinked, and a regular file.
    data = json.loads(safe_path.read_text(encoding="utf-8"))  # NOSONAR
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return data


def validate_release_certificate(path: Path) -> dict[str, object]:
    data = _load(path)
    sha = str(data.get("release_sha", ""))
    claimed = str(data.get("certificate_sha256", ""))
    material = {key: value for key, value in data.items() if key != "certificate_sha256"}
    evidence = data.get("evidence")
    core_revisions = evidence.get("core_revisions") if isinstance(evidence, dict) else None
    expected_core = {"waterfall-backend", "waterfall-frontend", "waterfall-watchdog"}
    if not (
        data.get("certificate_type") == "waterfallhunter_release_v1"
        and data.get("status") == "PASS"
        and _lower_hex(sha, 40)
        and _lower_hex(claimed, 64)
        and claimed == canonical_sha256(material)
        and data.get("production_healthy") is True
        and data.get("live_trading_enabled") is False
        and isinstance(evidence, dict)
        and evidence.get("healthy") is True
        and evidence.get("running_revision") == sha
        and evidence.get("checkout_revision") == sha
        and evidence.get("live_trading_enabled") is False
        and isinstance(core_revisions, dict)
        and set(core_revisions) == expected_core
        and all(revision == sha for revision in core_revisions.values())
    ):
        raise ValueError("release certificate is not a complete passing WaterfallHunter release certificate")
    return data


def _revalidate_release_certificate_for_cleanup(release: dict[str, object]) -> None:
    certified_at = release.get("certified_at")
    if isinstance(certified_at, bool) or not isinstance(certified_at, (int, float)):
        raise ValueError("release certificate certified_at is invalid")
    age = time.time() - float(certified_at)
    if age < 0 or age > MAX_RELEASE_CERTIFICATE_AGE_SECONDS:
        raise ValueError("release certificate is outside the cleanup authorization window")
    release_sha = str(release.get("release_sha") or "")
    snapshot = release_evidence_snapshot(PRODUCTION_PROJECT_DIR, PRODUCTION_ENV_FILE)
    core_revisions = snapshot.get("core_revisions")
    if not (
        snapshot.get("healthy") is True
        and snapshot.get("running_revision") == release_sha
        and snapshot.get("checkout_revision") == release_sha
        and snapshot.get("live_trading_enabled") is False
        and snapshot.get("notification_delivery_ready") is True
        and isinstance(core_revisions, dict)
        and set(core_revisions) == {"waterfall-backend", "waterfall-frontend", "waterfall-watchdog"}
        and all(revision == release_sha for revision in core_revisions.values())
    ):
        raise ValueError("release certificate no longer matches the healthy canonical runtime")


def _lower_hex(value: object, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(character in "0123456789abcdef" for character in text)


def validate_db_certificate(path: Path) -> dict[str, object]:
    data = _load(path)
    digest = str(data.get("sha256", ""))
    source_revision = str(data.get("source_revision", ""))
    claimed_certificate_hash = str(data.get("certificate_sha256", ""))
    material = {key: value for key, value in data.items() if key != "certificate_sha256"}
    wrapped = data.get("sqlite_backup_certification")
    if not isinstance(wrapped, dict):
        raise ValueError("database certificate core certification missing")
    backup_audit = wrapped.get("backup_audit")
    if not isinstance(backup_audit, dict):
        raise ValueError("database certificate backup audit missing")
    wrapped_hash = str(wrapped.get("certification_sha256", ""))
    wrapped_material = {key: value for key, value in wrapped.items() if key != "certification_sha256"}
    backup_path = _canonical_certified_backup_path(data.get("backup_path"))
    if not (
        data.get("certificate_type") == "waterfallhunter_db_backup_v1"
        and data.get("status") == "PASS"
        and _lower_hex(digest, 64)
        and _lower_hex(source_revision, 40)
        and _lower_hex(claimed_certificate_hash, 64)
        and claimed_certificate_hash == canonical_sha256(material)
        and data.get("device_separation_enforced") is False
        and data.get("independent_disaster_recovery") is False
        and data.get("source_volume_preserved_until_post_cutover") is True
        and str(data.get("integrity_check", "")).lower() == "ok"
        and backup_path.is_absolute()
        and wrapped.get("contract_version") == "sqlite_backup_certification_v1"
        and wrapped.get("status") == "BACKUP_RESTORE_CERTIFIED"
        and wrapped.get("restore_matches_backup") is True
        and wrapped.get("production_migration_authorized") is False
        and wrapped.get("production_deployment_authorized") is False
        and _lower_hex(wrapped_hash, 64)
        and wrapped_hash == canonical_sha256(wrapped_material)
        and str(wrapped.get("backup_path") or "") == str(backup_path)
        and str(backup_audit.get("file_sha256") or "") == digest
        and str(backup_audit.get("integrity_check") or "").lower() == "ok"
    ):
        raise ValueError("database certificate is not a complete passing WaterfallHunter backup certificate")
    try:
        current_audit = audit_sqlite_snapshot(backup_path)  # NOSONAR -- canonical non-symlinked certified file
    except (BackupCertificationError, OSError) as exc:
        raise ValueError("certified database backup is unreadable") from exc
    if (
        current_audit.get("audit_sha256") != backup_audit.get("audit_sha256")
        or current_audit.get("file_sha256") != digest
    ):
        raise ValueError("certified database backup no longer matches its certificate")
    return data


def _has_symlink_ancestor(path: Path) -> bool:
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _validated_legacy_path(value: str) -> Path:
    if "\x00" in value:
        raise ValueError("cleanup path contains NUL")
    candidate = Path(value)
    normalized = Path(os.path.abspath(os.fspath(candidate)))
    if not candidate.is_absolute() or candidate != normalized:
        raise ValueError(f"cleanup path is not canonical: {value}")
    disposition, reason = classify_path(candidate)
    if disposition == PROTECTED:
        raise ValueError(f"protected cleanup path refused: {value}: {reason}")
    if disposition != DELETE:
        raise ValueError(f"cleanup path is not allowlisted: {value}: {reason}")
    exact = _EXACT_LEGACY_BY_TEXT.get(value)
    if exact is not None:
        safe = exact
    elif candidate.parent in {Path("/root"), Path("/srv")} and _SAFE_BASENAME.fullmatch(candidate.name):
        safe = candidate.parent / candidate.name
    else:
        raise ValueError(f"cleanup path is outside the destructive allowlist: {value}")
    if _has_symlink_ancestor(safe):
        raise ValueError(f"cleanup path has a symlink ancestor: {safe}")
    return safe


def _docker_volume_mountpoint(name: str) -> Path:
    safe_name = str(name or "").strip()
    if not _SAFE_BASENAME.fullmatch(safe_name) or safe_name.startswith("-"):
        raise ValueError("invalid Docker volume name")
    result = subprocess.run(
        [DOCKER_BIN, "volume", "inspect", "--format", "{{.Mountpoint}}", safe_name],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"unable to inspect Docker volume mountpoint: {safe_name}")
    mountpoint = Path(result.stdout.strip())
    if not mountpoint.is_absolute() or mountpoint.is_symlink() or not mountpoint.is_dir():
        raise ValueError("Docker volume mountpoint is not a canonical directory")
    return mountpoint


def _database_source_paths_for_cleanup_target(entry: dict[str, object]) -> list[Path]:
    kind = str(entry.get("type") or "")
    name = str(entry.get("path_or_resource") or "")
    candidates: list[Path] = []
    if kind == "docker-volume":
        mountpoint = _docker_volume_mountpoint(name)
        candidates.append(mountpoint / "waterfall_registry.db")
    elif kind == "path":
        target = _validated_legacy_path(name)
        if target.is_file() and target.name == "waterfall_registry.db":
            candidates.append(target)
        elif target.is_dir():
            candidates.extend((target / "waterfall_registry.db", target / "data" / "waterfall_registry.db"))
    sources = [
        candidate.resolve()
        for candidate in candidates
        if candidate.is_file() and not candidate.is_symlink() and not _has_symlink_ancestor(candidate)
    ]
    if len(sources) > 1:
        raise ValueError("cleanup target contains multiple database sources")
    return sources


def _assert_database_target_certificate_binding(
    entry: dict[str, object], db_certificate: dict[str, object]
) -> None:
    sources = _database_source_paths_for_cleanup_target(entry)
    if not sources:
        return
    core = db_certificate.get("sqlite_backup_certification")
    if not isinstance(core, dict):
        raise ValueError("database certificate source binding is missing")
    source_path = Path(str(core.get("source_path") or ""))
    source_identity = core.get("source_identity")
    if not source_path.is_absolute() or not isinstance(source_identity, dict):
        raise ValueError("database certificate source binding is invalid")
    actual_source = sources[0]
    stat = actual_source.stat()
    current_identity = {"device_id": int(stat.st_dev), "inode": int(stat.st_ino)}
    if source_path.resolve(strict=False) != actual_source or source_identity != current_identity:
        raise ValueError("database certificate does not bind the cleanup target database")


def _docker_inspect_command(resource_kind: str, safe_name: str) -> list[str]:
    if resource_kind == "container":
        return [DOCKER_BIN, "inspect", safe_name]
    if resource_kind == "image":
        return [DOCKER_BIN, "image", "inspect", safe_name]
    return [DOCKER_BIN, resource_kind, "inspect", safe_name]


def _docker_inspect_labels(resource_kind: str, obj: object) -> dict[str, str]:
    if not isinstance(obj, dict):
        raise RuntimeError("Docker inspect returned invalid object")
    labels = (
        obj.get("Config", {}).get("Labels", {})
        if resource_kind in {"container", "image"}
        else obj.get("Labels", {})
    )
    if labels is None:
        return {}
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in labels.items()
    ):
        raise RuntimeError("Docker labels are invalid")
    return dict(labels)


def _current_docker_labels(kind: str, name: str) -> dict[str, str] | None:
    resource_kind = str(kind or "").strip()
    safe_name = str(name or "").strip()
    if resource_kind not in {"container", "volume", "network", "image"}:
        raise ValueError("unsupported Docker resource kind")
    if not safe_name or safe_name.startswith("-"):
        raise ValueError("invalid Docker resource name")
    result = subprocess.run(
        _docker_inspect_command(resource_kind, safe_name),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        combined = result.stderr + result.stdout
        if "No such" in combined or "not found" in combined.lower():
            return None
        raise RuntimeError(f"Docker inspect failed for {resource_kind} {safe_name}")
    try:
        obj = json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError) as exc:
        raise RuntimeError("Docker inspect returned invalid JSON") from exc
    return _docker_inspect_labels(resource_kind, obj)


def _validated_docker_resource_name(
    kind: str, value: str, *,
    labels: dict[str, str] | None = None,
    protected_volume_names: set[str] | None = None,
    protected_network_names: set[str] | None = None,
) -> str:
    pattern = _SAFE_IMAGE_NAME if kind == "image" else _SAFE_BASENAME
    if not pattern.fullmatch(value) or value.startswith("-"):
        raise ValueError(f"invalid Docker {kind} resource name")
    disposition, reason = classify_docker_resource(
        kind, value, labels or {},
        protected_volume_names=protected_volume_names,
        protected_network_names=protected_network_names,
    )
    if disposition != DELETE:
        raise ValueError(f"Docker cleanup target is not allowlisted: {value}: {reason}")
    return value


def _cleanup_certificate_output(path: Path) -> Path:
    candidate = Path(path)
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or candidate.resolve(strict=False) != candidate
        or candidate.parent != CANONICAL_RUNTIME_DIR
        or not _SAFE_BASENAME.fullmatch(candidate.name)
        or not candidate.name.endswith(".json")
    ):
        raise ValueError("cleanup certificate output must be a canonical JSON file in the runtime directory")
    return CANONICAL_RUNTIME_DIR / candidate.name


def _canonical_certified_backup_path(value: object) -> Path:
    text = str(value or "")
    candidate = Path(text)
    if (
        "\x00" in text
        or not candidate.is_absolute()
        or str(candidate) != text
        or any(part in {".", ".."} for part in candidate.parts)
    ):
        raise ValueError("certified database backup path is not canonical")
    allowed_root = next(
        (root for root in CERTIFIED_BACKUP_ROOTS if candidate != root and root in candidate.parents),
        None,
    )
    if allowed_root is None:
        raise ValueError("certified database backup path is outside configured backup roots")
    relative_parts = candidate.relative_to(allowed_root).parts
    if not relative_parts or any(not _SAFE_BASENAME.fullmatch(part) for part in relative_parts):
        raise ValueError("certified database backup path contains an invalid component")
    safe_candidate = allowed_root.joinpath(*relative_parts)
    if safe_candidate.is_symlink() or _has_symlink_ancestor(safe_candidate):  # NOSONAR -- fixed roots + sanitized components
        raise ValueError("certified database backup path must not traverse symlinks")
    return safe_candidate


def _target_contains_certified_backup(target: Path, backup: Path) -> bool:
    return target == backup or target in backup.parents


def _validated_delete_entries(
    inventory: dict[str, object], *,
    protected_volume_names: set[str] | None = None,
    protected_network_names: set[str] | None = None,
) -> list[dict[str, object]]:
    entries = inventory.get("entries")
    if protected_volume_names is None or protected_network_names is None:
        protected = _production_compose_resource_names()
        protected_volume_names = protected["volume"]
        protected_network_names = protected["network"]
    if not isinstance(entries, list):
        raise ValueError("inventory entries missing")
    approved: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("disposition") != DELETE:
            continue
        kind = str(entry.get("type", ""))
        name = str(entry.get("path_or_resource", ""))
        if kind == "path":
            safe_path = _validated_legacy_path(name)
            disposition, reason = classify_path(safe_path)
            safe_entry = {**entry, "path_or_resource": str(safe_path)}
        elif kind.startswith("docker-"):
            resource_kind = kind.removeprefix("docker-")
            labels = entry.get("labels") if isinstance(entry.get("labels"), dict) else {}
            disposition, reason = classify_docker_resource(
                resource_kind, name, labels,
                protected_volume_names=protected_volume_names,
                protected_network_names=protected_network_names,
            )
            if disposition == DELETE:
                safe_name = _validated_docker_resource_name(
                    resource_kind, name, labels=labels,
                    protected_volume_names=protected_volume_names,
                    protected_network_names=protected_network_names,
                )
            else:
                safe_name = name
            safe_entry = {**entry, "path_or_resource": safe_name}
        else:
            raise ValueError(f"unknown cleanup entry type: {kind}")
        if disposition == PROTECTED:
            raise ValueError(f"protected cleanup target refused: {name}: {reason}")
        if disposition != DELETE:
            raise ValueError(f"cleanup target is not allowlisted: {name}: {reason}")
        approved.append(safe_entry)
    return approved


def _delete_entry(
    entry: dict[str, object], *,
    protected_volume_names: set[str] | None = None,
    protected_network_names: set[str] | None = None,
) -> None:
    kind = str(entry["type"])
    name = str(entry["path_or_resource"])
    if kind == "path":
        path = _validated_legacy_path(name)
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)  # NOSONAR -- strict destructive allowlist, no traversal/symlink ancestor
        elif path.is_dir():
            shutil.rmtree(path)  # NOSONAR -- strict destructive allowlist, no traversal/symlink ancestor
        return
    resource_kind = kind.removeprefix("docker-")
    if protected_volume_names is None or protected_network_names is None:
        protected = _production_compose_resource_names()
        protected_volume_names = protected["volume"]
        protected_network_names = protected["network"]
    current_labels = _current_docker_labels(resource_kind, name)
    if current_labels is None:
        return
    safe_name = _validated_docker_resource_name(
        resource_kind, name, labels=current_labels,
        protected_volume_names=protected_volume_names,
        protected_network_names=protected_network_names,
    )
    command = {
        "container": [DOCKER_BIN, "rm", "-f", "--", safe_name],
        "volume": [DOCKER_BIN, "volume", "rm", "--", safe_name],
        "network": [DOCKER_BIN, "network", "rm", "--", safe_name],
        "image": [DOCKER_BIN, "image", "rm", "--", safe_name],
    }.get(resource_kind)
    if command is None:
        raise ValueError(f"unsupported Docker cleanup type: {resource_kind}")
    result = subprocess.run(command, text=True, capture_output=True, check=False)  # NOSONAR -- validated fixed-form argv
    if result.returncode != 0 and "No such" not in (result.stderr + result.stdout):
        raise RuntimeError(f"Docker cleanup failed for {kind} {name}: {result.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete only certified, allowlisted legacy WaterfallHunter artifacts.")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--release-certificate", type=Path)
    parser.add_argument("--db-certificate", type=Path)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        inventory = _load(args.inventory)
        protected = _production_compose_resource_names()
        approved = _validated_delete_entries(
            inventory,
            protected_volume_names=protected["volume"],
            protected_network_names=protected["network"],
        )
        if not args.execute:
            print(json.dumps({"mode": "dry-run", "delete_count": len(approved), "targets": [e["path_or_resource"] for e in approved]}, indent=2))
            return 0
        if args.release_certificate is None or args.db_certificate is None:
            raise ValueError("release certificate and database certificate are required for --execute")
        release = validate_release_certificate(args.release_certificate)
        _revalidate_release_certificate_for_cleanup(release)
        db = validate_db_certificate(args.db_certificate)
        certified_backup = Path(str(db["backup_path"]))
        removed: list[dict[str, object]] = []
        for entry in approved:
            if entry.get("type") == "path" and _target_contains_certified_backup(
                _validated_legacy_path(str(entry.get("path_or_resource") or "")),
                certified_backup,
            ):
                raise ValueError(
                    f"cleanup target contains certified database backup: {entry.get('path_or_resource')}"
                )
            _assert_database_target_certificate_binding(entry, db)
            _delete_entry(
                entry,
                protected_volume_names=protected["volume"],
                protected_network_names=protected["network"],
            )
            removed.append({"type": entry["type"], "path_or_resource": entry["path_or_resource"]})
        certificate = {
            "certificate_type": "waterfallhunter_cleanup_v1",
            "status": "PASS",
            "completed_at": time.time(),
            "release_sha": release["release_sha"],
            "db_backup_sha256": db["sha256"],
            "inventory_sha256": hashlib.sha256(args.inventory.read_bytes()).hexdigest(),
            "removed": removed,
        }
        if args.certificate:
            certificate_output = _cleanup_certificate_output(args.certificate)
            CANONICAL_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            certificate_output.write_text(  # NOSONAR -- canonical runtime parent + validated basename
                json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(json.dumps({"status": "PASS", "removed": len(removed)}))
        return 0
    except Exception as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
